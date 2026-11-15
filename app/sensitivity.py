"""联合不确定输入下的全局敏感性、内在噪声分离与补测价值。

本模块只做统计计算，不依赖 HTTP。结论严格按 data 中的 input_contract 解释：
stochastic_joint 用联合观测，bounds_only 只有范围、不赋予概率。

估计量
------
对目标量 T(X) 使用 Shapley 效应分解方差：

    phi_i = sum_{S ⊆ N\\{i}} |S|!(p-|S|-1)!/p! · (v(S∪{i}) − v(S))
    v(S)  = Var(E[T | X_S])

v(S) 由“交叉拟合多项式岭回归”估计条件均值，按行使用联合观测，因此保留
输入之间允许的相关，不做逐列独立打乱。相关输入下 Shapley 份额是一种公平
分摊共同解释方差的约定，不是唯一真理；模块同时给出单独解释比例、排他比例
和两两联合解释比例，便于检查“分不开”的部分。

内部冲击与输入独立，超支概率与期望超支额用正态积分解析计算，不使用
蒙特卡洛，因此模拟噪声不会冒充参数重要性。
"""

from __future__ import annotations

import itertools
import math
import random
import time

import numpy as np
from scipy.special import ndtr

INPUTS = ('participants', 'hours', 'hourly_cost')
ANALYSIS_VERSION = 1
DEFAULT_BOOTSTRAP = 200
DEFAULT_CONFIDENCE = 0.95
DEFAULT_DEGREE = 2
DEFAULT_SEED = 20261115
MIN_BOOTSTRAP = 20
MAX_BOOTSTRAP = 600
FOLDS = 5
RIDGE = 1e-6
DEGENERATE_VARIANCE = 1e-12
MIN_ROWS = 30
OAT_FRACTION = 0.10


# ---------------------------------------------------------------- 数值小工具

def _number(value, digits=None):
    """把 numpy/float 转成有限 JSON 数值；未定义返回 None。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return round(result, digits) if digits is not None else result


def _share(phi, variance):
    if variance is None or float(variance) <= DEGENERATE_VARIANCE:
        return None
    return float(phi) / float(variance)


def _interval(samples, confidence, digits=6):
    values = [value for value in samples if value is not None]
    if not values:
        return None
    tail = (1.0 - confidence) / 2.0
    low, high = np.quantile(np.asarray(values, dtype=float), [tail, 1.0 - tail])
    return [_number(low, digits), _number(high, digits)]


def _ranks(shares):
    """份额排名，1 为最大；并列取平均名次。"""
    if shares is None or any(value is None for value in shares):
        return None
    order = sorted(range(len(shares)), key=lambda index: (-shares[index], index))
    ranks = [0.0] * len(shares)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and shares[order[end + 1]] == shares[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for cursor in range(position, end + 1):
            ranks[order[cursor]] = average
        position = end + 1
    return ranks


# ---------------------------------------------------------------- 回归与分解

def _poly_basis(z, degree):
    """标准化输入上的全多项式基（含截距），按总次数 1..degree 展开。"""
    n, p = z.shape
    columns = [np.ones(n)]
    for total in range(1, degree + 1):
        for combo in itertools.combinations_with_replacement(range(p), total):
            columns.append(np.prod(z[:, combo], axis=1))
    return np.column_stack(columns)


def _ridge_solve(basis, target, ridge):
    gram = basis.T @ basis
    size = gram.shape[0]
    penalty = np.full(size, ridge)
    penalty[0] = 0.0
    gram[np.diag_indices(size)] += penalty
    return np.linalg.solve(gram, basis.T @ target)


def _fit_predict(train_x, train_y, test_x, degree, ridge):
    """在训练行上拟合 E[Y|X_S]，返回测试行的条件均值预测。"""
    if train_x.shape[1] == 0:
        return np.tile(train_y.mean(axis=0), (test_x.shape[0], 1))
    center = train_x.mean(axis=0)
    scale = train_x.std(axis=0, ddof=1)
    scale = np.where(scale < 1e-15, 1.0, scale)
    basis_train = _poly_basis((train_x - center) / scale, degree)
    basis_test = _poly_basis((test_x - center) / scale, degree)
    y_center = train_y.mean(axis=0)
    y_scale = train_y.std(axis=0, ddof=1)
    y_scale = np.where(y_scale < 1e-15, 1.0, y_scale)
    coefficient = _ridge_solve(basis_train, (train_y - y_center) / y_scale, ridge)
    return (basis_test @ coefficient) * y_scale + y_center


def _subset_seed(seed, subset):
    code = 0
    for position, index in enumerate(subset):
        code += (index + 1) * (131 ** position)
    return (int(seed) * 1000003 + code * 7919) % (2 ** 63 - 1)


def _fold_chunks(size, folds, seed):
    rng = np.random.default_rng(seed)
    return np.array_split(rng.permutation(size), min(folds, size))


def oof_predict(x, y, subset, degree, folds=FOLDS, seed=DEFAULT_SEED):
    """K 折交叉拟合的条件均值预测；每条预测都来自没见过该行的模型。"""
    n = y.shape[0]
    subset = list(subset)
    if len(subset) == 0:
        return np.tile(y.mean(axis=0), (n, 1))
    if len(subset) == x.shape[1]:
        return y.copy()
    prediction = np.empty_like(y, dtype=float)
    chunks = _fold_chunks(n, folds, _subset_seed(seed, subset))
    for index, test in enumerate(chunks):
        train = np.concatenate([chunk for other, chunk in enumerate(chunks) if other != index])
        prediction[test] = _fit_predict(x[train][:, subset], y[train],
                                        x[test][:, subset], degree, RIDGE)
    return prediction


def shapley_from_samples(x, y, degree=DEFAULT_DEGREE, folds=FOLDS, seed=DEFAULT_SEED):
    """底层入口：由输入矩阵 x 与目标矩阵 y 估计 Shapley 方差贡献。

    便于独立基准与单元测试使用；返回未四舍五入的 numpy 数值。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if y.ndim == 1:
        y = y[:, None]
    if x.ndim != 2 or y.shape[0] != x.shape[0]:
        raise ValueError('输入矩阵与目标矩阵行数应一致')
    p = x.shape[1]
    total = np.var(y, axis=0, ddof=1)
    values = {}
    r2 = {}
    for size in range(p + 1):
        for subset in itertools.combinations(range(p), size):
            if size == 0:
                values[subset] = np.zeros(y.shape[1])
            elif size == p:
                values[subset] = total.copy()
            else:
                prediction = oof_predict(x, y, subset, degree, folds, seed)
                values[subset] = np.var(prediction, axis=0, ddof=1)
                safe = np.where(total > DEGENERATE_VARIANCE, total, np.nan)
                r2[subset] = 1.0 - np.mean((prediction - y) ** 2, axis=0) / safe
    phi = np.zeros((y.shape[1], p))
    for index in range(p):
        others = [j for j in range(p) if j != index]
        for size in range(p):
            for subset in itertools.combinations(others, size):
                weight = (math.factorial(size) * math.factorial(p - size - 1)
                          / math.factorial(p))
                with_index = tuple(sorted(subset + (index,)))
                phi[:, index] += weight * (values[with_index] - values[subset])
    return {'values': values, 'variance': total, 'phi': phi, 'r2': r2}


