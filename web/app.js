const schema = [{"name": "scenario", "label": "预算情景", "options": ["reference", "busy"]}, {"name": "option", "label": "预算方案", "options": ["shared", "dedicated"]}];
const defaults = {"operation": "calculate", "scenario": "reference", "option": "shared"};
const sources = ["input_contract", "scenarios", "budget_options"];
let study = null;
let latestRequest = {...defaults};
let analysisMeta = null;

async function api(route, payload) {
  const options = payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
  };
  const response = await fetch(route, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || String(response.status));
  return result;
}

function display(target, value) {
  document.getElementById(target).textContent = JSON.stringify(value, null, 2);
}

function renderTable(source) {
  const holder = document.getElementById('records');
  holder.replaceChildren();
  const raw = study[source];
  const rows = Array.isArray(raw) ? raw : Object.entries(raw).map(([key, value]) => ({key, value}));
  if (!rows.length) { holder.textContent = '当前集合没有记录'; return; }
  const columns = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const table = document.createElement('table');
  const head = table.createTHead().insertRow();
  for (const column of columns) {
    const cell = document.createElement('th'); cell.textContent = column; head.append(cell);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const rendered = body.insertRow();
    for (const column of columns) {
      const value = row[column];
      rendered.insertCell().textContent = value === null ? '缺失' : typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
    }
  }
  holder.append(table);
  document.getElementById('record-count').textContent = String(rows.length) + ' 条记录';
}

function renderControls() {
  const form = document.getElementById('action-form');
  for (const control of schema) {
    const label = document.createElement('label'); label.textContent = control.label;
    let input;
    if (control.options) {
      input = document.createElement('select');
      input.multiple = Array.isArray(defaults[control.name]);
      for (const value of control.options) {
        const option = document.createElement('option'); option.value = value; option.textContent = value;
        option.selected = input.multiple ? defaults[control.name].includes(value) : defaults[control.name] === value;
        input.append(option);
      }
    } else {
      input = document.createElement('input'); input.type = 'number'; input.value = defaults[control.name];
    }
    input.name = control.name; label.append(input); form.append(label);
  }
  const button = document.createElement('button'); button.type = 'submit'; button.textContent = "计算单个预算情景";
  form.append(button);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    latestRequest = {operation: defaults.operation};
    for (const control of schema) {
      const field = form.elements.namedItem(control.name);
      latestRequest[control.name] = field.multiple ? [...field.selectedOptions].map(option => option.value)
        : control.options ? field.value : Number(field.value);
    }
    try { display('result', await api('/api/action', latestRequest)); }
    catch (error) { display('result', {error: error.message}); }
  });
}

async function refreshNotes() {
  const result = await api('/api/notes');
  const holder = document.getElementById('notes'); holder.replaceChildren();
  for (const note of result.notes) {
    const item = document.createElement('li');
    item.textContent = note.text + ' · ' + note.created_at;
    holder.append(item);
  }
}

// ------------------------------------------------------------ 敏感性分析

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function percent(value, digits = 1) {
  return value === null || value === undefined ? '—' : (100 * value).toFixed(digits) + '%';
}

function money(value) {
  return value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 0});
}

function intervalText(interval, formatter) {
  if (!interval) return '—';
  return formatter(interval[0]) + ' – ' + formatter(interval[1]);
}

function table(headers, rows) {
  const node = el('table', null, 'analysis-table');
  const head = node.createTHead().insertRow();
  for (const header of headers) head.append(el('th', header));
  const body = node.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    for (const cell of row) {
      const value = cell && typeof cell === 'object' && 'text' in cell ? cell.text : cell;
      const td = el('td', value === null || value === undefined ? '—' : value);
      if (cell && typeof cell === 'object' && cell.note) td.title = cell.note;
      line.append(td);
    }
  }
  const scroller = el('div', null, 'table-scroll');
  scroller.append(node);
  return scroller;
}

function details(summary, build) {
  const node = el('details');
  node.append(el('summary', summary));
  node.append(build());
  return node;
}

const KIND_LABELS = {
  budget: '预算额', overrun_probability: '超支概率', expected_overrun: '期望超支额',
  choice_margin: '方案总成本差', decision_regret: '决策后悔额'
};

