/* The browser shell. Everything it shows comes from the engine: a hello
   frame (schema, config, theme), a 10 Hz tick, and every log event, over
   Server-Sent Events. Everything it does is a POST of the same commands the
   C# window sends. Colour is never the only signal: every coloured thing
   here has a word beside it, so the page reads the same in greyscale.

   Calibration picks are made on a capture of the game's client area, so
   every coordinate is window-relative by construction and no overlay is
   needed on any OS. */
'use strict';

const TOKEN = window.GPO_TOKEN;
const LOG_LIMIT = 300;
const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const S = {
  hello: null, config: null, defaults: null, templates: {},
  stateColours: {}, eventStyles: {},
  running: false, state: 'idle', fault: null, hasWindow: false, window: null,
  regionValid: false, toggleKey: 'f6', livePreview: false, leadLine: '',
  lastWindowLine: '', webhookLine: '', webhookBrush: 'faint',
  lastTest: null,            // {at, found, detail, png}
  capture: null,             // {png, width, height}
  fields: [], sections: [],
  cards: {},
  welcomed: false,
};

// ---------------------------------------------------------------- engine

async function cmd(name, args = {}) {
  try {
    const response = await fetch('/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN },
      body: JSON.stringify({ cmd: name, ...args }),
    });
    return await response.json();
  } catch (err) {
    return { ok: false, error: 'could not reach the engine: ' + err.message };
  }
}

function ok(reply, what) {
  if (reply.ok) return true;
  addLog('error', `${what}: ${reply.error || 'no reason given'}`);
  return false;
}

function connect() {
  const source = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
  source.onopen = () => setReading('readEngine', 'connected', 'green');
  source.onerror = () => setReading('readEngine', 'reconnecting', 'amber');
  source.onmessage = (event) => {
    let frame;
    try { frame = JSON.parse(event.data); } catch (err) { return; }
    if (frame.t === 'hello') onHello(frame);
    else if (frame.t === 'event') addLog(frame.kind, frame.message, frame.ts);
    else if (frame.t === 'tick') applyTick(frame);
  };
}

const PALETTE_VARS = {
  BG: '--bg', SURFACE: '--surface', SURFACE_2: '--surface2', LINE: '--line',
  LINE_STRONG: '--line-strong', TEXT: '--text', TEXT_2: '--text2', MUTED: '--muted',
  FAINT: '--faint', GREEN: '--green', GREEN_DIM: '--green-dim', AMBER: '--amber', RED: '--red',
};

function onHello(hello) {
  S.hello = hello;
  const root = document.documentElement.style;
  if (hello.theme) {
    for (const [name, variable] of Object.entries(PALETTE_VARS))
      if (hello.theme.palette[name]) root.setProperty(variable, hello.theme.palette[name]);
    if (hello.theme.mono) root.setProperty('--mono', `"${hello.theme.mono}", ${getComputedStyle(document.documentElement).getPropertyValue('--mono')}`);
  }
  S.stateColours = hello.state_colors || {};
  S.eventStyles = hello.event_styles || {};
  S.defaults = hello.defaults;
  S.templates = hello.templates || {};
  $('versionLine').textContent = `version ${hello.version}`;
  $('settingsVersion').innerHTML = '';
  $('settingsVersion').append(`version ${hello.version} · `,
    Object.assign(el('a', '', 'releases'), { href: 'https://github.com/ZeekyBlast/GPO-Fishing-Macro/releases', target: '_blank', rel: 'noopener' }));

  if (S.fields.length && dirtyCount() > 0) {
    addLog('warn', 'engine reconnected - your unsaved settings edits were kept');
  } else {
    buildSettings(hello);
    adoptConfig(hello.config, hello.templates);
  }
  if (!S.welcomed) {
    S.welcomed = true;
    addLog('info', `Welcome - calibrate once (Calibration, on the left), then Start. ` +
      `Hotkeys: ${hello.config.hotkeys.start_stop} toggle, ${hello.config.hotkeys.panic} panic.`);
  }
}

function adoptConfig(config, templates) {
  S.config = config;
  if (templates) S.templates = templates;
  for (const field of S.fields) {
    const group = config[field.obj];
    if (group && field.attr in group) field.load(group[field.attr]);
  }
  S.toggleKey = config.hotkeys.start_stop;
  S.livePreview = !!config.ui.live_preview;
  S.leadLine = `Bar lead ${config.controller.bar_lead.toFixed(2)}s · fish lead ` +
    `${config.controller.fish_lead.toFixed(2)}s · tune Bar lead in Settings`;
  if (!S.running) $('tuningNote').textContent = S.leadLine;
  refreshDirty();
  refreshFilter();
  refreshCalibration();
}

async function patchConfig(patch) {
  const reply = await cmd('set_config', { patch });
  if (!ok(reply, 'save')) return false;
  adoptConfig(reply.result.config, reply.result.templates);
  return true;
}

async function reloadConfig() {
  const reply = await cmd('get_config');
  if (ok(reply, 'reload')) adoptConfig(reply.result.config, reply.result.templates);
}

// ------------------------------------------------------------------- log

function addLog(kind, message, ts) {
  const style = S.eventStyles[kind] || { mark: '-', colour: '' };
  const when = new Date(ts ? ts * 1000 : Date.now());
  const time = [when.getHours(), when.getMinutes(), when.getSeconds()]
    .map((n) => String(n).padStart(2, '0')).join(':');
  for (const list of [$('log'), $('railLog')]) {
    const row = el('div', 'log-row');
    row.append(el('span', 'log-time', time), el('span', 'log-mark', style.mark), el('span', 'log-msg', message));
    row.children[1].style.color = row.children[2].style.color = style.colour || 'var(--text2)';
    list.append(row);
    while (list.children.length > LOG_LIMIT) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }
  const count = $('log').children.length;
  $('logCount').textContent = `${count} line${count === 1 ? '' : 's'} · capped at ${LOG_LIMIT}`;
}

// ------------------------------------------------------------- dashboard

function setReading(id, text, brush) {
  const node = $(id);
  node.textContent = text;
  node.style.color = `var(--${brush})`;
}