# ---------------------------------------------------------------- 业务目标

def overrun_terms(cost, ceiling, sigma):
    """总成本 cost + N(0, sigma^2) 的超支概率与期望超支额（解析）。"""
    cost = np.asarray(cost, dtype=float)
    if sigma > 0:
        z = (ceiling - cost) / sigma
        probability = 1.0 - ndtr(z)
        expected = sigma * np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi) + (cost - ceiling) * probability
        return probability, expected
    return (cost > ceiling).astype(float), np.maximum(cost - ceiling, 0.0)


def option_costs(x, option, overhead):
    return x[:, 0] * x[:, 1] * x[:, 2] * option['labor_multiplier'] + option['fixed_cost'] + overhead


def _target_matrix(x, options, overhead, ceiling, sigma):
    """构造敏感性目标矩阵，并返回每个目标列的说明。"""
    columns = []
    meta = []
    for name, option in options:
        cost = option_costs(x, option, overhead)
        probability, expected = overrun_terms(cost, ceiling, sigma)
        columns.extend([cost, probability, expected])
        meta.extend([
            {'id': 'budget:' + name, 'kind': 'budget', 'option': name,
             'label': '预算额：' + name + '（不含内部冲击）'},
            {'id': 'overrun_probability:' + name, 'kind': 'overrun_probability',
             'option': name, 'label': '超支概率：' + name},
            {'id': 'expected_overrun:' + name, 'kind': 'expected_overrun',
             'option': name, 'label': '期望超支额：' + name},
        ])
    first, second = options[0][0], options[1][0]
    total_first = columns[0] + columns[2]
    total_second = columns[3] + columns[5]
    margin = total_first - total_second
    columns.append(margin)
    meta.append({'id': 'choice_margin', 'kind': 'choice_margin', 'option': None,
                 'label': '方案总成本差：' + first + ' − ' + second})
    baseline_shared = bool(total_first.mean() <= total_second.mean())
    regret = np.maximum(margin, 0.0) if baseline_shared else np.maximum(-margin, 0.0)
    columns.append(regret)
    meta.append({'id': 'decision_regret', 'kind': 'decision_regret', 'option': None,
                 'label': '决策后悔额（相对事后最优方案）'})
    return np.column_stack(columns), meta, {'first': first, 'second': second,
                                            'margin': margin, 'baseline_shared': baseline_shared}


# ---------------------------------------------------------------- 输入设计

def _scenario(data, scenario_id):
    for row in data['scenarios']:
        if row['id'] == scenario_id:
            return row
    raise ValueError('未知预算情景')