function renderSensitivity(data) {
  const holder = document.getElementById('sensitivity-result');
  holder.replaceChildren();

  const decision = el('div', null, 'decision-banner');
  decision.append(el('strong', '当前选择：' + data.decision.preferred_option));
  decision.append(el('span', '按期望总成本（含期望超支）比较，方案差 ' + money(data.decision.margin_mean) +
    '；不是按一次随机试算。'));
  holder.append(decision);

  const warnings = el('div', null, 'warnings');
  if (data.diagnostics.warnings.length) {
    warnings.append(el('strong', '需要先读的限制'));
    const list = el('ul');
    for (const warning of data.diagnostics.warnings) list.append(el('li', warning));
    warnings.append(list);
  } else {
    warnings.append(el('span', '本次没有触发额外限制提示；仍请阅读适用假设。'));
  }
  holder.append(warnings);

  // ---- 目标选择
  const tabs = el('div', null, 'tabs');
  const body = el('div', null, 'target-body');
  const renderTarget = target => {
    body.replaceChildren();
    for (const button of tabs.querySelectorAll('button')) {
      button.classList.toggle('active', button.dataset.target === target.id);
    }
    const contributionRows = target.contributions.map(item => [
      item.input,
      percent(item.share),
      intervalText(item.share_interval, value => percent(value)),
      item.rank === null ? '—' : String(item.rank),
      item.rank_interval ? intervalText(item.rank_interval, value => String(value)) : '—',
      percent(item.explained_by_alone),
      percent(item.exclusive_share)
    ]);
    const contributionBlock = el('div', null, 'contribution-block');
    contributionBlock.append(el('h4', '贡献估计与抽样误差'));
    contributionBlock.append(table(['输入', 'Shapley 份额', '自助区间', '排名', '排名区间', '单独可解释', '排他份额'], contributionRows));
    contributionBlock.append(el('p', '份额是相关输入下对共同解释方差的公平分摊约定；单独可解释之和可超过 100%，' +
      '排他份额只保留该输入独有的部分。区间来自联合样本行重抽样，只覆盖抽样波动。', 'note'));
    body.append(contributionBlock);
    body.append(details('主要交互与分不开的贡献', () => {
      const wrap = el('div');
      wrap.append(table(['输入组合', '联合可解释', '相对单独之和的重叠扣减'],
        target.interactions.map(item => [item.inputs.join(' + '), percent(item.joint_explained), percent(item.overlap_deduction)])));
      const groups = Object.entries(target.group_explained)
        .sort((a, b) => a[0].split('+').length - b[0].split('+').length || a[0].localeCompare(b[0]));
      wrap.append(table(['联合输入组', '可解释方差比例'], groups.map(([key, value]) => [key.replaceAll('+', ' + '), percent(value)])));
      wrap.append(el('p', '负的重叠扣减表示两个输入携带大量相同信息；联合可解释比例说明它们合起来能解释多少，' +
        '而不是各自独立的因果份额。', 'note'));
      if (target.composite_driver.out_of_sample_r2 !== null) {
        wrap.append(el('p', '三个输入经乘积 ' + target.composite_driver.name + ' 起作用的样本外 R²：' +
          target.composite_driver.out_of_sample_r2.toFixed(3) + '。' + target.composite_driver.note, 'note'));
      }
      return wrap;
    }));
    body.append(details('拟合证据与适用假设', () => {
      const wrap = el('div');
      const fitRows = Object.entries(target.fit.out_of_sample_r2).map(([key, value]) => [key, value === null ? '—' : value.toFixed(3)]);
      wrap.append(table(['条件输入集合', '样本外 R²'], fitRows));
      wrap.append(el('p', '方法：' + target.fit.method + '，阶数 ' + target.fit.degree + '，' + target.fit.folds + ' 折交叉拟合；' +
        '最低样本外 R² ' + (target.fit.minimum_r2 === null ? '—' : target.fit.minimum_r2.toFixed(3)) +
        '。R² 越低，条件均值拟合越不可靠，份额区间越应作为主导。', 'note'));
      const assumptions = el('ul');
      for (const assumption of data.diagnostics.assumptions) assumptions.append(el('li', assumption.text));
      wrap.append(assumptions);
      return wrap;
    }));
    const fitSummary = el('p', '均值 ' + (target.kind === 'overrun_probability' ? percent(target.mean, 2) : money(target.mean)) +
      '；标准差 ' + (target.kind === 'overrun_probability' ? percent(target.standard_deviation, 2) : money(target.standard_deviation)) +
      '；份额合计 ' + (target.sum_of_shares === null ? '未定义' : percent(target.sum_of_shares)) +
      '；效率误差 ' + (target.efficiency_error === null ? '—' : target.efficiency_error.toFixed(4)), 'note');
    body.prepend(fitSummary);
    if (target.degenerate) body.prepend(el('p', target.degenerate_reason, 'warnings-inline'));
  };
  const usableTargets = data.targets.filter(target => !target.degenerate);
  const shown = usableTargets.length ? usableTargets : data.targets;
  if (!usableTargets.length) {
    body.append(el('p', '本次联合观测在所有目标上都没有波动：参数重要性未定义。' +
      '若仍能看到内部随机波动，那只是模型内部的模拟噪声，不能当作任何输入的重要性。', 'warnings-inline'));
  }
  for (const target of data.targets) {
    const button = el('button', KIND_LABELS[target.kind] + (target.option ? '·' + target.option : ''));
    button.type = 'button';
    button.dataset.target = target.id;
    if (target.degenerate) button.disabled = true;
    button.addEventListener('click', () => renderTarget(target));
    tabs.append(button);
  }
  holder.append(tabs, body);
  renderTarget(shown[0]);

  // ---- 范围输入
  const bounds = el('div', null, 'card');
  bounds.append(el('h3', '只有范围、没有概率的输入'));
  for (const item of data.bounds_only) {
    bounds.append(el('p', item.input + ' ∈ [' + item.bounds.join(', ') + ']：' + item.note, 'note'));
    if (!item.cases || !item.cases.length) continue;
    bounds.append(table(['取值', '情形', 'shared 预算', 'dedicated 预算', 'shared 超支概率', 'dedicated 超支概率', '事后较优方案'],
      item.cases.map(entry => [
        money(entry.value), entry.kind === 'lower' ? '下端' : entry.kind === 'upper' ? '上端' : '情景值',
        money(entry.budget[data.decision.options[0]]), money(entry.budget[data.decision.options[1]]),
        percent(entry.overrun_probability[data.decision.options[0]], 3), percent(entry.overrun_probability[data.decision.options[1]], 3),
        entry.preferred_option
      ])));
    bounds.append(el('p', item.decision_stable_over_range
      ? '在这个范围内较优方案不变；这不代表范围内每个取值等概率。'
      : '在这个范围内较优方案会改变；不能假设某种分布后报一个“风险概率”。', 'note'));
  }
  holder.append(bounds);

  // ---- 内部噪声
  const noise = el('div', null, 'card');
  noise.append(el('h3', '内部随机波动与模拟噪声'));
  noise.append(el('p', '内部冲击标准差 ' + money(data.internal_noise.standard_deviation) +
    '；超支概率按解析积分计算，目标量不使用蒙特卡洛，所以模拟噪声不会进入参数重要性。', 'note'));
  noise.append(table(['同一参数、只换随机种子的总成本', '数值'],
    data.internal_noise.replay.totals.map((value, index) => ['种子 ' + (data.internal_noise.replay.seeds[0] + index), money(value)])));
  noise.append(el('p', '重复 ' + data.internal_noise.replay.replicates + ' 次的样本标准差 ' +
    money(data.internal_noise.replay.sample_standard_deviation) + '，与设定的 ' +
    money(data.internal_noise.replay.expected_standard_deviation) + ' 一致；' + data.internal_noise.replay.note, 'note'));
  const noiseShares = Object.entries(data.internal_noise.noise_variance_share_of_realized_total)
    .map(([key, value]) => [key, percent(value, 2)]);
  if (noiseShares.length) noise.append(table(['方案的实现方差中内部噪声占比', '比例'], noiseShares));
  holder.append(noise);

  // ---- 补测价值
  const info = el('div', null, 'card');
  info.append(el('h3', '补测价值：下一笔调研该花在哪里'));
  info.append(el('p', '当前基线：' + infoBaselineText(data), 'note'));
  info.append(table(['补测输入', '完美信息价值', '价值区间', '决策翻转概率', '翻转区间', '测量成本', '净价值'],
    data.information.sets.map(entry => [
      entry.inputs.join(' + '), money(entry.expected_value), intervalText(entry.value_interval, money),
      percent(entry.flip_probability), intervalText(entry.flip_interval, value => percent(value)),
      money(entry.measurement_cost), money(entry.net_value)
    ])));
  const recommendation = data.information.recommendation;
  let advice;
  if (recommendation.basis === 'net_value' && recommendation.inputs.length) {
    advice = '按扣除测量成本后的净价值，优先补测 ' + recommendation.inputs.join(' + ') +
      '（净价值 ' + money(recommendation.net_value) + '）。';
  } else if (recommendation.basis === 'net_value') {
    advice = '按已给出的测量成本，被评估的补测组合净价值都不为正（最高 ' + money(recommendation.net_value) +
      '）；这些成本下补测不能仅凭决策价值获得理由。';
  } else {
    advice = '按单输入完美信息价值排序，优先补测 ' + recommendation.inputs.join(' + ') +
      '；加入其余输入的增量价值约 ' + money(recommendation.incremental_value_of_all_inputs) + '。';
  }
  info.append(el('p', '建议：' + advice, 'callout'));
  info.append(el('p', recommendation.note, 'note'));
  info.append(el('p', data.information.scope_note, 'note'));

  const costsForm = el('form', null, 'inline-form');
  costsForm.append(el('label', '测量成本（留空表示未给出）'));
  for (const name of data.configuration.inputs) {
    const label = el('label', name);
    const input = el('input');
    input.type = 'number'; input.min = '0'; input.step = '0.01'; input.name = name;
    input.placeholder = '未给出';
    label.append(input); costsForm.append(label);
  }
  const apply = el('button', '按这些成本重新比较');
  apply.type = 'submit';
  costsForm.append(apply);
  costsForm.addEventListener('submit', async event => {
    event.preventDefault();
    const costs = {};
    for (const field of costsForm.querySelectorAll('input')) {
      if (field.value !== '') costs[field.name] = Number(field.value);
    }
    const config = sensitivityConfig();
    config.measurement_costs = costs;
    await runSensitivity(config, true);
  });
  info.append(costsForm);
  holder.append(info);

  // ---- 诊断
  const diagnostics = el('div', null, 'card');
  diagnostics.append(el('h3', '诊断与对照'));
  diagnostics.append(table(['项目', '值'], [
    ['输入相关系数', Object.entries(data.diagnostics.correlations).map(([key, value]) =>
      key.replace(',', '–') + '=' + (value === null ? '—' : value.toFixed(3))).join('；') || '—'],
    ['联合样本行数', String(data.configuration.joint_rows_used) + ' / ' + String(data.configuration.joint_rows)],
    ['自助法完成', String(data.diagnostics.bootstrap.completed) + ' / ' + String(data.diagnostics.bootstrap.requested) +
      (data.diagnostics.bootstrap.failed ? '（失败 ' + data.diagnostics.bootstrap.failed + ' 次，已保留原因）' : '')],
    ['多项式阶数检查', data.degree_check ? '最大份额差 ' + data.degree_check.max_absolute_share_difference.toFixed(3) +
      '；排序' + (data.degree_check.ranking_stable_across_degrees ? '稳定' : '不稳定') : '—'],
    ['运行时间', data.diagnostics.runtime_seconds + ' 秒']
  ]));
  if (data.diagnostics.bootstrap.failures.length) {
    diagnostics.append(table(['自助法失败位置', '原因'],
      data.diagnostics.bootstrap.failures.map(item => [String(item.replicate + 1), item.error])));
  }
  diagnostics.append(details('现行“逐个 ±10%”做法的对照', () => {
    const wrap = el('div');
    wrap.append(table(['输入', '最大绝对预算变化（首个方案）'],
      data.one_at_a_time.ranking.map(item => [item.input, money(item.maximum_absolute_budget_change)])));
    wrap.append(el('p', data.one_at_a_time.note, 'note'));
    return wrap;
  }));
  diagnostics.append(details('配置与可回查细节', () => {
    const wrap = el('div');
    wrap.append(el('pre', JSON.stringify({
      scenario: data.scenario, configuration: data.configuration, decision: data.decision,
      degree_check: data.degree_check, bootstrap: data.diagnostics.bootstrap
    }, null, 2)));
    return wrap;
  }));
  holder.append(diagnostics);
}