function setDot(id, brush) { $(id).style.background = `var(--${brush})`; }

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function applyTick(tick) {
  S.running = !!tick.running;
  S.state = tick.state || 'idle';
  const faultChanged = (tick.fault || null) !== S.fault;
  S.fault = tick.fault || null;
  const hasWindow = !!tick.window;
  const windowChanged = hasWindow !== S.hasWindow;
  S.hasWindow = hasWindow;
  S.window = tick.window;
  S.regionValid = !!tick.region_valid;

  let word = S.state.toUpperCase();
  let colour = S.stateColours[S.state] || 'var(--faint)';
  if (S.fault === 'window_lost') { word = 'STOPPED'; colour = 'var(--red)'; }
  $('stateWord').textContent = word;
  $('stateWord').style.color = colour;

  if (hasWindow) {
    const w = tick.window;
    S.lastWindowLine = `"${w.title}"  ${w.width}x${w.height}  at (${w.left},${w.top})`;
  }
  $('windowLine').textContent = hasWindow ? S.lastWindowLine
    : (S.fault === 'window_lost' && S.lastWindowLine ? 'was ' + S.lastWindowLine : 'no Roblox window');

  // The hint is the empty state: it says what to do, never just "idle".
  let hint, brush, recovery = '', tag = '';
  if (!hasWindow) [hint, brush] = ['Launch Roblox and open GPO.', 'amber'];
  else if (!S.regionValid) [hint, brush] = ['Calibrate the scan region before starting.', 'amber'];
  else if (!S.running) [hint, brush] = [`Ready. Start here or press ${S.toggleKey}.`, 'muted'];
  else if (S.state === 'paused') [hint, brush] = [`Paused. Resume here or press ${S.toggleKey}.`, 'amber'];
  else if (S.state === 'wait') [hint, brush] = ['Line is out, waiting for a bite.', 'muted'];
  else if (S.state === 'reel') [hint, brush] = ['Fighting a fish.', 'green'];
  else [hint, brush] = [S.state, 'muted'];
  // A fault overrides the hint and brings its fix along.
  if (S.fault === 'window_lost') {
    [hint, brush, recovery, tag] = ['Roblox window disappeared. Mouse released; stats and the scan region are kept.', 'amber', 'Recheck window', 'recheck'];
  } else if (S.fault === 'no_gauge') {
    [hint, brush, recovery, tag] = ['The last detection test found no gauge. Test again with the gauge on screen, or re-sample the colours.', 'amber', 'Open Calibration', 'calibration'];
  }
  $('hint').textContent = hint;
  $('hint').style.color = `var(--${brush})`;
  const recover = $('recovery');
  recover.hidden = !recovery;
  recover.textContent = recovery;
  recover.dataset.tag = tag;

  const start = $('startBtn');
  start.textContent = S.running ? 'Stop' : 'Start';
  start.classList.toggle('is-stop', S.running);
  $('pauseBtn').disabled = !S.running;
  $('pauseBtn').textContent = S.state === 'paused' ? 'Resume' : 'Pause';

  setDot('dotDashboard', S.running ? 'green'
    : ['window_lost', 'no_gauge', 'no_window'].includes(S.fault) ? 'amber' : 'line-strong');
  setDot('dotCalibration', S.regionValid && S.fault !== 'no_gauge' ? 'line-strong' : 'amber');
  setReading('readRoblox', hasWindow
    ? `${tick.window.width}x${tick.window.height} (${tick.window.left},${tick.window.top})` : 'not found',
    hasWindow ? 'green' : 'amber');

  const stats = tick.stats || {};
  const caught = stats.fish_total || 0, escaped = stats.fails || 0, landed = caught + escaped;
  $('tileCaught').textContent = caught;
  $('tilePerHour').textContent = Math.round(stats.fish_per_hour || 0);
  const hookRate = $('tileHookRate');
  hookRate.textContent = landed > 0 ? Math.round(100 * caught / landed) + '%' : '-';
  hookRate.classList.toggle('is-live', S.running && landed > 0);
  $('tileEscaped').textContent = escaped;
  $('tileTimeouts').textContent = stats.recast_timeouts || 0;
  $('tileSession').textContent = formatUptime(stats.uptime_seconds || 0);

  applyTelemetry(tick.telemetry);
  applyWebhook(tick.webhook);
  if (faultChanged && S.fault === 'webhook_failed') addLog('error', 'webhook: ' + S.webhookLine);

  $('previewNote').textContent = S.livePreview ? '' : 'preview off';
  if (tick.preview) {
    $('preview').src = 'data:image/png;base64,' + tick.preview;
    $('previewWell').classList.add('has-image');
  } else if (!S.livePreview || !tick.telemetry) {
    $('previewWell').classList.remove('has-image');
  }
  if (faultChanged || windowChanged) refreshCalibration();
}

const signed = (v) => ((v >= 0 ? '+' : '-') + Math.abs(Math.round(v))).padStart(5);
const num = (v) => String(Math.round(v)).padStart(5);

function applyTelemetry(tm) {
  const hold = $('holdWord'), onFish = $('onFish'), meter = $('meterFill'), line = $('telemetry');
  if (!tm) {
    hold.textContent = '-'; hold.style.color = 'var(--faint)';
    onFish.textContent = ''; meter.style.width = '0'; meter.style.background = 'var(--green-dim)';
    line.textContent = S.running ? 'waiting for a bite, readings appear here during a fight'
      : 'start the macro to see live readings';
    line.classList.remove('is-live');
    $('tuningNote').textContent = S.leadLine;
    return;
  }
  hold.textContent = tm.hold ? 'HOLD' : 'drop';
  hold.style.color = tm.hold ? 'var(--green)' : 'var(--muted)';
  onFish.textContent = `${tm.on_bar ? 'on the fish' : 'off the fish'}   ${Math.round(tm.on_bar_pct)}% this fight`;
  onFish.style.color = tm.on_bar ? 'var(--green)' : 'var(--muted)';
  meter.style.width = `${Math.max(0, Math.min(100, tm.on_bar_pct))}%`;
  meter.style.background = tm.on_bar ? 'var(--green)' : 'var(--green-dim)';
  line.textContent = `bar ${num(tm.bar_y)} ${signed(tm.bar_vel)}/s    fish ${num(tm.fish_y)} ` +
    `${signed(tm.fish_vel)}/s    error ${signed(tm.error)}    ` +
    `${tm.elapsed.toFixed(1).padStart(4)}s ${String(tm.frames).padStart(4)} frames`;
  line.classList.add('is-live');
  $('tuningNote').textContent = 'error is bar minus fish, both lead-compensated. Steady negative ' +
    'means the brake is late: raise Bar lead. Steady positive: lower it.';
}

function applyWebhook(hook) {
  if (!hook) return;
  if (!hook.configured) {
    S.webhookLine = hook.last_result || '';
    S.webhookBrush = 'faint';
  } else {
    const parts = [hook.last_result || ''];
    if (hook.sent > 0 || hook.failed > 0) parts.push(`${hook.sent} sent, ${hook.failed} failed`);
    if (hook.queued > 0) parts.push(`${hook.queued} queued`);
    if (hook.dropped > 0) parts.push(`${hook.dropped} dropped`);
    if (hook.suppressed > 0) parts.push(`${hook.suppressed} throttled`);
    S.webhookLine = parts.join('   ');
    S.webhookBrush = hook.sent > 0 && hook.failed === 0 ? 'green' : hook.failed > 0 ? 'red' : 'faint';
  }
  const field = S.fields.find((f) => f.obj === 'webhook' && f.attr === 'url');
  if (field) field.setEffect(S.webhookLine, S.webhookBrush);
}

// -------------------------------------------------------------- settings