def _design(data, scenario):
    """把联合观测按比例平移到情景中心，保留相关结构与变异系数。"""
    samples = data['input_contract'].get('joint_samples')
    if not isinstance(samples, list) or len(samples) < MIN_ROWS:
        raise ValueError('联合观测不足 %d 行，无法估计敏感性' % MIN_ROWS)
    try:
        raw = np.array([[float(row[name]) for name in INPUTS] for row in samples], dtype=float)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('联合观测字段不完整') from error
    if not np.isfinite(raw).all() or (raw <= 0).any():
        raise ValueError('联合观测必须为有限正数')
    means = raw.mean(axis=0)
    anchors = np.array([float(scenario['inputs'][name]) for name in INPUTS], dtype=float)
    if not np.isfinite(anchors).all() or (anchors <= 0).any():
        raise ValueError('情景输入必须为有限正数')
    scale = anchors / means
    scaled = raw * scale
    capacity = float(data['capacity_limit'])
    feasible = scaled[:, 0] <= capacity
    raw_scale = raw.std(axis=0, ddof=1)
    varying = raw_scale > np.maximum(np.abs(means), 1.0) * 1e-9
    with np.errstate(invalid='ignore', divide='ignore'):
        correlations = np.corrcoef(raw[:, varying].T) if varying.sum() >= 2 else np.zeros((0, 0))
    return {
        'raw': raw,
        'x': scaled[feasible],
        'scale': scale,
        'sample_mean': means,
        'anchor': anchors,
        'rows_total': int(len(raw)),
        'rows_excluded': int((~feasible).sum()),
        'retained_fraction': float(feasible.mean()),
        'correlations': correlations,
        'correlation_inputs': [INPUTS[index] for index in np.flatnonzero(varying)],
    }


def _bootstrap_rows(n, count, seed):
    rng = np.random.default_rng(int(seed) + 104729)
    for _ in range(count):
        yield rng.integers(0, n, n)


# ---------------------------------------------------------------- 补测价值

def policy_value(x, delta, subset, degree=DEFAULT_DEGREE, folds=FOLDS, seed=DEFAULT_SEED):
    """补测 X_S 后按条件期望做方案选择，相对当前固定方案的成本节省。

    delta 为“第一个方案减第二个方案”的总成本差（含期望超支）。
    值为完美信息上界；同时返回条件决策翻转概率。
    """
    n = delta.shape[0]
    prediction = oof_predict(x, delta[:, None], subset, degree, folds, seed)[:, 0]
    baseline_shared = bool(delta.mean() <= 0.0)
    choose_shared = prediction <= 0.0
    policy_cost = np.where(choose_shared, delta, 0.0)
    baseline_cost = min(float(delta.mean()), 0.0)
    value = baseline_cost - float(policy_cost.mean())
    flip = float(np.mean(choose_shared != baseline_shared))
    return value, flip


def information_sets(p):
    """单输入、两两组合与全部输入；用于比较补测哪些信息。"""
    sets = [tuple([index]) for index in range(p)]
    sets.extend(itertools.combinations(range(p), 2))
    if p > 2:
        sets.append(tuple(range(p)))
    return sets


# ---------------------------------------------------------------- 一次一个 ±10%

def one_at_a_time(data, scenario, option_names):
    """现行做法：把每个输入逐个上下调 10%，其余保持情景值。

    仅作对照：它忽略输入之间的联合关系，不能与全局份额直接比较。
    """
    base = {name: float(scenario['inputs'][name]) for name in INPUTS + ('overhead',)}
    base['overhead'] = float(scenario['inputs'].get('overhead', 0.0))
    ceiling = float(data['budget_ceiling'])
    sigma = float(data['internal_shock_sd'])

    def evaluate(values):
        row = np.array([[values['participants'], values['hours'], values['hourly_cost']]])
        budgets = {name: float(option_costs(row, data['budget_options'][name],
                                            values['overhead'])[0]) for name in option_names}
        totals = {}
        for name, cost in budgets.items():
            probability, expected = overrun_terms(np.array([cost]), ceiling, sigma)
            totals[name] = cost + float(expected[0])
        margin = totals[option_names[0]] - totals[option_names[1]]
        return {'budget': budgets, 'total': totals, 'margin': margin}

    baseline = evaluate(base)
    rows = []
    for name in INPUTS:
        for direction in (-1, 1):
            values = dict(base)
            values[name] = base[name] * (1.0 + direction * OAT_FRACTION)
            result = evaluate(values)
            rows.append({
                'input': name,
                'direction': direction,
                'change_fraction': direction * OAT_FRACTION,
                'value': _number(values[name], 6),
                'budget_change': {key: _number(result['budget'][key] - baseline['budget'][key], 2)
                                  for key in option_names},
                'margin_change': _number(result['margin'] - baseline['margin'], 2),
                'decision_flips': (result['margin'] < 0) != (baseline['margin'] < 0),
            })
    ranking = []
    for name in INPUTS:
        effect = max(abs(row['budget_change'][option_names[0]])
                     for row in rows if row['input'] == name)
        ranking.append({'input': name, 'maximum_absolute_budget_change': _number(effect, 2)})
    ranking.sort(key=lambda item: -item['maximum_absolute_budget_change'])
    return {
        'fraction': OAT_FRACTION,
        'baseline': {'budget': {key: _number(value, 2) for key, value in baseline['budget'].items()},
                     'expected_total_cost': {key: _number(value, 2) for key, value in baseline['total'].items()},
                     'margin_first_minus_second': _number(baseline['margin'], 2)},
        'rows': rows,
        'ranking': ranking,
        'note': ('仅按情景值逐个扰动，忽略联合分布与相关；排序只是现行做法的对照，'
                 '不能替代全局贡献，也不能据此声称某种概率'),
    }