function infoBaselineText(data) {
  return '较优方案 ' + data.information.baseline_option + '，首方案减次方案的期望总成本差 ' +
    money(data.information.baseline_margin) + '。价值是相对固定当前方案能节省的期望成本，不是方差贡献。';
}

function sensitivityConfig() {
  const form = document.getElementById('sensitivity-form');
  return {
    scenario: form.elements.namedItem('scenario').value,
    bootstrap: Number(form.elements.namedItem('bootstrap').value),
    degree: Number(form.elements.namedItem('degree').value),
    confidence: Number(form.elements.namedItem('confidence').value),
    seed: Number(form.elements.namedItem('seed').value)
  };
}

function validateSensitivityConfig(config) {
  const limits = analysisMeta ? analysisMeta.limits : {bootstrap: [20, 600], degree: [1, 3], confidence: [0.5, 0.99]};
  if (!Number.isInteger(config.bootstrap) || config.bootstrap < limits.bootstrap[0] || config.bootstrap > limits.bootstrap[1]) {
    return '自助重复次数应为 ' + limits.bootstrap[0] + ' 至 ' + limits.bootstrap[1] + ' 的整数';
  }
  if (![1, 2, 3].includes(config.degree)) return '多项式阶数应为 1 至 3';
  if (!(config.confidence >= limits.confidence[0] && config.confidence < 1)) {
    return '区间置信度应在 ' + limits.confidence[0] + ' 与 1 之间';
  }
  if (!Number.isInteger(config.seed) || config.seed < 0) return '分析种子应为非负整数';
  return null;
}