function buildSettings(hello) {
  const form = $('form');
  form.innerHTML = '';
  S.fields = [];
  S.sections = [];
  const byName = new Map();
  for (const entry of hello.schema) {
    let section = byName.get(entry.section);
    if (!section) {
      section = makeSection(entry.section, (hello.notes || {})[entry.section] || '', !!entry.advanced);
      byName.set(entry.section, section);
      S.sections.push(section);
      form.append(section.el);
    }
    const field = makeField(entry);
    section.fields.push(field);
    section.panel.append(field.row);
    S.fields.push(field);
  }
  for (const section of S.sections) {
    if (section.name === 'Webhook') {
      const test = el('button', 'btn quiet', 'Send test message');
      test.style.marginTop = '8px';
      test.onclick = webhookTest;
      section.panel.append(test);
    }
  }
}

function makeSection(name, note, advanced) {
  const wrap = el('div', 'section');
  const head = el('div', 'section-head');
  head.append(el('span', 'heading', name.toUpperCase()));
  if (advanced) head.append(el('span', 'badge amber', 'advanced'));
  const reset = el('button', 'btn text', 'reset section');
  head.append(reset);
  wrap.append(head);
  if (note) wrap.append(el('div', 'note section-note', note));
  const panel = el('div', 'panel section-panel');
  wrap.append(panel);
  const section = { name, advanced, el: wrap, panel, fields: [] };
  reset.onclick = () => {
    if (!S.defaults) return;
    for (const field of section.fields) {
      const group = S.defaults[field.obj];
      if (group && field.attr in group) field.edit(group[field.attr]);
    }
    setStatus(`${name} put back to its defaults - Save to keep that`, 'amber');
  };
  return section;
}

function makeField(entry) {
  const field = {
    obj: entry.obj, attr: entry.attr, label: entry.label, kind: entry.kind,
    gates: entry.gate || [], saved: '', capturing: false,
  };
  const row = el('div', 'row');
  field.row = row;
  const effect = el('span', 'effect', entry.effect || '');
  field.setEffect = (text, brush) => {
    effect.textContent = text;
    effect.className = 'effect' + (brush === 'green' ? ' green' : brush === 'red' ? ' red' : '');
  };

  if (entry.kind === 'bool') {
    row.classList.add('bool');
    const label = el('label', 'check');
    const input = el('input');
    input.type = 'checkbox';
    label.append(input, el('span', 'box'), el('span', '', entry.label));
    row.append(label);
    field.input = input;
    field.current = () => input.checked ? 'true' : 'false';
    field.isOn = () => input.checked;
    field.edit = (value) => { input.checked = value === true; onEdit(); };
    field.value = () => input.checked;
    input.onchange = onEdit;
  } else if (entry.kind === 'hotkey') {
    const key = el('button', 'btn keycap', 'not set');
    key.title = 'Click, then press the key you want. Esc keeps this one.';
    row.append(el('span', 'row-label', entry.label), key, effect);
    field.input = key;
    field.text = '';
    field.current = () => field.text.trim();
    field.isOn = () => field.text.trim().length > 0;
    field.paint = () => { key.textContent = field.capturing ? 'press a key…' : (field.text.trim() ? field.text.trim().toUpperCase() : 'not set'); };
    field.edit = (value) => { field.text = value == null ? '' : String(value); field.paint(); onEdit(); };
    field.value = () => field.text.trim();
    key.onclick = () => beginHotkeyCapture(field);
  } else {
    const input = el('input', 'field');
    input.type = entry.kind === 'secret' ? 'password' : 'text';
    input.autocomplete = 'off';
    input.spellcheck = false;
    row.append(el('span', 'row-label', entry.label), input, effect);
    field.input = input;
    field.current = () => input.value.trim();
    field.isOn = () => input.value.trim().length > 0;
    field.edit = (value) => { input.value = value == null ? '' : String(value); onEdit(); };
    field.value = () => {
      const text = input.value.trim();
      if (entry.kind === 'int' || entry.kind === 'float') {
        const n = Number(text);
        if (text === '' || !Number.isFinite(n)) throw new Error(`invalid value for "${entry.label}": ${text}`);
        return entry.kind === 'int' ? Math.round(n) : n;
      }
      return text;
    };
    input.oninput = onEdit;
  }
  field.load = (value) => {
    if (field.kind === 'bool') field.input.checked = value === true;
    else if (field.kind === 'hotkey') { field.text = value == null ? '' : String(value); field.paint(); }
    else field.input.value = value == null ? '' : String(value);
    field.saved = field.current();
  };
  field.isDirty = () => field.current() !== field.saved;
  function onEdit() { fadeStatus(); refreshDirty(); refreshFilter(); }
  return field;
}

function dirtyCount() { return S.fields.filter((f) => f.isDirty()).length; }

function refreshDirty() {
  const count = dirtyCount();
  $('dirtyLine').textContent = count === 0 ? '' : `${count} unsaved change${count === 1 ? '' : 's'}`;
  $('saveBtn').classList.toggle('is-clean', count === 0);
  setDot('dotSettings', count === 0 ? 'line-strong' : 'amber');
}

/* A field shows when any of its gates is on and itself showing; a search
   overrides the gates so typing "walk" finds the walk keys. */
function refreshFilter() {
  const text = $('filter').value.trim().toLowerCase();
  const advanced = $('showAdvanced').checked;
  const index = new Map(S.fields.map((f) => [f.obj + '.' + f.attr, f]));
  const revealed = (field, depth = 0) => {
    if (!field.gates.length || depth > 8) return true;
    return field.gates.some((g) => {
      const gate = index.get(g.obj + '.' + g.attr);
      return gate && gate.isOn() && revealed(gate, depth + 1);
    });
  };
  for (const section of S.sections) {
    let any = false;
    for (const field of section.fields) {
      const visible = text ? field.label.toLowerCase().includes(text) : revealed(field);
      field.row.hidden = !visible;
      any = any || visible;
    }
    section.el.hidden = !(any && (advanced || !section.advanced));
  }
}

function setStatus(text, brush) {
  const node = $('settingsStatus');
  node.hidden = !text;
  node.textContent = text;
  node.className = 'settings-status ' + (brush || '');
}

function fadeStatus() {
  const node = $('settingsStatus');
  if (!node.hidden) node.className = 'settings-status faint';
}

async function save(quiet = false) {
  const patch = {};
  for (const field of S.fields) {
    let value;
    try { value = field.value(); } catch (err) {
      addLog('error', err.message);
      setStatus('not saved - ' + err.message, 'red');
      return false;
    }
    (patch[field.obj] ||= {})[field.attr] = value;
  }
  const reply = await cmd('set_config', { patch });
  if (!reply.ok) {
    addLog('error', 'save: ' + reply.error);
    setStatus('not saved - ' + reply.error, 'red');
    return false;
  }
  adoptConfig(reply.result.config, reply.result.templates);
  if (!quiet) { addLog('info', 'settings saved'); setStatus('saved', 'green'); }
  return true;
}

