import copy
import unittest
from app import domain
from app.service import load_study


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()
        self.request = {'operation': 'calculate', 'scenario': 'reference', 'option': 'shared'}

    def test_seed_replays_internal_shock(self):
        first = domain.act(self.data, self.request)
        self.assertEqual(first, domain.act(self.data, self.request))

    def test_common_random_numbers_match_between_options(self):
        first = domain.act(self.data, self.request)
        second = domain.act(self.data, dict(self.request, option='dedicated'))
        self.assertEqual(first['internal_shock'], second['internal_shock'])

    def test_deterministic_model_has_no_noise(self):
        data = copy.deepcopy(self.data)
        data['internal_shock_sd'] = 0
        result = domain.act(data, self.request)
        self.assertEqual(result['total'], result['labor'] + result['setup'])

    def test_invalid_capacity_is_not_clamped(self):
        data = copy.deepcopy(self.data)
        data['scenarios'][0]['inputs']['participants'] = data['capacity_limit'] + 1
        with self.assertRaises(ValueError):
            domain.act(data, self.request)


if __name__ == '__main__':
    unittest.main()