let running = false;
async function runSensitivity(config, scroll = false) {
  if (running) return;
  const status = document.getElementById('sensitivity-status');
  const holder = document.getElementById('sensitivity-result');
  const invalid = validateSensitivityConfig(config);
  if (invalid) {
    status.textContent = '分析失败：' + invalid;
    status.classList.add('error');
    return;
  }
  running = true;
  status.textContent = '计算中：' + config.bootstrap + ' 次自助重复，保留联合关系…';
  status.classList.remove('error');
  if (scroll) holder.scrollIntoView({behavior: 'smooth', block: 'start'});
  try {
    const data = await api('/api/sensitivity', config);
    renderSensitivity(data);
    status.textContent = '完成：情景 ' + data.scenario.id + '，联合样本 ' + data.configuration.joint_rows_used +
      ' 行，自助 ' + data.diagnostics.bootstrap.completed + ' 次，用时 ' + data.diagnostics.runtime_seconds + ' 秒。';
    latestRequest = {operation: 'sensitivity', ...config};
  } catch (error) {
    holder.replaceChildren();
    status.textContent = '分析失败：' + error.message;
    status.classList.add('error');
  } finally {
    running = false;
  }
}

function renderSensitivityControls(meta) {
  analysisMeta = meta;
  const form = document.getElementById('sensitivity-form');
  form.noValidate = true;   // 用页面自己的中文校验信息，而不是浏览器气泡
  const scenarioLabel = el('label', '情景');
  const scenario = el('select'); scenario.name = 'scenario';
  for (const value of meta.scenarios) {
    const option = el('option', value); option.value = value;
    option.selected = value === meta.defaults.scenario;
    scenario.append(option);
  }
  scenarioLabel.append(scenario); form.append(scenarioLabel);

  const bootstrapLabel = el('label', '自助重复次数');
  const bootstrap = el('input'); bootstrap.type = 'number'; bootstrap.name = 'bootstrap';
  bootstrap.min = String(meta.limits.bootstrap[0]); bootstrap.max = String(meta.limits.bootstrap[1]);
  bootstrap.value = String(meta.defaults.bootstrap);
  bootstrapLabel.append(bootstrap); form.append(bootstrapLabel);

  const degreeLabel = el('label', '多项式阶数');
  const degree = el('select'); degree.name = 'degree';
  for (const value of [1, 2, 3]) {
    const option = el('option', String(value)); option.value = String(value);
    option.selected = value === meta.defaults.degree;
    degree.append(option);
  }
  degreeLabel.append(degree); form.append(degreeLabel);

  const confidenceLabel = el('label', '区间置信度');
  const confidence = el('input'); confidence.type = 'number'; confidence.name = 'confidence';
  confidence.min = String(meta.limits.confidence[0]); confidence.max = String(meta.limits.confidence[1]);
  confidence.step = '0.01'; confidence.value = String(meta.defaults.confidence);
  confidenceLabel.append(confidence); form.append(confidenceLabel);

  const seedLabel = el('label', '分析种子');
  const seed = el('input'); seed.type = 'number'; seed.name = 'seed'; seed.min = '0';
  seed.value = String(meta.defaults.seed);
  seedLabel.append(seed); form.append(seedLabel);

  const button = el('button', '运行全局敏感性分析'); button.type = 'submit';
  form.append(button);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    await runSensitivity(sensitivityConfig());
  });
  const notice = el('p', meta.notice, 'note');
  form.append(notice);
}

async function start() {
  study = await api('/api/study');
  display('summary', await api('/api/summary'));
  const selector = document.getElementById('source');
  for (const source of sources) {
    const option = document.createElement('option'); option.value = source; option.textContent = source; selector.append(option);
  }
  selector.addEventListener('change', () => renderTable(selector.value));
  renderTable(sources[0]); renderControls();
  document.getElementById('note-form').addEventListener('submit', async event => {
    event.preventDefault();
    const field = document.getElementById('note');
    try { await api('/api/notes', {text: field.value, selection: latestRequest}); field.value = ''; await refreshNotes(); }
    catch (error) { display('result', {error: error.message}); }
  });
  await refreshNotes();
  const meta = await api('/api/sensitivity/meta');
  renderSensitivityControls(meta);
  await runSensitivity(sensitivityConfig());
}

start().catch(error => display('summary', {error: error.message}));