async function webhookTest() {
  if (!await save(true)) return;                 // otherwise this tests the URL from before the paste
  const reply = await cmd('webhook_test');
  if (!reply.ok) { setStatus('webhook test failed: ' + reportFailure(reply, 'webhook test'), 'red'); return; }
  const problem = reply.result.problem;
  addLog(problem ? 'warn' : 'info', problem ? `webhook: ${problem}` : 'webhook: test message queued');
  setStatus(problem ? `webhook: ${problem}` : 'saved, test message queued - the URL row reports delivery',
    problem ? 'amber' : 'green');
}

function reportFailure(reply, what) {
  const detail = reply.error || 'no reason given';
  addLog('error', `${what}: ${detail}`);
  return detail;
}

function exportSettings() {
  if (!S.config) return;
  const copy = JSON.parse(JSON.stringify(S.config));
  if (copy.webhook) copy.webhook.url = '';      // a credential never leaves in an export
  const blob = new Blob([JSON.stringify(copy, null, 2)], { type: 'application/json' });
  const link = el('a');
  link.href = URL.createObjectURL(blob);
  link.download = 'gpo-macro-settings.json';
  link.click();
  URL.revokeObjectURL(link.href);
  addLog('info', 'settings exported - without the webhook URL');
  setStatus('exported gpo-macro-settings.json - without the webhook URL', 'green');
}

async function importSettings(file) {
  let patch;
  try {
    patch = JSON.parse(await file.text());
    if (!patch || typeof patch !== 'object' || Array.isArray(patch)) throw new Error('not an object');
  } catch (err) {
    addLog('error', `could not read ${file.name}: ${err.message}`);
    setStatus(`could not read ${file.name}: ${err.message}`, 'red');
    return;
  }
  const reply = await cmd('set_config', { patch });
  if (!reply.ok) { setStatus('import failed: ' + reportFailure(reply, 'import'), 'red'); return; }
  adoptConfig(reply.result.config, reply.result.templates);
  addLog('info', `settings imported from ${file.name}`);
  setStatus(`imported from ${file.name}`, 'green');
}

// ---------------------------------------------------------- hotkey capture

const KEY_NAMES = {
  Space: 'space', Tab: 'tab', Enter: 'enter', Backspace: 'backspace', Insert: 'insert',
  Delete: 'delete', Home: 'home', End: 'end', PageUp: 'page_up', PageDown: 'page_down',
  Pause: 'pause', ScrollLock: 'scroll_lock', CapsLock: 'caps_lock', NumLock: 'num_lock',
  PrintScreen: 'print_screen', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
};

function hotkeyName(event) {
  const code = event.code || '';
  let name = null, m;
  if ((m = /^F(\d{1,2})$/.exec(code))) name = 'f' + m[1];
  else if ((m = /^Key([A-Z])$/.exec(code))) name = m[1].toLowerCase();
  else if ((m = /^Digit(\d)$/.exec(code))) name = m[1];
  else name = KEY_NAMES[code] || null;
  if (!name) return null;
  const parts = [];
  if (event.ctrlKey) parts.push('ctrl');
  if (event.shiftKey) parts.push('shift');
  if (event.altKey) parts.push('alt');
  parts.push(name);
  return parts.join('+');
}

let capturingField = null;

function beginHotkeyCapture(field) {
  if (capturingField) endHotkeyCapture(capturingField, '');
  const current = field.text.trim() ? field.text.trim().toUpperCase() : 'nothing';
  capturingField = field;
  field.capturing = true;
  field.paint();
  document.addEventListener('keydown', onHotkeyKey, true);
  setTimeout(() => document.addEventListener('mousedown', cancelHotkeyCapture, true), 0);
  setStatus(`press the key for "${field.label}" - Esc keeps ${current}`, '');
}

function onHotkeyKey(event) {
  const field = capturingField;
  if (!field) return;
  event.preventDefault();
  event.stopPropagation();
  if (event.key === 'Escape') { endHotkeyCapture(field, 'kept ' + (field.text.trim().toUpperCase() || 'nothing')); return; }
  if (/^(Control|Shift|Alt|Meta)/.test(event.code)) return;        // wait for the key it modifies
  const name = hotkeyName(event);
  if (!name) { setStatus('that key cannot be a hotkey - try another', 'amber'); return; }
  const other = S.fields.find((f) => f.obj === 'hotkeys' && f.attr !== field.attr);
  if (other && other.text.trim().toLowerCase() === name) {
    setStatus(`${name.toUpperCase()} is already the ${other.attr === 'panic' ? 'panic' : 'toggle'} key - press another`, 'red');
    return;
  }
  field.text = name;
  endHotkeyCapture(field, `${field.label}: ${name.toUpperCase()} - Save to keep it`);
  field.paint();
  refreshDirty();
}

function cancelHotkeyCapture() {
  if (capturingField) endHotkeyCapture(capturingField, 'kept ' + (capturingField.text.trim().toUpperCase() || 'nothing'));
}

function endHotkeyCapture(field, status) {
  field.capturing = false;
  field.paint();
  capturingField = null;
  document.removeEventListener('keydown', onHotkeyKey, true);
  document.removeEventListener('mousedown', cancelHotkeyCapture, true);
  if (status) setStatus(status, '');
}

// ------------------------------------------------------------ calibration

const CARDS = [
  { key: 'region', title: '1 · SCAN REGION', optional: false,
    note: 'Start a fishing minigame in GPO, then auto-detect. Give the gauge plenty of room - it is anchored to the bobber and slides as the camera moves, so a tight box clips it mid-fight.',
    primary: ['Test detection', 'test'], extras: [['Auto-detect', 'auto'], ['Drag-select', 'drag']] },
  { key: 'colours', title: '2 · DETECTION COLOURS', optional: false,
    note: 'Sampled from the gauge itself. The fish marker is found by shape rather than by colour, so these two are only used by auto-detect.',
    primary: ['Re-sample both', 'resample'], extras: [['Sample gauge blue', 'blue'], ['Sample bar black', 'black']] },
  { key: 'cast', title: '3 · CAST', optional: true,
    note: 'Optional. Where the line goes and how hard. Pick a point over the water and the cursor moves there before every cast, so a menu or a hand cannot leave it aimed at the dock. GPO has no power meter: holding longer casts farther, up to a cap, so Test cast with the rod out, watch the bobber, and change Cast hold in Settings.',
    primary: ['Test cast', 'test_cast'], extras: [['Pick cast point', 'cast_point']] },
  { key: 'bait', title: '4 · BAIT UPKEEP', optional: true,
    note: "Stand at the Trading Hub dock: the spot beside Blacksmith Sen where his prompt shows and the water is in reach is the only place auto-craft is proven, and walking to him from anywhere else does not work yet - leave it off. Calibrate crafting walks his menu with you one screen at a time: the dialogue, the menu, the fish list, the bubble he leaves behind, then your bait's row on the rod. Buying: hold the key at the bait barrel and pick the quantity box and Confirm. Only if the macro has to walk to Sen: snip his T badge standing at him, and his floating name standing at the fishing spot - the walk back steers by where that name sits on screen.",
    primary: ['Calibrate crafting', 'craft'],
    extras: [['Menu region', 'menu'], ['N/M counter', 'counter'], ['Bait row', 'row'], ['Stack dialog', 'stack'],
             ['Pick barrel points', 'barrel'], ["Snip Sen's T badge", 'badge'], ["Snip Sen's name (from the spot)", 'nametag']] },
  { key: 'fruit', title: '5 · DEVIL FRUIT STORAGE', optional: true,
    note: 'Every fruit shares one hotbar icon, so the macro finds which slot holds a fruit, presses that key, clicks the green Store Fruit prompt, and reads the banner to see whether it took. Stand at the storage point while you calibrate.',
    primary: ['Calibrate storage', 'storage'],
    extras: [['Snip fruit icon', 'icon'], ['Hotbar row', 'hotbar'], ['Banner strip', 'banner'], ['Store button', 'store']] },
];

