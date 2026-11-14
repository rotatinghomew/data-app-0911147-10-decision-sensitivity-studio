const schema = [{"name": "scenario", "label": "预算情景", "options": ["reference", "busy"]}, {"name": "option", "label": "预算方案", "options": ["shared", "dedicated"]}];
const defaults = {"operation": "calculate", "scenario": "reference", "option": "shared"};
const sources = ["input_contract", "scenarios", "budget_options"];
let study = null;
let latestRequest = {...defaults};

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
    const item = document.createElement('li'); item.textContent = note.text + ' · ' + note.created_at; holder.append(item);
  }
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
}

start().catch(error => display('summary', {error: error.message}));