# ---------------------------------------------------------------- 接口元数据

def metadata(data):
    """页面初始化所需的分析选项，不运行估计。"""
    options = list(data['budget_options'])
    bounds = data['input_contract'].get('bounds_only') or {}
    samples = data['input_contract'].get('joint_samples') or []
    return {
        'analysis_version': ANALYSIS_VERSION,
        'scenarios': [row['id'] for row in data['scenarios']],
        'options': options,
        'inputs': list(INPUTS),
        'bounds_only': {name: value for name, value in bounds.items()},
        'joint_sample_count': len(samples),
        'defaults': {
            'scenario': data['scenarios'][0]['id'] if data['scenarios'] else None,
            'bootstrap': DEFAULT_BOOTSTRAP,
            'confidence': DEFAULT_CONFIDENCE,
            'degree': DEFAULT_DEGREE,
            'seed': DEFAULT_SEED,
            'measurement_costs': {},
        },
        'limits': {
            'bootstrap': [MIN_BOOTSTRAP, MAX_BOOTSTRAP],
            'degree': [1, 3],
            'confidence': [0.5, 0.99],
        },
        'estimator': 'shapley_effects_via_cross_fitted_polynomial_ridge',
        'notice': ('全局敏感性使用联合观测并保留输入相关；overhead 只有范围，'
                   '只做范围分析，不假设分布'),
    }


# ---------------------------------------------------------------- 主分析