const STACK_LABELS = ["the slider's knob", "the far right end of the slider's track", 'anywhere on the green Craft Selected button'];
const STACK_ATTRS = ['craft_slider', 'craft_slider_end', 'craft_all'];

function buildCards() {
  const host = $('cards');
  host.innerHTML = '';
  for (const spec of CARDS) {
    const card = el('div', 'card panel');
    const head = el('button', 'card-head');
    const chevron = el('span', 'chevron', '>');
    const title = el('span', 'card-title', spec.title);
    const mark = el('span', 'card-mark', '');
    head.append(chevron, title);
    if (spec.optional) head.append(el('span', 'badge', 'optional'));
    head.append(mark);
    const body = el('div', 'card-body');
    const note = el('div', 'note card-note', spec.note);
    const fault = el('div', 'fault');
    fault.hidden = true;
    const faultWord = el('span', 'fault-word', 'note');
    const faultText = el('span', 'fault-text', '');
    fault.append(faultWord, faultText);
    const row = el('div', 'card-row');
    const thumb = el('div', 'thumb');
    thumb.hidden = true;
    const thumbImg = el('img');
    const thumbTag = el('span', 'well-tag', '');
    thumb.append(thumbImg, thumbTag);
    const readout = el('div', 'readout', '');
    row.append(thumb, readout);
    const actions = el('div', 'card-actions');
    const primary = el('button', 'btn primary', spec.primary[0]);
    primary.onclick = () => runAction(spec.primary[1]);
    const more = el('button', 'btn text', 'more actions');
    actions.append(primary, more);
    const extras = el('div', 'card-extras');
    for (const [label, tag] of spec.extras) {
      const chip = el('button', 'btn chip', label);
      chip.onclick = () => runAction(tag);
      extras.append(chip);
    }
    body.append(note, fault, row, actions, extras);
    card.append(head, body);
    host.append(card);
    head.onclick = () => { card.classList.toggle('is-open'); chevron.textContent = card.classList.contains('is-open') ? 'v' : '>'; };
    more.onclick = () => { card.classList.toggle('is-more'); more.textContent = card.classList.contains('is-more') ? 'fewer actions' : 'more actions'; };
    S.cards[spec.key] = { spec, card, title, mark, fault, faultWord, faultText, thumb, thumbImg, thumbTag, readout, chevron };
  }
}

function showCard(key, state, readout, faultMessage = '', thumb = null) {
  const c = S.cards[key];
  const cannotVerify = !S.hasWindow;
  const shown = cannotVerify ? 'unverifiable' : state;
  const marks = { set: ['set', 'green'], failed: ['set · test failed', 'red'],
                  unverifiable: ['cannot verify', 'faint'], notset: [c.spec.optional ? 'not set · optional' : 'not set', c.spec.optional ? 'faint' : 'amber'] };
  const [word, brush] = marks[shown];
  c.mark.textContent = word;
  c.mark.style.color = `var(--${brush})`;
  c.title.classList.toggle('is-dim', c.spec.optional && state === 'notset');
  c.readout.textContent = readout;
  c.readout.classList.toggle('is-empty', state === 'notset');
  c.fault.hidden = !faultMessage;
  c.fault.classList.toggle('red', shown === 'failed');
  c.faultWord.textContent = shown === 'failed' ? 'why' : 'note';
  c.faultText.textContent = faultMessage;
  c.thumb.hidden = !thumb;
  if (thumb) {
    c.thumb.classList.toggle('is-set', state === 'set');
    if (thumb.png) { c.thumbImg.hidden = false; c.thumbImg.src = 'data:image/png;base64,' + thumb.png; c.thumb.style.background = ''; }
    else { c.thumbImg.hidden = true; c.thumb.style.background = thumb.css || ''; }
    c.thumbTag.textContent = thumb.tag || '';
  }
  c.state = state;
}

const rowText = (name, detail) => `${name.padEnd(8)} ${detail || 'not set'}`;
const pointText = (arr) => (Array.isArray(arr) && arr.length >= 2) ? `(${arr[0]},${arr[1]})` : '';
const regionSize = (r) => (r && r.x2 - r.x1 > 0 && r.y2 - r.y1 > 0) ? `${r.x2 - r.x1}x${r.y2 - r.y1}` : '';
const pointsSet = (group, attrs) => {
  const set = attrs.filter((a) => Array.isArray(group[a]) && group[a].length >= 2).length;
  return set === 0 ? '' : `${set}/${attrs.length} points`;
};
const ago = (at) => {
  const seconds = (Date.now() - at) / 1000;
  return seconds < 90 ? 'just now' : seconds < 5400 ? `${Math.round(seconds / 60)} min ago`
    : seconds < 129600 ? `${Math.round(seconds / 3600)} h ago` : `${Math.round(seconds / 86400)} days ago`;
};
const WORDS = ['None', 'One', 'Two', 'Three', 'Four', 'Five'];

