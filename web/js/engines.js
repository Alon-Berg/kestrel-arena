/* Kestrel Arena - Engines tab: the bundled Kestrel, your own UCI engines, the roster import, per-engine UCI options */
(function () {
  const A = window.A;
  const { h } = A;
  let root, tbody, countEl;
  let saveTimer = null, selfUpdate = false;

  A.engineById = (id) => A.state.engines.find((e) => e.id === id);
  A.engineName = (id) => (A.engineById(id) || {}).name || id;

  function persist(now) {
    clearTimeout(saveTimer);
    const go = async () => {
      try {
        const r = await A.post('/api/engines/save', { engines: A.state.engines.map((e) => { const c = Object.assign({}, e); delete c._probe; return c; }) });
        r.engines.forEach((se, i) => { const le = A.state.engines[i]; if (le) { le.id = se.id; le.exists = se.exists; } else A.state.engines.push(se); });
        selfUpdate = true; A.emit('engines', {}); selfUpdate = false;
      } catch (e) { A.toast(e.message, 'err'); }
    };
    if (now) return go();
    saveTimer = setTimeout(go, 500);
  }

  async function probe(e, statusEl) {
    statusEl.textContent = 'checking...'; statusEl.className = 'muted';
    try {
      const r = await A.post('/api/engines/probe', { path: e.path, cwd: e.cwd, args: e.args });
      if (r.ok) { statusEl.textContent = r.id_name || 'ok'; statusEl.className = 'good'; e._probe = r; }
      else { statusEl.textContent = r.error || 'failed'; statusEl.className = 'bad'; }
      return r;
    } catch (err) { statusEl.textContent = err.message; statusEl.className = 'bad'; }
  }

  async function editOptions(e) {
    const dlg = A.modal({ title: 'UCI options - ' + e.name, body: h('div', { class: 'muted' }, 'Reading the engine\'s options...'), width: '760px', buttons: [{ label: 'Close' }] });
    let r = e._probe;
    if (!r) r = await A.post('/api/engines/probe', { path: e.path, cwd: e.cwd, args: e.args });
    const body = dlg.el.querySelector('.body');
    A.clear(body);
    if (!r.ok) { body.appendChild(h('div', { class: 'bad' }, r.error || 'The engine did not answer.')); return; }
    e._probe = r;
    e.options = e.options || {};
    const rows = r.options.filter((o) => o.type !== 'button').map((o) => {
      const cur = e.options[o.name] !== undefined ? e.options[o.name] : o.default;
      let ctl;
      const set = (v) => { if (v === o.default || v === '' || v === undefined) delete e.options[o.name]; else e.options[o.name] = v; persist(); };
      if (o.type === 'check') ctl = h('input', { type: 'checkbox', checked: !!cur, onchange: (ev) => set(ev.target.checked) });
      else if (o.type === 'spin') ctl = h('input', { class: 'input num', type: 'number', value: cur, min: o.min, max: o.max, onchange: (ev) => set(ev.target.value === '' ? undefined : parseInt(ev.target.value, 10)) });
      else if (o.type === 'combo') ctl = h('select', { class: 'select', onchange: (ev) => set(ev.target.value) }, o.vars.map((v) => h('option', { value: v, selected: v === cur }, v)));
      else ctl = h('input', { class: 'input', value: cur === null || cur === undefined ? '' : cur, onchange: (ev) => set(ev.target.value) });
      return h('tr', {}, h('td', {}, o.name), h('td', { class: 'muted' }, o.type + (o.min !== null && o.max !== null ? ' ' + o.min + '..' + o.max : '')), h('td', { class: 'muted mono' }, String(o.default === null ? '' : o.default)), h('td', {}, ctl));
    });
    body.appendChild(h('div', { class: 'help', style: 'margin-bottom:8px' }, 'Values that differ from the default are sent to the engine before every game. Tournament-wide Hash / Threads from the tournament setup are applied first; the values here win.'));
    body.appendChild(h('table', { class: 'tbl' }, h('thead', {}, h('tr', {}, h('th', {}, 'Option'), h('th', {}, 'Type'), h('th', {}, 'Default'), h('th', {}, 'Value'))), h('tbody', {}, rows)));
  }

  async function addEngine() {
    let path;
    try { path = (await A.post('/api/pick_file', { kind: 'exe' })).path; } catch (e) { A.toast(e.message, 'err'); return; }
    if (!path) return;
    const base = path.split(/[\\/]/).pop().replace(/\.exe$/i, '');
    const e = { id: '', name: base, path, cwd: null, options: {}, elo: null, enabled: true };
    A.state.engines.push(e);
    render();
    const r = await A.post('/api/engines/probe', { path });
    if (r.ok && r.id_name) e.name = r.id_name;
    else A.toast('That file did not answer as a UCI engine: ' + (r.error || ''), 'err');
    await persist(true);
    render();
  }

  async function importRoster(folder) {
    try {
      const r = await A.post('/api/engines/import_roster', folder ? { folder } : {});
      A.state.engines = r.engines; A.emit('engines', {}); render();
      A.toast('Roster ' + r.file + ': ' + r.added + ' added, ' + r.updated + ' updated, ' + r.disabled + ' switched off (no longer in the roster), ' + r.skipped + ' skipped.', 'ok');
    } catch (e) {
      if (/Could not find/.test(e.message)) {
        const ok = await A.confirm('Tournament project not found', 'Pick the folder of your tournament project (the one that contains config\\verify_results.json).', 'Choose folder');
        if (ok) { const p = (await A.post('/api/pick_file', { kind: 'folder' })).path; if (p) importRoster(p); }
      } else A.toast(e.message, 'err');
    }
  }

  function row(e) {
    const status = h('span', { class: e.exists === false ? 'bad' : 'muted' }, e.exists === false ? 'file missing' : '');
    const nameIn = h('input', { class: 'input', value: e.name, disabled: e.builtin, onchange: (ev) => { e.name = ev.target.value; persist(); } });
    const eloIn = h('input', { class: 'input num', type: 'number', value: e.elo === null || e.elo === undefined ? '' : e.elo, placeholder: 'Elo', onchange: (ev) => { e.elo = ev.target.value === '' ? null : parseInt(ev.target.value, 10); persist(); } });
    const nopt = Object.keys(e.options || {}).length;
    return h('tr', {},
      h('td', { style: 'min-width:230px' }, h('div', { class: 'row', style: 'gap:6px;flex-wrap:nowrap' }, nameIn, e.builtin ? h('span', { class: 'badge acc' }, 'bundled') : null)),
      h('td', {}, eloIn),
      h('td', { class: 'mono muted ellipsis', style: 'max-width:300px', title: e.path }, e.path),
      h('td', {}, e.builtin ? '' : h('input', { class: 'input', style: 'min-width:130px', value: e.args || '', placeholder: 'arguments', title: 'Command line arguments (e.g. -jar engine.jar)', onchange: (ev) => { e.args = ev.target.value; persist(); } })),
      h('td', {}, h('button', { class: 'btn sm', onclick: () => editOptions(e) }, 'Options', nopt ? h('span', { class: 'badge acc' }, String(nopt)) : null)),
      h('td', {}, status),
      h('td', { class: 'right nowrap' },
        h('button', { class: 'btn sm', onclick: () => probe(e, status) }, 'Test'), ' ',
        e.builtin ? null : h('button', { class: 'btn sm danger', onclick: async () => { if (await A.confirm('Remove engine', 'Remove ' + e.name + ' from the list? The file itself stays on disk.', 'Remove', true)) { A.state.engines = A.state.engines.filter((x) => x !== e); await persist(true); render(); } } }, 'Remove')));
  }

  function render() {
    A.clear(tbody);
    A.state.engines.forEach((e) => tbody.appendChild(row(e)));
    countEl.textContent = A.state.engines.length + ' engines';
  }

  A.register({
    id: 'engines', title: 'Engines',
    init(el) {
      root = el;
      tbody = h('tbody');
      countEl = h('span', { class: 'muted' });
      el.appendChild(h('div', { style: 'max-width:1280px;margin:0 auto' },
        h('div', { class: 'card' },
          h('div', { class: 'h' }, 'Engines', countEl, h('span', { class: 'spacer' }),
            h('button', { class: 'btn sm', onclick: addEngine }, '+ Add engine...'),
            h('button', { class: 'btn sm', onclick: () => importRoster() }, 'Import tournament roster')),
          h('div', { class: 'scroll', style: 'max-height:calc(100vh - 230px)' }, h('table', { class: 'tbl' },
            h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', {}, 'Elo'), h('th', {}, 'File'), h('th', {}, 'Arguments'), h('th', {}, 'UCI options'), h('th', {}, 'Status'), h('th', {}, ''))), tbody)),
          h('div', { class: 'help', style: 'margin-top:10px' }, 'Elo is optional: it seeds Swiss and knockout tournaments and anchors the rating column of the standings. Kestrel is bundled with the app (the build that fits your CPU is chosen automatically).'))));
      render();
      A.on('engines', () => { if (!selfUpdate) render(); });
    },
  });
})();