def analyze(data, config):
    """执行一次完整的全局敏感性分析；返回可 JSON 序列化的结果。"""
    started = time.monotonic()
    if not isinstance(config, dict):
        raise ValueError('分析配置应为对象')
    scenario = _scenario(data, config.get('scenario'))
    options = list(data['budget_options'].items())
    if len(options) != 2:
        raise ValueError('本分析要求恰好两个预算方案，当前为 %d 个' % len(options))

    bootstrap = config.get('bootstrap', DEFAULT_BOOTSTRAP)
    if isinstance(bootstrap, bool) or not isinstance(bootstrap, int) or not (MIN_BOOTSTRAP <= bootstrap <= MAX_BOOTSTRAP):
        raise ValueError('bootstrap 次数应为 %d 至 %d 的整数' % (MIN_BOOTSTRAP, MAX_BOOTSTRAP))
    confidence = config.get('confidence', DEFAULT_CONFIDENCE)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.5 <= confidence < 1.0):
        raise ValueError('confidence 应在 0.5 与 1 之间')
    degree = config.get('degree', DEFAULT_DEGREE)
    if isinstance(degree, bool) or not isinstance(degree, int) or not (1 <= degree <= 3):
        raise ValueError('degree 应为 1 至 3 的整数')
    seed = config.get('seed', DEFAULT_SEED)
    if isinstance(seed, bool) or not isinstance(seed, int) or not (0 <= seed < 2 ** 31):
        raise ValueError('seed 应为非负整数')
    measurement_costs = config.get('measurement_costs') or {}
    if not isinstance(measurement_costs, dict):
        raise ValueError('measurement_costs 应为对象')
    for name, value in measurement_costs.items():
        if name not in INPUTS:
            raise ValueError('未知测量成本输入：' + str(name))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('测量成本必须为有限非负数')

    design = _design(data, scenario)
    x = design['x']
    if x.shape[0] < MIN_ROWS:
        raise ValueError('可行联合观测不足 %d 行，无法估计敏感性' % MIN_ROWS)
    sigma = float(data['internal_shock_sd'])
    ceiling = float(data['budget_ceiling'])
    if not math.isfinite(sigma) or sigma < 0:
        raise ValueError('内部冲击标准差必须为有限非负数')
    if not math.isfinite(ceiling):
        raise ValueError('预算上限必须为有限数')
    overhead = float(scenario['inputs']['overhead'])
    p = len(INPUTS)

    y, meta, decision = _target_matrix(x, options, overhead, ceiling, sigma)
    delta = decision['margin']

    estimate = shapley_from_samples(x, y, degree, FOLDS, seed)
    values, total_variance, phi, r2_values = (
        estimate['values'], estimate['variance'], estimate['phi'], estimate['r2'])

    # ---- 多项式阶数稳健性：份额差异与排序是否改变
    alternatives = {}
    for other in (1, 3):
        if other != degree:
            alternatives[other] = shapley_from_samples(x, y, other, FOLDS, seed)
    degree_check = None
    if alternatives:
        maximum = 0.0
        rank_stable = True
        per_target = []
        for column, item in enumerate(meta):
            variance = total_variance[column]
            if variance <= DEGENERATE_VARIANCE:
                continue
            main_shares = [_share(phi[column, index], variance) for index in range(p)]
            main_order = _ranks(main_shares)
            biggest = 0.0
            for other, result in alternatives.items():
                other_shares = [_share(result['phi'][column, index], variance) for index in range(p)]
                for index in range(p):
                    biggest = max(biggest, abs(main_shares[index] - other_shares[index]))
                if _ranks(other_shares) != main_order:
                    rank_stable = False
            maximum = max(maximum, biggest)
            per_target.append({'id': item['id'],
                               'max_absolute_share_difference': _number(biggest, 6)})
        degree_check = {
            'degrees': sorted(alternatives),
            'max_absolute_share_difference': _number(maximum, 6),
            'ranking_stable_across_degrees': rank_stable,
            'per_target': per_target,
        }

    # ---- 复合驱动：三个输入在模型中只通过乘积出现
    product = x[:, 0] * x[:, 1] * x[:, 2]
    product_prediction = oof_predict(product[:, None], y, (0,), degree, FOLDS, seed)
    product_fit = []
    for column in range(y.shape[1]):
        variance = total_variance[column]
        if variance <= DEGENERATE_VARIANCE:
            product_fit.append(None)
        else:
            residual = float(np.mean((product_prediction[:, column] - y[:, column]) ** 2))
            product_fit.append(_number(1.0 - residual / variance, 6))

    # ---- 自助法：份额、排名、补测价值
    sets = information_sets(p)
    bootstrap_shares = [[] for _ in meta]
    bootstrap_ranks = [[] for _ in meta]
    bootstrap_value = {subset: [] for subset in sets}
    bootstrap_flip = {subset: [] for subset in sets}
    failures = []
    completed = 0
    for replicate, indices in enumerate(_bootstrap_rows(x.shape[0], bootstrap, seed)):
        try:
            xb = x[indices]
            yb, _, decision_b = _target_matrix(xb, options, overhead, ceiling, sigma)
            result = shapley_from_samples(xb, yb, degree, FOLDS, seed)
            for column in range(len(meta)):
                shares = [_share(result['phi'][column, index], result['variance'][column])
                          for index in range(p)]
                bootstrap_shares[column].append(shares)
                bootstrap_ranks[column].append(_ranks(shares))
            for subset in sets:
                value, flip = policy_value(xb, decision_b['margin'], subset, degree, FOLDS, seed)
                bootstrap_value[subset].append(value)
                bootstrap_flip[subset].append(flip)
            completed += 1
        except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
            if len(failures) < 5:
                failures.append({'replicate': replicate, 'error': str(error)})

    # ---- 组织目标结果
    targets = []
    for column, item in enumerate(meta):
        variance = total_variance[column]
        degenerate = bool(variance <= DEGENERATE_VARIANCE)
        rank_interval_tail = (1.0 - confidence) / 2.0
        contributions = []
        shares = [_share(phi[column, index], variance) for index in range(p)]
        ranks = _ranks(shares)
        for index, name in enumerate(INPUTS):
            without = tuple(j for j in range(p) if j != index)
            share = shares[index]
            rank_interval = None
            if not degenerate:
                series = [row[index] for row in bootstrap_ranks[column] if row is not None]
                if series:
                    rank_interval = [_number(np.quantile(series, rank_interval_tail), 2),
                                     _number(np.quantile(series, 1.0 - rank_interval_tail), 2)]
            contributions.append({
                'input': name,
                'shapley_variance': None if degenerate else _number(phi[column, index]),
                'share': None if share is None else _number(share, 6),
                'share_interval': _interval([row[index] for row in bootstrap_shares[column]], confidence),
                'rank': None if degenerate else ranks[index],
                'rank_interval': rank_interval,
                'explained_by_alone': None if degenerate else _number(
                    values[(index,)][column] / variance, 6),
                'exclusive_share': None if degenerate else _number(
                    (variance - values[without][column]) / variance, 6),
            })
        if degenerate:
            for contribution in contributions:
                contribution['rank'] = None
        interactions = []
        if not degenerate:
            for first, second in itertools.combinations(range(p), 2):
                both = values[(first, second)][column]
                single = values[(first,)][column] + values[(second,)][column]
                interactions.append({
                    'inputs': [INPUTS[first], INPUTS[second]],
                    'joint_explained': _number(both / variance, 6),
                    'overlap_deduction': _number((both - single) / variance, 6),
                })
        groups = {}
        if not degenerate:
            for size in range(1, p):
                for subset in itertools.combinations(range(p), size):
                    groups['+'.join(INPUTS[index] for index in subset)] = _number(
                        values[subset][column] / variance, 6)

        noise_share = None
        if item['kind'] == 'budget' and sigma > 0 and not degenerate:
            noise_share = _number(sigma ** 2 / (variance + sigma ** 2), 6)
        fit = {}
        for subset, scores in r2_values.items():
            label = '{' + ','.join(INPUTS[index] for index in subset) + '}'
            fit[label] = None if degenerate else _number(scores[column], 6)
        usable = [value for value in fit.values() if value is not None]
        targets.append({
            'id': item['id'],
            'kind': item['kind'],
            'label': item['label'],
            'option': item['option'],
            'units': 'probability' if item['kind'] == 'overrun_probability' else 'currency',
            'mean': _number(y[:, column].mean()),
            'variance': None if degenerate else _number(variance),
            'standard_deviation': None if degenerate else _number(math.sqrt(variance)),
            'degenerate': degenerate,
            'degenerate_reason': ('联合输入在该目标上没有波动，方差为 0；贡献份额未定义'
                                  if degenerate else None),
            'contributions': contributions,
            'sum_of_shares': None if degenerate else _number(
                sum(value for value in shares if value is not None), 6),
            'efficiency_error': None if degenerate else _number(
                sum(phi[column]) / variance - 1.0, 6),
            'interactions': interactions,
            'group_explained': groups,
            'fit': {
                'method': 'polynomial_ridge_cross_fitted',
                'degree': degree,
                'folds': FOLDS,
                'out_of_sample_r2': fit,
                'minimum_r2': _number(min(usable), 6) if usable else None,
            },
            'composite_driver': {
                'name': 'participants×hours×hourly_cost',
                'out_of_sample_r2': product_fit[column],
                'note': ('该值接近 1 说明模型主要经由三个输入的乘积起作用；'
                         '三者在此目标上分不开，单独份额只是分摊约定'),
            },
            'internal_noise_share_of_realized_variance': noise_share,
        })

    # ---- 内部噪声证据：同一组参数改随机种子只产生模拟波动
    replay_seeds = list(range(1, 51))
    anchor_row = np.array([[scenario['inputs'][name] for name in INPUTS]])
    baseline_cost = float(option_costs(anchor_row, options[0][1], overhead)[0])
    replay_totals = [baseline_cost + random.Random(value).gauss(0, sigma) for value in replay_seeds]
    replay_sd = float(np.std(replay_totals, ddof=1)) if sigma > 0 else 0.0

    # ---- 仅有范围、没有概率的输入
    bounds_report = []
    for name, bounds in (data['input_contract'].get('bounds_only') or {}).items():
        if name != 'overhead' or not isinstance(bounds, list) or len(bounds) != 2:
            bounds_report.append({'input': name, 'bounds': bounds, 'distribution': None,
                                  'note': '该范围输入没有进入本次模型计算，因此不纳入分析',
                                  'cases': [], 'decision_stable_over_range': None,
                                  'preferred_options_over_range': []})
            continue
        low, high = float(bounds[0]), float(bounds[1])
        cases = []
        for value, kind in ((low, 'lower'), (overhead, 'scenario_value'), (high, 'upper')):
            adjusted = np.array([[scenario['inputs'][key] for key in INPUTS]])
            budgets = {option_name: float(option_costs(adjusted, option, value)[0])
                       for option_name, option in options}
            probabilities = {}
            expected = {}
            totals = {}
            for option_name, cost in budgets.items():
                probability, expectation = overrun_terms(np.array([cost]), ceiling, sigma)
                probabilities[option_name] = _number(probability[0], 6)
                expected[option_name] = _number(expectation[0], 6)
                totals[option_name] = cost + float(expectation[0])
            order = sorted(totals, key=totals.get)
            cases.append({
                'value': _number(value, 6),
                'kind': kind,
                'budget': {key: _number(value_, 2) for key, value_ in budgets.items()},
                'overrun_probability': probabilities,
                'expected_overrun': expected,
                'expected_total_cost': {key: _number(value_, 2) for key, value_ in totals.items()},
                'preferred_option': order[0],
                'margin_first_minus_second': _number(totals[options[0][0]] - totals[options[1][0]], 2),
            })
        preferences = [case['preferred_option'] for case in cases]
        bounds_report.append({
            'input': name,
            'bounds': [low, high],
            'distribution': None,
            'note': ('只有范围、没有概率分布：以下为端点与情景值的范围分析，'
                     '不假设均匀或其他分布，也不进入 Shapley 分解'),
            'cases': cases,
            'decision_stable_over_range': len(set(preferences)) == 1,
            'preferred_options_over_range': preferences,
        })

    # ---- 补测价值
    baseline_margin = float(delta.mean())
    baseline_option = options[0][0] if baseline_margin <= 0 else options[1][0]
    entries = []
    for subset in sorted(sets, key=lambda item: (len(item), item)):
        value, flip = policy_value(x, delta, subset, degree, FOLDS, seed)
        names = [INPUTS[index] for index in subset]
        cost = None
        if all(name in measurement_costs for name in names):
            cost = float(sum(measurement_costs[name] for name in names))
        entries.append({
            'inputs': names,
            'expected_value': _number(value, 2),
            'value_interval': _interval(bootstrap_value[subset], confidence, 2),
            'flip_probability': _number(flip, 6),
            'flip_interval': _interval(bootstrap_flip[subset], confidence, 6),
            'measurement_cost': _number(cost, 2),
            'net_value': _number(value - cost, 2) if cost is not None else None,
            'note': '完美信息上界：假定补测能准确给出该组输入；真实测量误差会使价值更低',
        })
    singles = [entry for entry in entries if len(entry['inputs']) == 1]
    joint = [entry for entry in entries if len(entry['inputs']) == len(INPUTS)]
    fully_costed = [entry for entry in entries if entry['net_value'] is not None]
    if fully_costed:
        ranked = sorted(fully_costed, key=lambda entry: (-entry['net_value'], len(entry['inputs'])))
        best = ranked[0]
        complete = len(fully_costed) == len(entries)
        if best['net_value'] <= 0:
            recommendation = {
                'basis': 'net_value',
                'inputs': [],
                'net_value': best['net_value'],
                'note': ('按已给出的测量成本，任何被评估的补测组合净价值都不为正；'
                         '在这些成本下补测不能仅凭决策价值获得理由'
                         + ('；部分组合缺少成本，未参与比较' if not complete else '')),
            }
        else:
            recommendation = {
                'basis': 'net_value',
                'inputs': best['inputs'],
                'net_value': best['net_value'],
                'note': ('已扣除请求中给出的测量成本；同额时优先测量更少的信息'
                         if complete else
                         '只在给出了测量成本的组合之间比较净价值，覆盖不完整；'
                         '未给出成本的组合不参与排序'),
            }
    else:
        ranked = sorted(singles, key=lambda entry: -entry['expected_value'])
        best = ranked[0]
        incremental = None
        if joint:
            incremental = _number(
                max(0.0, joint[0]['expected_value'] - best['expected_value']), 2)
        recommendation = {
            'basis': 'single_input_perfect_information_value',
            'inputs': best['inputs'],
            'net_value': None,
            'incremental_value_of_all_inputs': incremental,
            'note': ('未提供测量成本，只能按单输入完美信息价值排序；'
                     '加入其余输入带来的增量是联合价值与单输入价值之差。'
                     '这不是“最值得花钱”的结论，需要补上测量成本后再比较'),
        }
    information = {
        'cost_basis': 'expected_total_cost_including_expected_overrun',
        'baseline_option': baseline_option,
        'baseline_margin': _number(baseline_margin, 2),
        'sets': entries,
        'single_input_ranking': [entry['inputs'][0] for entry in
                                 sorted(singles, key=lambda entry: -entry['expected_value'])],
        'recommendation': recommendation,
        'cost_coverage': ('complete' if fully_costed and len(fully_costed) == len(entries)
                          else 'partial' if fully_costed else 'absent'),
        'scope_note': ('补测价值联系的是方案选择带来的期望成本变化，不是方差贡献；'
                       '区间只反映联合样本的抽样波动，不覆盖模型设定误差'),
    }

    # ---- 诊断与假设
    warnings = []
    if design['rows_excluded']:
        warnings.append('按情景中心平移后 %d/%d 条联合观测超出人数上限 %s，已剔除；'
                        '剩余样本代表可行设计，联合分布被截断'
                        % (design['rows_excluded'], design['rows_total'],
                           _number(data['capacity_limit'], 2)))
    minimums = [target['fit']['minimum_r2'] for target in targets
                if target['fit']['minimum_r2'] is not None]
    if minimums and min(minimums) < 0.5:
        warnings.append('部分条件均值的样本外 R² 低于 0.5，对应贡献估计误差较大，应结合区间与阶数检查阅读')
    if degree_check and not degree_check['ranking_stable_across_degrees']:
        warnings.append('改变多项式阶数会改变某些目标的贡献排序，排序结论不够稳健')
    if completed < bootstrap:
        warnings.append('自助法完成 %d/%d 次、失败 %d 次；区间只基于完成的重复，'
                        '失败次数保留在诊断中' % (completed, bootstrap, bootstrap - completed))
    active = design['correlation_inputs']
    upper = design['correlations'][np.triu_indices(len(active), 1)]
    maximum_correlation = float(np.max(np.abs(upper))) if upper.size else 0.0
    if maximum_correlation > 0.8:
        warnings.append('输入强相关（最大 |r|=%.3f）：单独份额会重复计算共享信息，'
                        '请优先看联合可解释比例、排他比例与交互项' % maximum_correlation)

    assumptions = [
        {'id': 'joint_dependence',
         'text': '联合观测按行使用，保留输入之间允许的相关；没有逐列打乱。'},
        {'id': 'scenario_anchor',
         'text': '联合观测按比例平移到情景中心：每个输入乘以“情景值/样本均值”，'
                 '因此均值为情景值、相关系数与变异系数保持不变。这是情景解释假设，不是原始总体。'},
        {'id': 'feasibility',
         'text': '平移后超出 capacity_limit 的抽选被剔除，估计条件于可行设计；剔除行数见诊断，'
                 '因此不能外推到超出服务上限的人群。'},
        {'id': 'bounds_only',
         'text': 'overhead 只有范围、没有概率；只做端点与情景值的范围分析，不假设均匀分布，'
                 '也不进入 Shapley 分解，不能报告其“概率贡献”。'},
        {'id': 'internal_noise',
         'text': '内部冲击与输入独立、方差固定为常数；超支概率与期望超支额按正态积分解析计算，'
                 '不使用蒙特卡洛，所以模拟噪声不进入参数重要性。'},
        {'id': 'common_shock',
         'text': '方案比较使用含期望超支的期望总成本；内部冲击已解析积分，且两个方案的冲击分布相同，'
                 '在差值中抵消，因此不会抬高任一输入对方案选择的贡献。'},        {'id': 'conditional_model',
         'text': '条件期望用交叉拟合多项式岭回归估计；贡献估计依赖拟合质量，'
                 '每个目标与子集给出样本外 R²。'},
        {'id': 'shapley_convention',
         'text': '相关输入下 Shapley 效应按条件期望分配共同解释的方差，是公平分摊约定；'
                 '单独解释比例之和可超过 1，两两重叠扣减为负，这不是错误。'},
        {'id': 'interval_scope',
         'text': '区间为联合样本行重抽样的百分位区间，只覆盖抽样波动，'
                 '不覆盖模型设定、平移假设或测量误差。'},
        {'id': 'perfect_information',
         'text': '补测价值是完美信息上界；真实调研有测量误差与延迟，实际价值更低。'
                 '未提供测量成本时不能直接换算成经费分配。'},
    ]

    return {
        'analysis_version': ANALYSIS_VERSION,
        'scenario': {'id': scenario['id'], 'inputs': dict(scenario['inputs'])},
        'configuration': {
            'inputs': list(INPUTS),
            'estimator': 'shapley_effects_via_cross_fitted_polynomial_ridge',
            'degree': degree,
            'folds': FOLDS,
            'bootstrap_replicates': bootstrap,
            'confidence': confidence,
            'seed': seed,
            'internal_shock_sd': _number(sigma, 6),
            'budget_ceiling': _number(ceiling, 6),
            'overhead_used_for_targets': _number(overhead, 6),
            'joint_rows': design['rows_total'],
            'joint_rows_used': int(x.shape[0]),
            'anchoring': {
                'method': 'proportional_recentering',
                'sample_mean': {name: _number(value, 6)
                                for name, value in zip(INPUTS, design['sample_mean'])},
                'scenario_anchor': {name: _number(value, 6)
                                    for name, value in zip(INPUTS, design['anchor'])},
                'scale': {name: _number(value, 6)
                          for name, value in zip(INPUTS, design['scale'])},
            },
            'capacity': {
                'limit': _number(data['capacity_limit'], 6),
                'excluded_rows': design['rows_excluded'],
                'retained_fraction': _number(design['retained_fraction'], 6),
            },
            'measurement_costs': {name: _number(value, 2)
                                  for name, value in measurement_costs.items()},
        },
        'decision': {
            'quantity': 'expected_total_cost_including_expected_overrun',
            'preferred_option': baseline_option,
            'margin_id': 'choice_margin',
            'margin_mean': _number(baseline_margin, 2),
            'margin_note': ('正值表示第一个方案更贵；方案选择按期望总成本较小者，'
                            '不是按一次随机试算的结果'),
            'options': list(data['budget_options']),
        },
        'targets': targets,
        'degree_check': degree_check,
        'bounds_only': bounds_report,
        'one_at_a_time': one_at_a_time(data, scenario, [name for name, _ in options]),
        'internal_noise': {
            'standard_deviation': _number(sigma, 6),
            'variance': _number(sigma ** 2, 6),
            'monte_carlo_used_for_targets': False,
            'integration': 'analytic_normal',
            'common_shock_across_options': True,
            'common_shock_note': ('方案成本差与决策后悔额按共享同一次内部冲击计算，'
                                  '冲击在方案间抵消；各方案自身的超支概率仍包含该冲击'),
            'noise_variance_share_of_realized_total': {
                target['option']: target['internal_noise_share_of_realized_variance']
                for target in targets
                if target['kind'] == 'budget' and target['option'] is not None
            },
            'replay': {
                'seeds': [replay_seeds[0], replay_seeds[-1]],
                'replicates': len(replay_seeds),
                'totals': [_number(value, 2) for value in replay_totals[:8]],
                'sample_standard_deviation': _number(replay_sd, 2),
                'expected_standard_deviation': _number(sigma, 2),
                'note': ('同一组参数、只改随机种子时，结果差异来自内部冲击；'
                         '这部分不能算作任何输入的重要性'),
            },
        },
        'information': information,
        'diagnostics': {
            'correlations': {
                active[first] + ',' + active[second]: _number(design['correlations'][first, second], 6)
                for first, second in itertools.combinations(range(len(active)), 2)
            },
            'sample_size': int(x.shape[0]),
            'bootstrap': {
                'requested': bootstrap,
                'completed': completed,
                'failed': bootstrap - completed,
                'failures': failures,
                'method': 'row_resampling_percentile',
            },
            'assumptions': assumptions,
            'warnings': warnings,
            'runtime_seconds': _number(time.monotonic() - started, 3),
        },
    }