function refreshCalibration() {
  const config = S.config;
  if (!config) return;
  const r = config.scan_region;
  const valid = r.x2 > r.x1 && r.y2 > r.y1;
  const size = `${r.x2 - r.x1} x ${r.y2 - r.y1}`;
  setReading('readRegion', valid ? `${size} · set` : 'not set', valid ? 'green' : 'amber');

  const test = S.lastTest;
  const testLine = test ? (test.found ? `tested ${ago(test.at)}, bar and fish read` : `tested ${ago(test.at)}, ${test.detail}`) : 'never tested';
  const noGauge = S.fault === 'no_gauge' || (test && !test.found);
  showCard('region',
    !valid ? 'notset' : noGauge ? 'failed' : 'set',
    valid ? `region  (${r.x1},${r.y1}) - (${r.x2},${r.y2})\nsize    ${size}\n${testLine}`
          : 'not set - run auto-detect with the gauge on screen',
    noGauge ? 'The test found no run of gauge blue tall enough to be the bar. Start a minigame so the gauge is up and test again. If the gauge was up, the blue was sampled off the sea - re-sample it from inside the gauge.' : '',
    test && test.png ? { png: test.png, tag: 'last test' } : null);

  const d = config.detection;
  const rgb = (v) => `rgb(${v[0]}, ${v[1]}, ${v[2]})`;
  showCard('colours', 'set',
    `gauge blue  RGB (${d.bar_blue.join(', ')})\nbar black   RGB (${d.track_gray.join(', ')})\ntolerance   +/- ${d.color_tolerance} per channel`,
    noGauge && valid ? 'If the region is right and the test still fails, this blue is the reason: re-sample it from inside the gauge, not the sea. Widening the tolerance cannot fix a wrong colour.' : '',
    { css: `linear-gradient(90deg, ${rgb(d.bar_blue)} 0 50%, ${rgb(d.track_gray)} 50% 100%)`, tag: 'sampled' });

  const f = config.fruit;
  const fruitParts = [S.templates.fruit ? f.template_path : '', regionSize(f.hotbar_region), regionSize(f.banner_region), pointText(f.store_point)];
  showCard('fruit', fruitParts.every((p) => p) ? 'set' : 'notset',
    [rowText('icon', fruitParts[0]), rowText('hotbar', fruitParts[1]), rowText('banner', fruitParts[2]), rowText('store', fruitParts[3])].join('\n'));

  const fi = config.fishing;
  const castPoint = pointText(fi.cast_point);
  showCard('cast', castPoint ? 'set' : 'notset',
    [rowText('aim', castPoint || 'wherever the cursor is'), rowText('hold', `${fi.cast_hold_duration.toFixed(2)}s`),
     rowText('re-equip', fi.equip_every_cast ? 'every cast' : 'once')].join('\n'));

  const b = config.bait;
  const craft = pointsSet(b, ['dialog_yes', 'craft_recipe', 'craft_add', 'craft_pick', 'craft_button', 'craft_close', 'dialog_end']);
  const stack = pointsSet(b, STACK_ATTRS);
  const barrel = pointsSet(b, ['shop_quantity', 'shop_confirm']);
  const home = pointText(b.home_tag);
  showCard('bait', craft.startsWith('7/') || barrel.startsWith('2/') ? 'set' : 'notset',
    [rowText('craft', craft), rowText('stack', stack), rowText('menu', regionSize(b.menu_region)),
     rowText('counter', regionSize(b.craft_counter_region)), rowText('bait row', pointText(b.bait_select)),
     rowText('barrel', barrel), rowText('T badge', S.templates.prompt ? b.prompt_template : ''),
     rowText('nametag', S.templates.tag && home ? `home ${home}` : '')].join('\n'));

  const baitOn = b.auto_craft || b.auto_buy, fruitOn = f.auto_store;
  setReading('readOptional', baitOn && fruitOn ? 'bait, fruit on' : baitOn ? 'bait on · fruit off' : fruitOn ? 'bait off · fruit on' : 'bait, fruit off',
    baitOn || fruitOn ? 'text2' : 'faint');

  const set = Object.values(S.cards).filter((c) => c.state === 'set').length;
  $('calSummary').textContent = !S.hasWindow ? 'No Roblox window, so nothing here can be re-run or tested.'
    : !valid ? 'The scan region is not set, so nothing can fish yet.'
    : noGauge ? 'Set, but the last test could not read the gauge.'
    : `${WORDS[set]} of five set. Cast, bait upkeep and fruit storage are optional and ${baitOn && fruitOn ? 'on' : !baitOn && !fruitOn ? 'off' : 'one is on'}.`;

  // The proof rail.
  const rail = $('railReadings');
  rail.innerHTML = '';
  const line = (text, brush) => { const node = el('div', '', text); node.style.color = `var(--${brush})`; rail.append(node); };
  line(S.hasWindow ? S.lastWindowLine : 'no Roblox window', S.hasWindow ? 'green' : 'amber');
  line(valid ? `region (${r.x1},${r.y1}) - (${r.x2},${r.y2})` : 'region not set', valid ? 'text2' : 'amber');
  line(`blue (${d.bar_blue.join(',')}) · bar (${d.track_gray.join(',')})`, 'text2');
  line(testLine, test ? (test.found ? 'green' : 'red') : 'faint');
  const well = $('testWell');
  if (test && test.png) { $('testImage').src = 'data:image/png;base64,' + test.png; well.classList.add('has-image'); $('testTag').textContent = size; }
  else { well.classList.remove('has-image'); $('testTag').textContent = ''; }
}

// ------------------------------------------------------ calibration actions

let actionBusy = false;

async function runAction(tag) {
  if (actionBusy) return;
  actionBusy = true;
  try {
    const action = ACTIONS[tag];
    if (action) await action();
  } catch (err) {
    addLog('error', `${tag}: ${err.message}`);
  } finally {
    actionBusy = false;
  }
}

async function captureAndPick(options) {
  const reply = await cmd('capture');
  if (!reply.ok) {
    addLog('warn', reply.error && reply.error.includes('window') ? 'no Roblox window - launch GPO before calibrating' : 'capture: ' + reply.error);
    return null;
  }
  S.capture = reply.result;
  return picker.show({ ...options, png: S.capture.png, width: S.capture.width, height: S.capture.height });
}

async function regionInto(attr, obj, instructions, done) {
  const region = await captureAndPick({ mode: 'region', text: instructions });
  if (!region) return false;
  const patch = obj ? { [obj]: { [attr]: region } } : { [attr]: region };
  if (!await patchConfig(patch)) return false;
  addLog('info', `${done} (${region.x2 - region.x1}x${region.y2 - region.y1})`);
  return true;
}

async function pickPoints(obj, labels, attrs, what) {
  const picked = await captureAndPick({ mode: 'points', labels });
  if (!picked) { addLog('info', `${what}: cancelled`); return false; }
  // A skipped point is stored as empty, which is how the engine reads "not calibrated".
  const patch = {};
  labels.forEach((label, i) => { patch[attrs[i]] = picked[label] ? picked[label] : []; });
  if (!await patchConfig({ [obj]: patch })) return false;
  const count = Object.keys(picked).length;
  addLog('info', `${what} points saved: ${count}/${labels.length}`);
  return count === labels.length;
}

async function sampleColour(attr) {
  const what = attr === 'bar_blue' ? "the gauge's blue, inside the gauge" : "the bar's black, on the bar itself";
  const point = await captureAndPick({ mode: 'color', text: `Click ${what}` });
  if (!point) return false;
  const reply = await cmd('sample_color', { x: point[0], y: point[1], attr, from_capture: true });
  if (!ok(reply, 'colour sample')) return false;
  const [r, g, b] = reply.result.rgb;
  addLog('info', `${attr} set to RGB (${r}, ${g}, ${b})`);
  await reloadConfig();
  return true;
}

async function snipTemplate(text, what, path) {
  const region = await captureAndPick({ mode: 'region', text });
  if (!region) { addLog('info', `${what} snip cancelled`); return null; }
  const args = { ...region, from_capture: true };
  if (path) args.path = path;
  const reply = await cmd('save_template', args);
  if (!ok(reply, what)) return null;
  addLog('info', `${what} saved (${reply.result.width}x${reply.result.height})`);
  await reloadConfig();
  return region;
}

