import math
import random


def calculate(data, option, inputs, shock_seed):
    if option not in data['budget_options']:
        raise ValueError('未知预算方案')
    required = ('participants', 'hours', 'hourly_cost', 'overhead')
    if any(name not in inputs or not isinstance(inputs[name], (int, float))
           or not math.isfinite(inputs[name]) or inputs[name] < 0 for name in required):
        raise ValueError('预算输入必须是有限非负数')
    if inputs['participants'] > data['capacity_limit']:
        raise ValueError('超出模型可服务人数范围')
    definition = data['budget_options'][option]
    shock = random.Random(shock_seed).gauss(0, data['internal_shock_sd'])
    labor = inputs['participants'] * inputs['hours'] * inputs['hourly_cost'] * definition['labor_multiplier']
    setup = definition['fixed_cost'] + inputs['overhead']
    return {'option': option, 'labor': labor, 'setup': setup,
            'total': labor + setup + shock, 'internal_shock': shock,
            'over_budget': labor + setup + shock > data['budget_ceiling']}


def baseline(data):
    return {'scenarios': [row['id'] for row in data['scenarios']],
            'options': list(data['budget_options']),
            'joint_sample_count': len(data['input_contract']['joint_samples']),
            'bounds_only': data['input_contract']['bounds_only'],
            'notice': '单情景计算不表示总体风险或因素贡献'}


def act(data, request):
    if request.get('operation') != 'calculate':
        raise ValueError('未知情景室操作')
    scenarios = {row['id']: row for row in data['scenarios']}
    if request.get('scenario') not in scenarios:
        raise ValueError('未知预算情景')
    return calculate(data, request['option'], scenarios[request['scenario']]['inputs'], request.get('shock_seed', 31))
