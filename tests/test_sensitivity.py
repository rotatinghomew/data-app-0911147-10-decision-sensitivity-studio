import json
import math
import unittest

import numpy as np

from app import sensitivity
from app.service import load_study


CASES = json.loads((__import__('pathlib').Path(__file__).resolve().parents[1]
                    / 'data/analysis-cases.json').read_text())


def case(name):
    return next(row for row in CASES if row['name'] == name)['study']


class OracleEstimatorTests(unittest.TestCase):
    """用已知答案的独立基准检查估计器，不依赖业务数据的结论方向。"""

    def test_independent_additive_variance_is_hand_checkable(self):
        rng = np.random.default_rng(11)
        n = 4000
        x1 = rng.normal(0, math.sqrt(1), n)
        x2 = rng.normal(0, math.sqrt(4), n)
        y = 2 * x1 + x2            # 方差贡献分别为 4 与 4
        result = sensitivity.shapley_from_samples(np.column_stack([x1, x2]), y, degree=1)
        shares = result['phi'][0] / result['variance'][0]
        self.assertAlmostEqual(result['variance'][0], 8.0, delta=0.6)
        self.assertAlmostEqual(shares[0], 0.5, delta=0.03)
        self.assertAlmostEqual(shares[1], 0.5, delta=0.03)
        self.assertAlmostEqual(result['phi'][0].sum() / result['variance'][0], 1.0, delta=1e-9)

    def test_pure_interaction_is_split_between_inputs(self):
        rng = np.random.default_rng(12)
        x1 = rng.normal(size=4000)
        x2 = rng.normal(size=4000)
        y = x1 * x2
        result = sensitivity.shapley_from_samples(np.column_stack([x1, x2]), y, degree=2)
        shares = result['phi'][0] / result['variance'][0]
        self.assertAlmostEqual(shares[0], 0.5, delta=0.05)
        self.assertAlmostEqual(shares[1], 0.5, delta=0.05)
        self.assertLess(result['values'][(0,)][0] / result['variance'][0], 0.1)

    def test_strong_dependence_is_not_shuffled_apart(self):
        rng = np.random.default_rng(13)
        x = rng.multivariate_normal([0, 0], [[1, 0.95], [0.95, 1]], 4000)
        y = x[:, 0] + x[:, 1]
        result = sensitivity.shapley_from_samples(x, y, degree=1)
        shares = result['phi'][0] / result['variance'][0]
        self.assertAlmostEqual(shares.sum(), 1.0, delta=1e-9)
        self.assertAlmostEqual(shares[0], 0.5, delta=0.05)
        self.assertAlmostEqual(shares[1], 0.5, delta=0.05)
        # 两列高度相关：单独解释比例之和远大于 1，说明共享信息被重复计算
        self.assertGreater(sum(result['values'][(i,)][0] for i in range(2)) / result['variance'][0], 1.5)

    def test_additive_oracle_case_metadata(self):
        oracle = case('additive_oracle')
        self.assertEqual(oracle['independent_variances'], [1, 4])
        self.assertEqual(oracle['coefficients'], [2, 1])

    def test_identical_joint_rows_leave_contributions_undefined(self):
        data = case('noise_only')
        result = sensitivity.analyze(data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 3})
        for target in result['targets']:
            self.assertTrue(target['degenerate'])
            self.assertIsNone(target['variance'])
            self.assertIsNone(target['sum_of_shares'])
            self.assertIsNone(target['contributions'][0]['share'])
            self.assertIsNone(target['contributions'][0]['rank'])

    def test_deterministic_case_has_no_internal_noise(self):
        data = case('deterministic')
        result = sensitivity.analyze(data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 3})
        self.assertEqual(result['internal_noise']['replay']['sample_standard_deviation'], 0.0)
        self.assertFalse(result['internal_noise']['monte_carlo_used_for_targets'])

    def test_zero_noise_does_not_change_parameter_importance(self):
        base = case('correlated_joint')
        deterministic = case('deterministic')
        first = sensitivity.analyze(base, {'scenario': 'reference', 'bootstrap': 20, 'seed': 5})
        second = sensitivity.analyze(deterministic, {'scenario': 'reference', 'bootstrap': 20, 'seed': 5})
        for first_target, second_target in zip(first['targets'], second['targets']):
            if first_target['kind'] not in ('budget', 'choice_margin', 'decision_regret'):
                continue
            for left, right in zip(first_target['contributions'], second_target['contributions']):
                self.assertAlmostEqual(left['share'], right['share'], places=3)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()

    def test_default_analysis_structure_and_numbers(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 25, 'seed': 12345})
        self.assertEqual(result['analysis_version'], sensitivity.ANALYSIS_VERSION)
        ids = [target['id'] for target in result['targets']]
        self.assertIn('overrun_probability:shared', ids)
        self.assertIn('choice_margin', ids)
        self.assertIn('decision_regret', ids)
        for target in result['targets']:
            self.assertFalse(target['degenerate'])
            shares = [item['share'] for item in target['contributions']]
            self.assertAlmostEqual(sum(shares), 1.0, places=4)
            self.assertLessEqual(abs(target['efficiency_error']), 1e-6)
            for item in target['contributions']:
                self.assertIsNotNone(item['share_interval'])
                self.assertIsNotNone(item['rank_interval'])
                self.assertEqual(item['rank_interval'][0] <= item['rank'] <= item['rank_interval'][1], True)
        self.assertEqual(result['diagnostics']['bootstrap']['completed'], 25)

    def test_joint_dependence_is_preserved_and_shuffling_would_differ(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        design = sensitivity._design(self.data, sensitivity._scenario(self.data, 'reference'))
        self.assertGreater(result['diagnostics']['correlations']['participants,hours'], 0.8)
        self.assertEqual(design['x'].shape[0], 200)
        # 相关系数在平移后保持不变
        correlations = np.corrcoef(design['x'].T)
        original = np.corrcoef(design['raw'].T)
        np.testing.assert_allclose(correlations, original, atol=0.02)

    def test_bounds_only_is_range_analysis_without_distribution(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        overhead = next(item for item in result['bounds_only'] if item['input'] == 'overhead')
        self.assertIsNone(overhead['distribution'])
        self.assertEqual([case['kind'] for case in overhead['cases']], ['lower', 'scenario_value', 'upper'])
        self.assertEqual(overhead['bounds'], [1500.0, 3500.0])
        # 端点取值不同，预算也随之改变，但没有任何概率解释
        budgets = [case['budget']['shared'] for case in overhead['cases']]
        self.assertEqual(len(set(budgets)), 3)
        for target in result['targets']:
            self.assertNotIn('overhead', [item['input'] for item in target['contributions']])

    def test_measurement_cost_drives_recommendation(self):
        result = sensitivity.analyze(self.data, {
            'scenario': 'reference', 'bootstrap': 20, 'seed': 7,
            'measurement_costs': {'participants': 900.0, 'hours': 100.0, 'hourly_cost': 50.0}})
        recommendation = result['information']['recommendation']
        self.assertEqual(recommendation['basis'], 'net_value')
        self.assertIsNotNone(recommendation['net_value'])
        by_inputs = {tuple(item['inputs']): item for item in result['information']['sets']}
        self.assertIsNotNone(by_inputs[('hours',)]['net_value'])
        for entry in result['information']['sets']:
            if entry['measurement_cost'] is not None:
                self.assertAlmostEqual(entry['net_value'], entry['expected_value'] - entry['measurement_cost'],
                                       places=2)

    def test_without_costs_recommendation_is_not_a_budget_claim(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        recommendation = result['information']['recommendation']
        self.assertEqual(recommendation['basis'], 'single_input_perfect_information_value')
        self.assertIsNone(recommendation['net_value'])
        self.assertEqual(len(recommendation['inputs']), 1)
        self.assertEqual(result['information']['cost_coverage'], 'absent')

    def test_nonpositive_net_value_does_not_pick_an_input(self):
        result = sensitivity.analyze(self.data, {
            'scenario': 'reference', 'bootstrap': 20, 'seed': 7,
            'measurement_costs': {'participants': 100000.0}})
        recommendation = result['information']['recommendation']
        self.assertEqual(recommendation['basis'], 'net_value')
        self.assertEqual(recommendation['inputs'], [])
        self.assertLessEqual(recommendation['net_value'], 0)
        self.assertEqual(result['information']['cost_coverage'], 'partial')

    def test_value_of_information_bounded_by_all_inputs(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        entries = {tuple(item['inputs']): item for item in result['information']['sets']}
        singles = [value['expected_value'] for key, value in entries.items() if len(key) == 1]
        full = entries[tuple(sensitivity.INPUTS)]['expected_value']
        self.assertGreaterEqual(full + 1e-6, max(singles) - 1e-6)
        self.assertGreater(full, 0.0)

    def test_one_at_a_time_is_labelled_as_a_counterexample(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        oat = result['one_at_a_time']
        self.assertEqual(oat['fraction'], 0.10)
        self.assertEqual(len(oat['rows']), 6)
        self.assertIn('不能替代全局贡献', oat['note'])
        # 乘性模型里三个输入的 ±10% 对预算的影响相同，这正是逐参数扰动看不出结构的地方
        effects = {item['input']: item['maximum_absolute_budget_change'] for item in oat['ranking']}
        self.assertAlmostEqual(effects['participants'], effects['hours'], places=6)

    def test_invalid_configuration_is_rejected(self):
        for config in ({'scenario': 'missing'},
                       {'scenario': 'reference', 'bootstrap': 1},
                       {'scenario': 'reference', 'bootstrap': 9999},
                       {'scenario': 'reference', 'degree': 9},
                       {'scenario': 'reference', 'bootstrap': 20, 'confidence': 1.5},
                       {'scenario': 'reference', 'bootstrap': 20, 'measurement_costs': {'unknown': 1}},
                       {'scenario': 'reference', 'bootstrap': 20, 'measurement_costs': {'hours': -1}}):
            with self.assertRaises(ValueError):
                sensitivity.analyze(self.data, config)

    def test_scenario_anchor_sets_expectation(self):
        reference = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        busy = sensitivity.analyze(self.data, {'scenario': 'busy', 'bootstrap': 20, 'seed': 7})
        design = sensitivity._design(self.data, sensitivity._scenario(self.data, 'reference'))
        np.testing.assert_allclose(design['x'].mean(axis=0), [90.0, 7.0, 40.0], atol=0.5)
        self.assertGreater(busy['decision']['margin_mean'], reference['decision']['margin_mean'])

    def test_capacity_exclusions_are_reported(self):
        result = sensitivity.analyze(self.data, {'scenario': 'busy', 'bootstrap': 20, 'seed': 7})
        capacity = result['configuration']['capacity']
        self.assertGreater(capacity['excluded_rows'], 0)
        self.assertLess(capacity['retained_fraction'], 1.0)
        self.assertTrue(any('人数上限' in warning for warning in result['diagnostics']['warnings']))

    def test_internal_noise_replay_scale(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        replay = result['internal_noise']['replay']
        self.assertAlmostEqual(replay['sample_standard_deviation'], 400.0, delta=80.0)
        self.assertEqual(replay['replicates'], 50)
        for target in result['targets']:
            if target['kind'] == 'budget' and target['option'] is not None:
                self.assertLess(target['internal_noise_share_of_realized_variance'], 0.01)

    def test_fit_evidence_and_assumptions_present(self):
        result = sensitivity.analyze(self.data, {'scenario': 'reference', 'bootstrap': 20, 'seed': 7})
        assumption_ids = {item['id'] for item in result['diagnostics']['assumptions']}
        for required in ('joint_dependence', 'bounds_only', 'internal_noise',
                         'conditional_model', 'shapley_convention', 'interval_scope'):
            self.assertIn(required, assumption_ids)
        for target in result['targets']:
            self.assertIsNotNone(target['fit']['out_of_sample_r2'])
            self.assertIsNotNone(target['composite_driver']['out_of_sample_r2'])


if __name__ == '__main__':
    unittest.main()