async function testDetection() {
  const reply = await cmd('test_detection');
  if (!ok(reply, 'detection test')) return;
  const result = reply.result;
  S.lastTest = { at: Date.now(), found: result.found, detail: result.detail, png: result.png };
  addLog(result.found ? 'info' : 'warn', `detection test: ${result.detail} - saved ${result.path}`);
  refreshCalibration();
}

async function autoDetect() {
  const reply = await cmd('auto_calibrate');
  if (!ok(reply, 'auto-detect')) return;
  for (const note of reply.result.notes || []) addLog('debug', '   ' + note);
  addLog(reply.result.confidence >= 0.5 ? 'info' : 'warn', reply.result.message || '');
  await reloadConfig();
}

async function testCast() {
  const reply = await cmd('test_cast');
  if (!ok(reply, 'test cast')) return;
  const { hold, aimed } = reply.result;
  addLog('info', `cast with a ${hold.toFixed(2)}s hold ${aimed ? 'at the cast point' : 'wherever the cursor was (no cast point set)'} - watch where the bobber lands, then change Cast hold in Settings and cast again`);
}

async function craftWizard() {
  const step = (index, title, text) => promptStep('crafting', index, 6, title, text);
  let choice = await step(1, 'the dialogue', 'Stand at Blacksmith Sen and press T so his "are you interested?" dialogue is up. Leave it open, then pick the Yes button.');
  if (choice !== 'ok') return;
  if (!await pickPoints('bait', ['the Yes button'], ['dialog_yes'], 'dialogue')) return;

  choice = await step(2, 'the menu', "Click Yes, then click your bait's row (Rare or Legendary Fish Bait) so a red 0/N counter shows under the + slot. Leave it like that.\n\nYou will pick four points, then drag two boxes.");
  if (choice !== 'ok') return;
  if (!await pickPoints('bait', ["your bait's row", 'the + slot', 'anywhere on the green CRAFT button', 'the red X'],
                        ['craft_recipe', 'craft_add', 'craft_button', 'craft_close'], 'menu')) return;
  if (!await regionInto('menu_region', 'bait', "Drag a box over the whole of Sen's craft menu. It only has to cover most of it.", 'craft menu region set')) return;
  if (!await regionInto('craft_counter_region', 'bait', 'Drag a tight box around the red 0/N counter under the + slot - just the number, not the + itself.', 'material counter set')) return;

  choice = await step(3, 'the fish list', 'Click the + slot once so the list of your eligible fish opens beside the menu (you need at least one fish of that tier). Leave it open.');
  if (choice !== 'ok') return;
  if (!await pickPoints('bait', ['the first fish in the list'], ['craft_pick'], 'fish list')) return;

  let stackSkipped = true;
  choice = await step(4, 'the stack dialog (optional)', 'If you have two or more of the same fish: click the fish in the list, then CRAFT. A dialog with a slider and a green Craft Selected appears - leave it open. Without a stack, press Skip; the Stack dialog button does this later.');
  if (choice === 'cancel') return;
  if (choice === 'ok') {
    if (!await pickPoints('bait', STACK_LABELS, STACK_ATTRS, 'stack dialog')) return;
    stackSkipped = false;
  }

  choice = await step(5, 'closing', 'Close the dialog if it is open, click + to close the fish list if it is open, then click the red X. Sen\'s "..." bubble stays at the bottom of the screen - leave it there.');
  if (choice !== 'ok') return;
  if (!await pickPoints('bait', ['the ... bubble'], ['dialog_end'], 'bubble')) return;

  choice = await step(6, 'the bait row', 'Click the ... bubble to end the conversation, then hold your fishing rod so the Fishing Baits panel shows.');
  if (choice !== 'ok') return;
  if (!await pickPoints('bait', ["your bait's row in the Fishing Baits panel"], ['bait_select'], 'bait row')) return;

  addLog('info', 'crafting calibrated - turn on Auto-craft in Settings' +
    (stackSkipped ? '. Stack dialog skipped: the first stacked fish stops a pass until Stack dialog is calibrated' : ''));
}

async function storageWizard() {
  if (!await ACTIONS.icon()) return;
  if (!await ACTIONS.hotbar()) return;
  if (!await ACTIONS.banner()) return;
  await ACTIONS.store();
}

const ACTIONS = {
  test: testDetection,
  auto: autoDetect,
  drag: () => regionInto('scan_region', null, 'Drag a box around the fishing gauge', 'scan region set'),
  resample: async () => { if (await sampleColour('bar_blue')) await sampleColour('track_gray'); },
  blue: () => sampleColour('bar_blue'),
  black: () => sampleColour('track_gray'),
  test_cast: testCast,
  cast_point: () => pickPoints('fishing', ['a spot on the water where the line should land'], ['cast_point'], 'cast point'),
  craft: craftWizard,
  menu: () => regionInto('menu_region', 'bait', "Drag a box over the whole of Sen's craft menu. It only has to cover most of it.", 'craft menu region set'),
  counter: () => regionInto('craft_counter_region', 'bait', 'Drag a tight box around the N/M counter under the + slot - the red 0/1 that turns green when it is filled. Just the number, not the + itself.', 'material counter set'),
  row: () => pickPoints('bait', ["your bait's row in the Fishing Baits panel (rod held)"], ['bait_select'], 'bait row'),
  stack: () => pickPoints('bait', STACK_LABELS, STACK_ATTRS, 'stack dialog'),
  barrel: () => pickPoints('bait', ["the quantity box in the barrel's dialog", 'Confirm', 'Cancel (optional)'], ['shop_quantity', 'shop_confirm', 'shop_cancel'], 'barrel'),
  badge: () => snipTemplate("Drag a box around the white T key badge on Sen's prompt - just the badge", 'prompt badge', S.config.bait.prompt_template),
  nametag: async () => {
    const region = await snipTemplate('Stand exactly where you fish from, then drag a tight box around Sen\'s floating "Blacksmith Sen" name - just the text', 'nametag', S.config.bait.tag_template);
    if (!region) return;
    const home = [Math.round((region.x1 + region.x2) / 2), Math.round((region.y1 + region.y2) / 2)];
    if (await patchConfig({ bait: { home_tag: home } })) addLog('info', `home at (${home[0]},${home[1]})`);
  },
  storage: storageWizard,
  icon: async () => !!await snipTemplate('Drag a box around the devil fruit icon - just the icon, not the whole slot', 'fruit icon', null),
  hotbar: () => regionInto('hotbar_region', 'fruit', 'Drag across the whole hotbar row. Fruits are found by position inside this box, so the box only has to contain every slot - it does not need to line up with them.', 'hotbar row set'),
  banner: () => regionInto('banner_region', 'fruit', "Drag a box over the top-of-screen banner strip, where 'New Item', 'You can only store one of each fruit!' and Sen's 'no eligible materials' appear", 'banner strip set'),
  store: async () => {
    const label = 'anywhere on the green Store Fruit button';
    const picked = await captureAndPick({ mode: 'points', labels: [label] });
    if (!picked || !picked[label]) return false;
    const [x, y] = picked[label];
    if (!await patchConfig({ fruit: { store_point: [x, y] } })) return false;
    addLog('info', `store button anchor set at (${x},${y}) - the prompt is searched for around it`);
    return true;
  },
};

// ----------------------------------------------------------------- picker

const picker = {
  show({ png, width, height, mode, text, labels }) {
    return new Promise((resolve) => {
      const root = $('picker'), canvas = $('pickCanvas'), stage = root.querySelector('.picker-stage');
      const ctx = canvas.getContext('2d');
      const image = new Image();
      const remaining = mode === 'points' ? [...labels] : [];
      const points = {};
      let scale = 1, anchor = null, dragging = false;

      const caption = () => {
        if (mode === 'points') {
          $('pickText').textContent = remaining.length ? `Click: ${remaining[0]}` : 'done';
          $('pickKeys').textContent = 'right-click skips · Esc stops';
        } else if (mode === 'color') {
          $('pickText').textContent = text;
          $('pickKeys').textContent = 'click a point · Esc cancels';
        } else {
          $('pickText').textContent = text;
          $('pickKeys').textContent = 'drag a box · Esc cancels';
        }
      };
      const draw = () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        ctx.lineWidth = 1;
        for (const [x, y] of Object.values(points)) {
          ctx.strokeStyle = '#4EC98A';
          ctx.beginPath(); ctx.moveTo(x * scale - 8, y * scale); ctx.lineTo(x * scale + 8, y * scale);
          ctx.moveTo(x * scale, y * scale - 8); ctx.lineTo(x * scale, y * scale + 8); ctx.stroke();
        }
      };
      const marquee = (a, b) => {
        draw();
        const x = Math.min(a.x, b.x) * scale, y = Math.min(a.y, b.y) * scale;
        const w = Math.abs(b.x - a.x) * scale, h = Math.abs(b.y - a.y) * scale;
        ctx.fillStyle = 'rgba(78, 201, 138, 0.2)'; ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = '#4EC98A'; ctx.lineWidth = 2; ctx.strokeRect(x, y, w, h);
      };
      const at = (event) => {
        const rect = canvas.getBoundingClientRect();
        const x = Math.round((event.clientX - rect.left) / scale), y = Math.round((event.clientY - rect.top) / scale);
        return { x: Math.max(0, Math.min(width - 1, x)), y: Math.max(0, Math.min(height - 1, y)) };
      };
      const finish = (result) => {
        canvas.onmousedown = canvas.onmousemove = canvas.onmouseup = canvas.oncontextmenu = null;
        document.removeEventListener('keydown', onKey, true);
        root.hidden = true;
        resolve(result);
      };
      const onKey = (event) => {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        finish(mode === 'points' && Object.keys(points).length ? points : null);
      };
      const advance = () => {
        remaining.shift();
        if (!remaining.length) { finish(Object.keys(points).length ? points : null); return; }
        caption();
      };

      image.onload = () => {
        root.hidden = false;
        const avail = stage.getBoundingClientRect();
        scale = Math.min(1, (avail.width - 24) / width, (avail.height - 24) / height);
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.style.width = canvas.width + 'px';
        canvas.style.height = canvas.height + 'px';
        draw();
        caption();
        document.addEventListener('keydown', onKey, true);
        canvas.oncontextmenu = (event) => {
          event.preventDefault();
          if (mode === 'points' && remaining.length) advance();
        };
        canvas.onmousedown = (event) => {
          if (event.button !== 0) return;
          const p = at(event);
          if (mode === 'points') { points[remaining[0]] = [p.x, p.y]; draw(); advance(); return; }
          if (mode === 'color') { finish([p.x, p.y]); return; }
          anchor = p; dragging = true;
        };
        canvas.onmousemove = (event) => { if (dragging) marquee(anchor, at(event)); };
        canvas.onmouseup = (event) => {
          if (!dragging) return;
          dragging = false;
          const p = at(event);
          const region = { x1: Math.min(anchor.x, p.x), y1: Math.min(anchor.y, p.y), x2: Math.max(anchor.x, p.x), y2: Math.max(anchor.y, p.y) };
          finish(region.x2 > region.x1 && region.y2 > region.y1 ? region : null);
        };
      };
      image.onerror = () => { addLog('error', 'could not decode the capture'); finish(null); };
      image.src = 'data:image/png;base64,' + png;
    });
  },
};

function promptStep(wizard, index, total, title, text) {
  return new Promise((resolve) => {
    const root = $('prompt');
    $('promptHeading').textContent = `${wizard.toUpperCase()} · STEP ${index} OF ${total} · ${title.toUpperCase()}`;
    $('promptText').textContent = text + '\n\nSet the game up as described, then Capture and pick: the pick is made on a capture of the game, so you can come back to this window to do it.';
    root.hidden = false;
    const done = (choice) => { root.hidden = true; $('promptOk').onclick = $('promptSkip').onclick = $('promptCancel').onclick = null; resolve(choice); };
    $('promptOk').onclick = () => done('ok');
    $('promptSkip').onclick = () => done('skip');
    $('promptCancel').onclick = () => done('cancel');
  });
}

// ------------------------------------------------------------------ boot

function showScreen(name) {
  for (const item of document.querySelectorAll('.nav-item')) item.classList.toggle('is-current', item.dataset.screen === name);
  for (const screen of document.querySelectorAll('.screen')) screen.classList.toggle('is-current', screen.id === 'screen-' + name);
}

function boot() {
  for (const item of document.querySelectorAll('.nav-item')) item.onclick = () => showScreen(item.dataset.screen);
  $('startBtn').onclick = async () => {
    const reply = await cmd(S.running ? 'stop' : 'start');
    if (!ok(reply, 'start/stop')) return;
    if (reply.result.reason) addLog('warn', `cannot start - ${reply.result.reason}`);
  };
  $('pauseBtn').onclick = () => cmd('pause');
  $('panicBtn').onclick = async () => { const reply = await cmd('panic'); ok(reply, 'panic'); };
  $('recovery').onclick = async () => {
    const tag = $('recovery').dataset.tag;
    if (tag === 'recheck') {
      const reply = await cmd('recheck_window');
      if (!ok(reply, 'recheck')) return;
      addLog(reply.result.found ? 'info' : 'warn', reply.result.found ? 'Roblox window found again - ready to start' : 'still no Roblox window - launch GPO, then recheck');
    } else if (tag === 'calibration') {
      showScreen('calibration');
      const card = S.cards.region;
      card.card.classList.add('is-open');
      card.chevron.textContent = 'v';
    }
  };
  $('filter').oninput = refreshFilter;
  $('showAdvanced').onchange = refreshFilter;
  $('saveBtn').onclick = () => save(false);
  $('resetStatsBtn').onclick = async () => { await cmd('reset_stats'); addLog('info', 'session stats reset'); };
  $('exportBtn').onclick = exportSettings;
  $('importBtn').onclick = () => $('importFile').click();
  $('importFile').onchange = () => { const file = $('importFile').files[0]; if (file) importSettings(file); $('importFile').value = ''; };
  buildCards();
  connect();
}

document.addEventListener('DOMContentLoaded', boot);
