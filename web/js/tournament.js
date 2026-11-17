/* Kestrel Arena - Tournament tab: setup, live run view (standings, crosstable, bracket, live boards), game viewer */
(function () {
  const A = window.A;
  const { h, svg, fmt } = A;

  // ======================================================================================================== state
  const DEFAULT_S = {
    name: '', format: 'roundrobin', games: 2, rounds: 1, hero: '', swiss_rounds: 5, ko_games: 2, seeding: 'rating', losses: 2, showOff: false, wideBracket: true,
    tcMode: 'time', base: 10, inc: 0.1, movetime: 100, nodes: 20000, depth: 10,
    hash: 64, threads: 1, concurrency: 2, margin_ms: 600, overhead_ms: 'auto', priority: 'above', seed: 1, v: 2,
    openings: { source: 'builtin', path: '', order: 'random' },
    adj: { draw: false, draw_after_move: 40, draw_moves: 8, draw_cp: 10, resign: false, resign_moves: 4, resign_cp: 800, max_plies: 700 },
    selected: [],
  };
  const S = JSON.parse(JSON.stringify(DEFAULT_S));
  const T = { snap: null, games: [], active: new Map(), names: {}, sel: null, view: null, ply: 0, tab: 'standings', structure: null, filter: '', t0: 0, elapsed0: 0, evSeq: 0 };
  let root, setupEl, runEl, inRun = false;
  const FORMATS = { roundrobin: 'Round robin', gauntlet: 'Gauntlet', swiss: 'Swiss', knockout: 'Knockout', elimination: 'Elimination (N losses)' };
  const fmtName = (format, losses) => (format === 'elimination' && losses ? 'Elimination (' + losses + (losses === 1 ? ' loss' : ' losses') + ')' : FORMATS[format] || format);
  const isBracket = (format) => format === 'knockout' || format === 'elimination';          // formats with a bracket (the number of games is only known at the end: tie-breaks, the champion's losses)

  const saveS = A.throttle(() => A.setPref('t_setup', S), 800);

  function tcLabel(tc) {
    if (!tc) return '';
    if (tc.mode === 'time') return tc.base + '+' + tc.inc;
    if (tc.mode === 'movetime') return tc.ms + ' ms/move';
    if (tc.mode === 'nodes') return fmt.num(tc.n) + ' nodes/move';
    return 'depth ' + tc.d;
  }
  function currentTc() {
    if (S.tcMode === 'time') return { mode: 'time', base: +S.base, inc: +S.inc };
    if (S.tcMode === 'movetime') return { mode: 'movetime', ms: +S.movetime };
    if (S.tcMode === 'nodes') return { mode: 'nodes', n: +S.nodes };
    return { mode: 'depth', d: +S.depth };
  }
  function gameCount(n) {
    if (S.format === 'roundrobin') return n * (n - 1) / 2 * S.games * S.rounds;
    if (S.format === 'gauntlet') return (n - 1) * S.games * S.rounds;
    if (S.format === 'swiss') return S.swiss_rounds * Math.floor(n / 2) * S.games;
    const perMatch = S.ko_games % 2 ? S.ko_games + 1 : S.ko_games;
    if (S.format === 'elimination') {          // bracket k has n-1-k matches, then the champions of the min(losses, n) brackets play one match less than that (tie-breaks come on top)
      const L = Math.max(1, Math.min(10, Math.round(S.losses) || 1)), K = Math.min(L, n);
      return (K * (n - 1) - K * (K - 1) / 2 + K - 1) * perMatch;
    }
    return (n - 1) * perMatch;
  }
  function gameSeconds(tc) {           // rough average wall time of one game
    if (tc.mode === 'time') return ((S.adj.draw || S.adj.resign) ? 0.55 : 0.7) * 2 * (tc.base + 40 * tc.inc);
    if (tc.mode === 'movetime') return 120 * tc.ms / 1000;
    if (tc.mode === 'nodes') return 120 * tc.n / 900000 + 1;
    return 2 + tc.d * 0.6;
  }

  // ======================================================================================================== setup view
  let partBody, summaryEl, savedBody, searchIn, dynamic;

  function field(label, ...ctl) { return h('div', { class: 'field' }, h('label', {}, label), h('div', { class: 'inline' }, ...ctl)); }
  function num(key, o, after) {
    o = o || {};
    return h('input', { class: 'input num', type: 'number', min: o.min, max: o.max, step: o.step || 1, value: o.get ? o.get() : S[key],
      oninput: (e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) { if (o.set) o.set(v); else S[key] = v; saveS(); refreshSummary(); if (after) after(); } } });
  }
  function sel(key, opts, after, target) {
    const obj = target || S;
    return h('select', { class: 'select', style: 'width:auto;min-width:150px', onchange: (e) => { obj[key] = e.target.value; saveS(); refreshSummary(); refreshDynamic(); if (after) after(); } },
      opts.map(([v, l]) => h('option', { value: v, selected: obj[key] === v }, l)));
  }
  function chk(obj, key, label, after) {
    return h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { obj[key] = e.target.checked; saveS(); if (after) after(); } }), label);
  }

  function buildSetup() {
    const presets = [['1+0.01', 'time', 1, 0.01], ['3+0.03', 'time', 3, 0.03], ['10+0.1', 'time', 10, 0.1], ['20+0.5', 'time', 20, 0.5], ['60+0.6', 'time', 60, 0.6]];
    const tcBox = h('div');
    const tcChips = h('div', { class: 'row', style: 'gap:6px;margin-bottom:6px' });
    function renderTc() {
      A.clear(tcChips); A.clear(tcBox);
      presets.forEach(([l, m, b, i]) => tcChips.appendChild(h('span', { class: 'chip' + (S.tcMode === 'time' && S.base === b && S.inc === i ? ' on' : ''), onclick: () => { S.tcMode = 'time'; S.base = b; S.inc = i; saveS(); renderTc(); refreshSummary(); } }, l)));
      [['movetime', '100 ms/move', 'movetime', 100], ['nodes', '20k nodes', 'nodes', 20000], ['depth', 'depth 10', 'depth', 10]].forEach(([m, l, k, v]) =>
        tcChips.appendChild(h('span', { class: 'chip' + (S.tcMode === m && S[k] === v ? ' on' : ''), onclick: () => { S.tcMode = m; S[k] = v; saveS(); renderTc(); refreshSummary(); } }, l)));
      tcBox.appendChild(field('Time control', sel('tcMode', [['time', 'Clock (base + increment)'], ['movetime', 'Fixed time per move'], ['nodes', 'Fixed nodes per move'], ['depth', 'Fixed depth']], renderTc)));
      if (S.tcMode === 'time') tcBox.appendChild(field('Base / increment', num('base', { min: 0.1, step: 0.1 }), h('span', { class: 'muted' }, 's +'), num('inc', { min: 0, step: 0.01 }), h('span', { class: 'muted' }, 's per move')));
      if (S.tcMode === 'movetime') tcBox.appendChild(field('Time per move', num('movetime', { min: 5 }), h('span', { class: 'muted' }, 'ms')));
      if (S.tcMode === 'nodes') tcBox.appendChild(field('Nodes per move', num('nodes', { min: 100, step: 1000 })));
      if (S.tcMode === 'depth') tcBox.appendChild(field('Depth', num('depth', { min: 1, max: 60 })));
    }
    dynamic = h('div');
    const adjBox = h('div');
    function renderAdj() {
      A.clear(adjBox);
      adjBox.appendChild(field('Draw adjudication', chk(S.adj, 'draw', 'on', renderAdj)));
      if (S.adj.draw) adjBox.appendChild(field('', h('span', { class: 'muted' }, 'after move'), num(null, { get: () => S.adj.draw_after_move, set: (v) => (S.adj.draw_after_move = v) }), h('span', { class: 'muted' }, 'when both |eval| <='), num(null, { get: () => S.adj.draw_cp, set: (v) => (S.adj.draw_cp = v) }), h('span', { class: 'muted' }, 'cp for'), num(null, { get: () => S.adj.draw_moves, set: (v) => (S.adj.draw_moves = v) }), h('span', { class: 'muted' }, 'moves')));
      adjBox.appendChild(field('Win adjudication', chk(S.adj, 'resign', 'on', renderAdj)));
      if (S.adj.resign) adjBox.appendChild(field('', h('span', { class: 'muted' }, 'when both engines show'), num(null, { get: () => S.adj.resign_cp, set: (v) => (S.adj.resign_cp = v) }), h('span', { class: 'muted' }, 'cp for'), num(null, { get: () => S.adj.resign_moves, set: (v) => (S.adj.resign_moves = v) }), h('span', { class: 'muted' }, 'moves')));
    }
    const openFile = h('span', { class: 'muted ellipsis', style: 'max-width:220px' });
    const openBox = h('div');
    function renderOpen() {
      A.clear(openBox);
      openBox.appendChild(field('Openings', sel('source', [['builtin', 'Built-in balanced suite'], ['file', 'EPD / PGN file...'], ['none', 'Start position only']], renderOpen, S.openings)));
      if (S.openings.source === 'file') {
        openFile.textContent = S.openings.path || 'no file chosen';
        openBox.appendChild(field('File', h('button', { class: 'btn sm', onclick: async () => { const p = (await A.post('/api/pick_file', { kind: 'epd' })).path; if (p) { S.openings.path = p; saveS(); renderOpen(); } } }, 'Choose...'), openFile));
      }
      if (S.openings.source !== 'none') openBox.appendChild(field('Order', sel('order', [['random', 'Random (seeded)'], ['sequential', 'In file order']], null, S.openings), h('span', { class: 'muted' }, 'seed'), num('seed', { min: 0 })));
    }
    function refreshDyn() {
      A.clear(dynamic);
      if (S.format === 'roundrobin') { dynamic.appendChild(field('Games per pairing', num('games', { min: 1, max: 100 }), h('span', { class: 'help' }, '2 = both colours'))); dynamic.appendChild(field('Rounds', num('rounds', { min: 1, max: 50 }), h('span', { class: 'help' }, 'times the whole round robin'))); }
      if (S.format === 'gauntlet') {
        dynamic.appendChild(field('Hero engine', h('select', { class: 'select', style: 'width:auto;min-width:200px', onchange: (e) => { S.hero = e.target.value; saveS(); } }, selectedEngines().map((e) => h('option', { value: e.id, selected: S.hero === e.id }, e.name)))));
        dynamic.appendChild(field('Games per opponent', num('games', { min: 1, max: 1000 })));
        dynamic.appendChild(field('Rounds', num('rounds', { min: 1, max: 50 })));
      }
      if (S.format === 'swiss') { dynamic.appendChild(field('Rounds', num('swiss_rounds', { min: 1, max: 50 }))); dynamic.appendChild(field('Games per pairing', num('games', { min: 1, max: 100 }))); }
      if (S.format === 'elimination') dynamic.appendChild(field('Losses to be out', num('losses', { min: 1, max: 10 }), h('span', { class: 'help' }, '1 = knockout, 2 = double elimination (winners and losers bracket), 3 = triple, ...')));
      if (isBracket(S.format)) { dynamic.appendChild(field('Games per match', num('ko_games', { min: 2, max: 1000, step: 2 }), h('span', { class: 'help' }, 'ties are broken by extra pairs of games'))); dynamic.appendChild(field('Seeding', sel('seeding', [['rating', 'By Elo (best first)'], ['list', 'As listed'], ['random', 'Random']]))); }
    }
    refreshDynamic = refreshDyn;

    const nameIn = h('input', { class: 'input', placeholder: 'Tournament name (optional)', value: S.name, oninput: (e) => { S.name = e.target.value; saveS(); } });
    const left = h('div', {},
      h('div', { class: 'card' }, h('h3', {}, 'Tournament'), field('Name', nameIn),
        field('Format', sel('format', Object.entries(FORMATS), () => { refreshDyn(); })), dynamic,
        h('div', { class: 'hr' }), tcChips, tcBox),
      h('div', { class: 'card' }, h('h3', {}, 'Engines and games'),
        field('Hash per engine', num('hash', { min: 1, max: 65536 }), h('span', { class: 'muted' }, 'MB')),
        field('Threads per engine', num('threads', { min: 1, max: 128 })),
        field('Parallel games', num('concurrency', { min: 1, max: 64 }), h('span', { class: 'help', id: 'cc-help' })),
        field('Time margin', num('margin_ms', { min: 0, max: 10000, step: 50 }), h('span', { class: 'muted' }, 'ms before a flag falls')),
        field('Engine time overhead', sel('overhead_ms', [['auto', 'Automatic (5 ms when games are very fast)'], ['', "Engines' own setting"], ['0', '0 ms'], ['1', '1 ms'], ['5', '5 ms'], ['10', '10 ms'], ['20', '20 ms']]),
          h('span', { class: 'help' }, 'turns down the "Move Overhead" of the engines that have one (many keep 50 to 250 ms for themselves, which is too much for games of a second or two); automatic = clocks up to 3 s')),
        field('Process priority', sel('priority', [['above', 'Above normal (recommended)'], ['normal', 'Normal']]), h('span', { class: 'help' }, 'keeps background programs from making moves late')),
        h('div', { class: 'hr' }), openBox, h('div', { class: 'hr' }), adjBox));
    renderTc(); renderAdj(); renderOpen(); refreshDyn();

    // participants
    partBody = h('div', { class: 'scroll', style: 'max-height:calc(100vh - 400px);min-height:160px' });
    searchIn = h('input', { class: 'input', placeholder: 'Filter engines...', style: 'max-width:220px', oninput: renderParticipants });
    summaryEl = h('div', { class: 'stat-row' });
    savedBody = h('div');
    const quick = (label, fn) => h('button', { class: 'btn sm', onclick: () => { fn(); saveS(); renderParticipants(); refreshSummary(); refreshDyn(); } }, label);
    const right = h('div', {},
      h('div', { class: 'card' },
        h('div', { class: 'h' }, 'Participants', h('span', { class: 'spacer' }), chk(S, 'showOff', 'show switched-off engines', () => renderParticipants()), searchIn, quick('All', () => (S.selected = usable().map((e) => e.id))), quick('None', () => (S.selected = [])),
          quick('Kestrel + 7 nearest', () => nearKestrel(8)), quick('Top 8 by Elo', () => topN(8)), quick('Top 16', () => topN(16))),
        partBody,
        h('div', { class: 'hr' }), summaryEl,
        h('div', { class: 'row', style: 'margin-top:12px' }, h('button', { class: 'btn primary lg', id: 'start-btn', onclick: startTournament }, 'Start tournament'),
          h('button', { class: 'btn', id: 'back-btn', onclick: () => showRun(true) }, 'Back to running tournament'))),
      h('div', { class: 'card' }, h('h3', {}, 'Saved tournaments'), savedBody));
    setupEl = h('div', { class: 'grid-setup', style: 'max-width:1320px;margin:0 auto' }, left, right);
    renderParticipants(); refreshSummary(); renderSaved();
  }
  let refreshDynamic = () => {};

  const usable = () => A.state.engines.filter((e) => e.enabled !== false);            // engines that are switched off (Engines tab) are left out of the quick picks
  function topN(n) {
    S.selected = usable().filter((e) => e.elo).sort((a, b) => b.elo - a.elo).slice(0, n).map((e) => e.id);
    if (S.selected.length < 2) S.selected = usable().slice(0, n).map((e) => e.id);
  }
  function nearKestrel(n) {                         // Kestrel plus the engines closest to it in Elo
    const k = usable().find((e) => e.builtin && e.elo) || usable().find((e) => /kestrel/i.test(e.name) && e.elo);
    if (!k) { topN(n); return; }
    const others = usable().filter((e) => e !== k && e.elo).sort((a, b) => Math.abs(a.elo - k.elo) - Math.abs(b.elo - k.elo)).slice(0, n - 1);
    S.selected = [k.id].concat(others.map((e) => e.id));
  }
  const selectedEngines = () => S.selected.map((id) => A.engineById(id)).filter(Boolean);

  function renderParticipants() {
    A.clear(partBody);
    const q = (searchIn.value || '').toLowerCase();
    const set = new Set(S.selected);
    const list = A.state.engines.filter((e) => (!q || e.name.toLowerCase().includes(q)) && (S.showOff || e.enabled !== false || set.has(e.id)));
    if (!list.length) { partBody.appendChild(h('div', { class: 'empty' }, h('b', {}, 'No engines'), 'Add engines in the Engines tab.')); return; }
    if (A.state.engines.length < 2 && !q) {
      partBody.appendChild(h('div', { class: 'card', style: 'background:var(--panel2);margin-bottom:10px' }, h('b', {}, 'A tournament needs at least two engines.'),
        h('div', { class: 'muted', style: 'margin:4px 0 8px' }, 'Only the bundled Kestrel is in your list. Add engines in the Engines tab, or import the engines of your tournament project.'),
        h('div', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: () => A.showTab('engines') }, 'Open the Engines tab'))));
    }
    const rows = list.map((e) => h('tr', { onclick: (ev) => { if (ev.target.tagName === 'INPUT') return; toggle(e.id); } },
      h('td', { style: 'width:28px' }, h('input', { type: 'checkbox', checked: set.has(e.id), onchange: () => toggle(e.id) })),
      h('td', {}, e.name, e.builtin ? h('span', { class: 'badge acc', style: 'margin-left:8px' }, 'bundled') : null, e.enabled === false ? h('span', { class: 'badge', style: 'margin-inline-start:8px' }, 'off') : null),
      h('td', { class: 'right mono muted' }, e.elo || ''),
      h('td', { class: 'muted' }, e.exists === false ? h('span', { class: 'bad' }, 'missing') : '')));
    partBody.appendChild(h('table', { class: 'tbl click' }, h('tbody', {}, rows)));
  }
  function toggle(id) {
    const i = S.selected.indexOf(id);
    if (i >= 0) S.selected.splice(i, 1); else S.selected.push(id);
    saveS(); renderParticipants(); refreshSummary(); refreshDynamic();
  }

  function refreshSummary() {
    if (!summaryEl) return;
    const n = S.selected.filter((id) => A.engineById(id)).length;
    const tc = currentTc();
    const games = n >= 2 ? gameCount(n) : 0;
    const secs = games * gameSeconds(tc) / Math.max(1, S.concurrency);
    A.clear(summaryEl);
    summaryEl.style.cssText = 'display:flex;gap:28px;flex-wrap:wrap';
    summaryEl.appendChild(h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, 'engines')));
    summaryEl.appendChild(h('div', { class: 'stat' }, h('b', {}, games ? (isBracket(S.format) ? '~' : '') + games.toLocaleString() : '-'), h('span', {}, 'games')));
    summaryEl.appendChild(h('div', { class: 'stat' }, h('b', {}, games ? '~' + fmt.dur(secs) : '-'), h('span', {}, 'estimated time')));
    const over = S.concurrency * S.threads > (A.state.cpu.logical || 8);
    if (over) summaryEl.appendChild(h('div', { class: 'warn', style: 'align-self:center;max-width:300px' }, 'Parallel games x threads is more than the ' + A.state.cpu.logical + ' logical CPUs: time-based games will be unfair.'));
    const sb = A.$('#start-btn'); if (sb) sb.disabled = n < 2;
  }

  function renderSaved() {
    if (!savedBody) return;
    A.clear(savedBody);
    const list = A.state.tournaments || [];
    if (!list.length) { savedBody.appendChild(h('div', { class: 'muted' }, 'Finished and stopped tournaments are kept here so you can look at them again or continue them.')); return; }
    savedBody.appendChild(h('div', { class: 'scroll h300' }, h('table', { class: 'tbl' }, h('tbody', {}, list.map((t) => h('tr', {},
      h('td', {}, h('b', {}, t.name || t.id), h('div', { class: 'muted' }, fmtName(t.format, t.losses) + ' - ' + t.engines + ' engines - ' + tcLabel(t.tc))),
      h('td', {}, h('span', { class: 'badge ' + (t.status === 'finished' ? 'good' : t.status === 'running' ? 'acc' : '') }, t.status), h('div', { class: 'muted' }, t.games + ' games')),
      h('td', { class: 'right nowrap' },
        h('button', { class: 'btn sm', onclick: () => openTournament(t.id) }, t.status === 'finished' ? 'Open' : 'Open / resume'), ' ',
        h('button', { class: 'btn sm danger', onclick: async () => { if (await A.confirm('Delete tournament', 'Delete "' + (t.name || t.id) + '" with all its games?', 'Delete', true)) { try { const r = await A.post('/api/tournament/' + t.id + '/delete'); A.state.tournaments = r.tournaments; renderSaved(); } catch (e) { A.toast(e.message, 'err'); } } } }, 'Delete'))))))));
  }

  async function startTournament() {
    const ids = S.selected.filter((id) => A.engineById(id));
    if (ids.length < 2) { A.toast('Pick at least two engines.', 'err'); return; }
    if (S.format === 'gauntlet' && !ids.includes(S.hero)) S.hero = ids[0];
    const wait = A.modal({ title: 'Checking engines', body: h('div', {}, 'Starting each engine once to make sure it speaks UCI (' + ids.length + ' engines)...'), dismiss: false, buttons: [] });
    let res;
    try { res = (await A.post('/api/engines/check', { ids })).results; } catch (e) { wait.close(); A.toast(e.message, 'err'); return; }
    wait.close();
    let use = ids;
    const bad = res.filter((r) => !r.ok);
    if (bad.length) {
      const remaining = ids.filter((id) => !bad.some((b) => b.id === id));
      const go = await new Promise((resolve) => A.modal({ title: bad.length + ' engine(s) did not start', width: '640px',
        body: h('div', {}, h('table', { class: 'tbl' }, h('tbody', {}, bad.map((b) => h('tr', {}, h('td', {}, b.name || b.id), h('td', { class: 'bad' }, b.error || 'no answer'))))),
          remaining.length < 2 ? h('div', { class: 'bad', style: 'margin-top:10px' }, 'Fewer than two working engines are left.') : null),
        buttons: [{ label: 'Cancel', onClick: () => resolve(false) }].concat(remaining.length >= 2 ? [{ label: 'Continue without them', cls: 'primary', onClick: () => resolve(true) }] : []) }));
      if (!go) return;
      use = remaining;
    }
    try {
      const snap = await A.post('/api/tournament/create', {
        name: S.name, format: S.format, engines: use, games: S.games, rounds: S.rounds, hero: S.hero, swiss_rounds: S.swiss_rounds, ko_games: S.ko_games, seeding: S.seeding, losses: S.losses,
        tc: currentTc(), hash: S.hash, threads: S.threads, concurrency: S.concurrency, margin_ms: S.margin_ms, overhead_ms: S.overhead_ms === 'auto' || S.overhead_ms === undefined ? 'auto' : (S.overhead_ms === '' ? null : +S.overhead_ms), priority: S.priority, openings: S.openings, adj: S.adj, seed: S.seed, start: true });
      loadSnapshot(snap);
      showRun();
      A.get('/api/tournaments').then((r) => { A.state.tournaments = r.tournaments; renderSaved(); });
    } catch (e) { A.toast(e.message, 'err'); }
  }

  async function openTournament(id) {
    try { loadSnapshot(await A.get('/api/tournament/' + id)); showRun(); } catch (e) { A.toast(e.message, 'err'); }
  }

  // ======================================================================================================== run view
  let hdr = {}, standEl, liveEl, viewerEl, listEl, standBody, tabBtns, cards = new Map(), viewerBoard = null, listSel;

  function loadSnapshot(snap) {
    T.snap = snap; T.games = snap.games.slice(); T.names = {};
    for (const id of snap.cfg.engine_ids) T.names[id] = snap.cfg.engines[id].name;
    T.active = new Map(snap.active.map((g) => [g.gid, Object.assign(g, { _t: performance.now() })]));
    T.structure = snap.structure; T.sel = null; T.view = null; T.tab = isBracket(snap.format) ? 'bracket' : 'standings'; T.filter = '';
    T.elapsed0 = snap.elapsed; T.t0 = performance.now();
    buildRun();
    refreshAll();
  }

  function buildRun() {
    const cfg = T.snap.cfg;
    hdr.title = h('div', { class: 'grow' }, h('b', { style: 'font-size:17px' }, cfg.name), h('div', { class: 'muted', id: 'run-sub' }), h('div', { class: 'bad', id: 'run-err' }));
    hdr.status = h('span', { class: 'badge' });
    hdr.prog = h('div', { class: 'progress', style: 'width:100%' }, h('i'));
    hdr.progText = h('span', { class: 'mono' });
    hdr.time = h('span', { class: 'muted mono' });
    hdr.btns = h('div', { class: 'row', style: 'gap:6px' });
    const head = h('div', { class: 'card' },
      h('div', { class: 'row', style: 'flex-wrap:nowrap;gap:14px' }, hdr.title, hdr.status, hdr.btns),
      h('div', { class: 'row', style: 'flex-wrap:nowrap;gap:12px;margin-top:10px' }, h('div', { style: 'flex:1' }, hdr.prog), hdr.progText, hdr.time));
    standBody = h('div', { class: 'scroll', style: 'max-height:calc(100vh - 330px)' });
    tabBtns = h('div', { class: 'seg' });
    hdr.wide = h('span', { class: 'hidden' }, chk(S, 'wideBracket', 'full width', () => { applyWide(); renderStandings(); }));
    standEl = h('div', { class: 'card' }, h('div', { class: 'h' }, tabBtns, h('span', { class: 'spacer' }), hdr.wide), standBody);
    liveEl = h('div', { class: 'card' });
    viewerEl = h('div', { class: 'card hidden' });
    listSel = h('select', { class: 'select', style: 'width:auto;min-width:150px', onchange: (e) => { T.filter = e.target.value; renderList(); } });
    listEl = h('div', { class: 'card' });
    hdr.grid = h('div', { class: 'grid-run', style: 'margin-top:14px' }, h('div', {}, standEl), h('div', {}, liveEl, viewerEl, listEl));
    runEl = h('div', { style: 'max-width:1500px;margin:0 auto' }, head, hdr.grid);
    cards = new Map(); viewerBoard = null;
  }

  const refreshStanding = A.throttle(() => { renderStandings(); renderList(); }, 450);
  const refreshHeaderT = A.throttle(() => renderHeader(), 250);
  function refreshAll() { renderHeader(); renderTabs(); renderStandings(); renderLive(); renderViewer(); renderList(); }

  function elapsedNow() { return T.elapsed0 + (T.snap && T.snap.status === 'running' ? (performance.now() - T.t0) / 1000 : 0); }

  function renderHeader() {
    const s = T.snap, cfg = s.cfg;
    const done = T.games.length, total = s.total;
    A.$('#run-sub', runEl).textContent = fmtName(cfg.format, (cfg.params || {}).losses) + ' - ' + cfg.engine_ids.length + ' engines - ' + tcLabel(cfg.tc) + ' - ' + cfg.concurrency + ' parallel game' + (cfg.concurrency > 1 ? 's' : '') + ' - openings: ' + s.book;
    const st = s.status;
    hdr.status.textContent = { running: 'Running', paused: 'Paused', stopping: 'Stopping...', stopped: 'Stopped', finished: 'Finished', error: 'Error', ready: 'Ready' }[st] || st;
    hdr.status.className = 'badge ' + (st === 'running' ? 'good' : st === 'finished' ? 'acc' : st === 'error' ? 'bad' : st === 'paused' ? 'warn' : '');
    let shownTotal = total;
    const approx = isBracket(cfg.format) && st !== 'finished';          // tie-break games are added while a knockout runs
    if (done > total && !approx) shownTotal = done;
    if (approx && done >= total) shownTotal = done + Math.max(1, T.active.size);
    const pct = shownTotal ? Math.min(approx ? 99 : 100, 100 * done / shownTotal) : 0;
    hdr.prog.firstChild.style.width = pct + '%';
    hdr.progText.textContent = done + ' / ' + (approx ? '~' : '') + shownTotal + ' games (' + pct.toFixed(0) + '%)';
    const el = elapsedNow();
    let eta = '';
    if (st === 'running' && done >= 3 && done < shownTotal) eta = ' - about ' + fmt.dur(el / done * (shownTotal - done)) + ' left';
    hdr.time.textContent = fmt.dur(el) + eta;
    A.clear(hdr.btns);
    const b = (label, cls, fn) => hdr.btns.appendChild(h('button', { class: 'btn sm ' + cls, onclick: fn }, label));
    if (st === 'running') { b('Pause', '', () => act('pause')); b('Stop', 'danger', () => act('stop')); }
    if (st === 'paused') { b('Resume', 'primary', () => act('resume')); b('Stop', 'danger', () => act('stop')); }
    if (st === 'stopped' || st === 'ready' || st === 'error') b('Continue', 'primary', () => act('start'));
    hdr.btns.appendChild(h('a', { class: 'btn sm', href: A.url('/api/tournament/' + s.id + '/pgn'), download: '' }, 'PGN'));
    b('Export CSV', '', exportCsv);
    b('Setup', '', () => showSetup());
    const err = A.$('#run-err', runEl);
    if (err) {            // the message of the program (a missing engine file ...) or, for an internal error, the last line of its report
      const lines = (s.error || '').trim().split('\n');
      err.textContent = s.error ? (lines.length > 1 ? 'Internal error: ' : '') + lines[lines.length - 1].slice(0, 300) : '';
      err.title = s.error || '';
    }
  }

  async function act(a) {
    const seq = T.evSeq;          // a tournament that stops at once (a missing engine file) or ends at once has sent its events before this answer arrives: the answer is older
    try { const r = await A.post('/api/tournament/' + T.snap.id + '/' + a); if (T.evSeq === seq) T.snap.status = r.status; if (a === 'start') { T.t0 = performance.now(); } renderHeader(); }
    catch (e) { A.toast(e.message, 'err'); }
  }

  function currentStandings() {
    const cfg = T.snap.cfg;
    const engines = cfg.engine_ids.map((id) => ({ id, name: cfg.engines[id].name, elo: cfg.engines[id].elo }));
    const extra = {};
    const st = T.structure;
    if (cfg.format === 'swiss' && st && st.byes) for (const b in st.byes) extra[st.byes[b]] = (extra[st.byes[b]] || 0) + st.bye_points;
    return A.elo.standings(engines, T.games, { extraPoints: extra });
  }

  function renderTabs() {
    A.clear(tabBtns);
    const tabs = [['standings', 'Standings'], ['crosstable', 'Crosstable']];
    if (isBracket(T.snap.format)) tabs.unshift(['bracket', 'Bracket']);
    for (const [id, l] of tabs) tabBtns.appendChild(h('button', { class: T.tab === id ? 'on' : '', onclick: () => { T.tab = id; renderTabs(); renderStandings(); } }, l));
  }

  function renderStandings() {
    A.clear(standBody);
    applyWide();
    if (T.tab === 'bracket') return renderBracket();
    const res = currentStandings();
    if (T.tab === 'crosstable') return renderCross(res);
    const el = elimInfo();
    if (el) orderElimination(res, el);
    const anchored = T.snap.cfg.engine_ids.every((id) => T.snap.cfg.engines[id].elo);
    const rows = res.rows.map((r) => h('tr', {},
      h('td', { class: 'right muted' }, r.rank),
      h('td', { style: 'max-width:200px', class: 'ellipsis', title: r.name }, r.name),
      el ? h('td', { class: 'right mono' }, el.st.loss[r.id] + ' / ' + el.losses) : null, el ? h('td', {}, elimStatus(el, r)) : null,
      h('td', { class: 'right mono' }, r.games ? Math.round(r.elo) : ''),
      h('td', { class: 'right mono muted' }, r.err ? '±' + Math.round(r.err) : ''),
      h('td', { class: 'right mono', style: 'font-weight:700' }, fmt.pts(r.pts)),
      h('td', { class: 'right mono muted' }, r.games),
      h('td', { class: 'right mono' }, h('span', { class: 'good' }, r.w), h('span', { class: 'muted' }, ' / '), r.d, h('span', { class: 'muted' }, ' / '), h('span', { class: 'bad' }, r.l)),
      h('td', { class: 'right mono' }, r.score === null ? '' : fmt.pct(r.score, 1)),
      h('td', { class: 'right mono muted' }, fmt.pts(r.sb))));
    standBody.appendChild(h('table', { class: 'tbl' }, h('thead', {}, h('tr', {}, h('th', { class: 'r' }, '#'), h('th', {}, 'Engine'), el ? h('th', { class: 'r', title: 'Matches lost / matches a player may lose' }, 'Losses') : null, el ? h('th', {}, 'Status') : null, h('th', { class: 'r', title: anchored ? 'Rating fitted to the results, anchored to the engines\' Elo values' : 'Rating difference fitted to the results (average = 0)' }, anchored ? 'Elo' : 'Elo diff'), h('th', { class: 'r', title: '95% confidence' }, '±'), h('th', { class: 'r' }, 'Pts'), h('th', { class: 'r' }, 'Games'), h('th', { class: 'r' }, 'W / D / L'), h('th', { class: 'r' }, 'Score'), h('th', { class: 'r', title: 'Sonneborn-Berger tie-break' }, 'SB'))), h('tbody', {}, rows)));
    if (!T.games.length) standBody.appendChild(h('div', { class: 'help', style: 'padding:8px' }, 'Results appear here as games finish.'));
  }

  function renderCross(res) {
    const n = res.rows.length;
    if (n > 60) { standBody.appendChild(h('div', { class: 'empty' }, h('b', {}, 'Too many engines for a matrix'), 'Use the standings table (or export the CSV).')); return; }
    const order = res.rows.map((r) => res.idx.get(r.id));
    const names = T.snap.cfg.engine_ids.map((id) => T.names[id]);
    const head = h('tr', {}, h('th', {}, ''), order.map((j, k) => h('th', { title: names[j], class: 'c' }, k + 1)), h('th', { class: 'c' }, 'Pts'));
    const body = order.map((i, k) => h('tr', {}, h('th', { style: 'text-align:left;white-space:nowrap' }, (k + 1) + ' ' + names[i]),
      order.map((j) => {
        if (i === j) return h('td', { class: 'self' }, '');
        const g = res.N[i][j];
        if (!g) return h('td', { class: 'muted' }, '·');
        const s = res.S[i][j];
        return h('td', { class: s > g / 2 ? 's1' : s < g / 2 ? 's0' : 'sh', title: names[i] + ' vs ' + names[j] + ': ' + s + ' / ' + g }, fmt.pts(s));
      }), h('td', { class: 'mono', style: 'font-weight:700' }, fmt.pts(res.rows[k].pts))));
    standBody.appendChild(h('table', { class: 'tbl xt' }, h('thead', {}, head), h('tbody', {}, body)));
  }

  // ---- elimination (N losses): loss counts, order and bracket ----------------------------------------------------------
  function elimInfo() {            // null unless the tournament is an elimination whose structure has arrived
    const st = T.structure;
    if (!T.snap || T.snap.cfg.format !== 'elimination' || !st || (st.kind !== 'elimination' && st.kind !== 'bracket')) return null;
    const out = {};
    st.out.forEach((o, i) => { out[o.id] = { round: o.round, order: i }; });
    return { st, out, losses: st.losses };
  }
  const elimRank = (el, r) => (r.id === el.st.champion ? 0 : el.out[r.id] ? 2 : 1);
  function orderElimination(res, el) {            // the champion, then the players still in (fewest losses first), then the others (the later a player went out, the better)
    res.rows.sort((a, b) => elimRank(el, a) - elimRank(el, b) || (elimRank(el, a) === 1 ? el.st.loss[a.id] - el.st.loss[b.id] : elimRank(el, a) === 2 ? el.out[b.id].round - el.out[a.id].round : 0) || b.pts - a.pts || b.sb - a.sb);
    res.rows.forEach((r, i) => { r.rank = i + 1; });
  }
  const elimStatus = (el, r) => (r.id === el.st.champion ? 'champion' : el.out[r.id] ? 'out in round ' + el.out[r.id].round : 'alive');
  const lossTxt = (n) => n + (n === 1 ? ' loss' : ' losses');

  // ---- the elimination bracket as a tree: a box for every match at its place, lines from the matches that feed it ----------------------------------
  const BK = { w: 176, fw: 232, h: 42, hb: 22, colGap: 30, rowH: 54, pad: 8, top: 14 };          // fw: the boxes of the final stage are wider, they show the losses of the two players

  function renderBracketTree(st) {
    const nm = (id) => T.names[id];
    const live = new Set();            // the matches that are being played now
    T.active.forEach((g) => { live.add(g.white + '|' + g.black); live.add(g.black + '|' + g.white); });
    const place = (s) => (s ? (s[0] === 'W' ? 'Winner of #' + s[1] : s[0] === 'L' ? 'Loser of #' + s[1] : 'Seed ' + (s[1] + 1)) : 'bye');
    const lossTag = (id, lo) => {      // final stage: the losses of a player before the match (they are what makes these two meet), in bold when one more loss puts the player out
      if (lo === undefined || lo === null) return null;
      const last = !st.ladder && lo === st.losses - 1;
      return h('span', { class: 'ls' + (last ? ' last' : ''), title: nm(id) + ': ' + lossTxt(lo) + ' before this match' + (st.ladder ? ' - the loser of this match is out' : ' (out at ' + st.losses + ')' + (last ? ' - one more loss and it is out' : '')) }, lossTxt(lo));
    };
    const row = (m, i) => {            // one player of a match, or where he will come from
      const id = i === 0 ? m.a : m.b, sc = i === 0 ? m.sa : m.sb;
      if (id) return h('div', { class: 'r' + (m.winner && m.winner === id ? ' win' : '') }, h('span', { class: 'nm', title: nm(id) }, nm(id)), lossTag(id, i === 0 ? m.la : m.lb), m.kind === 'real' && (m.done || sc || m.tb) ? h('span', { class: 'mono' }, fmt.pts(sc)) : null);
      return h('div', { class: 'r ph' }, h('span', { class: 'nm' }, place(m.s[i])));
    };
    const box = (m, x, y, hh, ww) => h('div', { class: 'mb' + (m.kind === 'bye' ? ' bye' : '') + (m.kind === 'real' && !m.done && m.a && m.b && live.has(m.a + '|' + m.b) ? ' live' : '') + (m.g && m.g.length ? ' clk' : ''), onclick: m.g && m.g.length ? () => selectFinished(m.g[0]) : null, style: 'left:' + x + 'px;top:' + y + 'px;height:' + hh + 'px' + (ww ? ';width:' + ww + 'px' : ''),
      title: (m.no ? 'Match #' + m.no : '') + (m.g && m.g.length ? ' - click to replay its first game' : '') + (m.tb ? ' - ' + m.tb + ' extra pair' + (m.tb > 1 ? 's' : '') + ' of games' : '') },
      m.no ? h('span', { class: 'no' }, '#' + m.no) : null,
      m.kind === 'bye' ? [row(m, m.s[0] ? 0 : 1), h('span', { class: 'bye-tag' }, 'bye')] : [row(m, 0), row(m, 1)]);
    const els = [], segs = [], pos = new Map();
    let y = BK.pad, cols = 0;
    st.brackets.forEach((bk) => {
      els.push(h('div', { class: 'bt', style: 'left:' + BK.pad + 'px;top:' + y + 'px' }, bk.name, bk.k ? h('span', { class: 'muted' }, ' (' + lossTxt(bk.k) + ')') : null));
      const base = y + 36;
      bk.rounds.forEach((rd, ri) => els.push(h('div', { class: 'ch', style: 'left:' + (BK.pad + ri * (BK.w + BK.colGap)) + 'px;top:' + (base - 14) + 'px;width:' + BK.w + 'px' }, 'Round ' + rd.round)));
      bk.rounds.forEach((rd) => rd.matches.forEach((m) => {
        const x = BK.pad + m.x * (BK.w + BK.colGap), cy = base + BK.top + m.y * BK.rowH + BK.h / 2, hh = m.kind === 'bye' ? BK.hb : BK.h;
        pos.set(m.id, { x, cy, w: BK.w });
        els.push(box(m, x, cy - hh / 2, hh));
      }));
      y = base + BK.top + bk.rows * BK.rowH + 22;
      cols = Math.max(cols, bk.rounds.length);
    });
    st.brackets.forEach((bk) => bk.rounds.forEach((rd) => rd.matches.forEach((m) => (m.f || []).forEach((fid) => {
      const a = pos.get(fid), b = pos.get(m.id);
      if (!a || !b) return;
      const x1 = a.x + BK.w, xm = x1 + BK.colGap / 2;
      segs.push('M' + x1 + ' ' + a.cy + 'H' + xm + 'V' + b.cy + 'H' + b.x);
    }))));
    const fx = BK.pad + cols * (BK.w + BK.colGap) + 6;            // the final stage and the champion: at the right of the widest bracket, level with the winners final
    const wb = st.brackets[0].rounds[st.brackets[0].rounds.length - 1].matches[0];
    let fy = wb ? pos.get(wb.id).cy - BK.h / 2 : BK.pad + 50;
    let wMax = fx + BK.fw + BK.pad;
    if (!st.ladder) els.push(h('div', { class: 'bt', style: 'left:' + fx + 'px;top:' + (fy - 44) + 'px' }, 'Final'));
    const byNo = new Map();            // match number -> its place in the drawing (the lines to the first match of the final stage start there)
    st.brackets.forEach((bk) => bk.rounds.forEach((rd) => rd.matches.forEach((m) => { if (m.no) byNo.set(m.no, pos.get(m.id)); })));
    const bn = (k) => (k === 0 ? 'winners bracket' : 'bracket ' + k);
    const ladderText = () => {
      const fk = (st.final[0] && st.final[0].fk) || [];
      if (st.final.length === 1) return st.losses === 2 ? 'Grand final: the champion of the winners bracket against the champion of the losers bracket, one match; the loser is out.' : 'Final: ' + bn(fk[0]) + ' against ' + bn(fk[1]) + ', one match; the loser is out.';
      return 'Final stage: the champions of the brackets meet one after the other, the bottom bracket first: ' + bn(fk[0]) + ' against ' + bn(fk[1]) + ', the winner against the next bracket up, and so on up to the winners bracket. '
        + 'The loser of each of these matches is out, whatever its losses. The losses behind a name are those before that match.';
    };
    if (st.ladder) {                   // the final ladder: one column of boxes for every match, going up to the right; a box is level with the middle of its two places, the lines come from these places
      const colX = (i) => fx + i * (BK.fw + BK.colGap);
      if (st.final.length) els.push(h('div', { class: 'bt', style: 'left:' + fx + 'px;top:' + BK.pad + 'px' }, 'Final stage'));
      st.final.forEach((f, i) => {
        const x = colX(i), srcs = (f.s || []).map((sl) => sl && byNo.get(sl[1])).filter(Boolean), fk = f.fk || [];
        const cy = srcs.length ? srcs.reduce((t, p) => t + p.cy, 0) / srcs.length : fy + BK.h / 2;
        srcs.forEach((p) => segs.push('M' + (p.x + p.w) + ' ' + p.cy + 'H' + (x - BK.colGap / 2) + 'V' + cy + 'H' + x));
        els.push(h('div', { class: 'ch', style: 'left:' + x + 'px;top:' + (cy - BK.h / 2 - 30) + 'px;width:' + BK.fw + 'px' }, st.losses === 2 ? 'Grand final' : 'Final ' + (i + 1) + ': ' + (fk[0] === null ? 'winner' : bn(fk[0])) + ' vs ' + bn(fk[1])));
        els.push(box({ kind: 'real', a: f.a, b: f.b, sa: f.sa, sb: f.sb, tb: f.tb, winner: f.winner, done: f.done, s: f.s, no: f.no, g: f.g, la: f.la, lb: f.lb }, x, cy - BK.h / 2, BK.h, BK.fw));
        byNo.set(f.no, { x, cy, w: BK.fw });
      });
      const lastP = st.final.length ? byNo.get(st.final[st.final.length - 1].no) : (wb ? pos.get(wb.id) : null);          // the champion: right of the last match (of the winners final when there is no ladder)
      if (lastP) {
        const cx = lastP.x + lastP.w + BK.colGap;
        segs.push('M' + (lastP.x + lastP.w) + ' ' + lastP.cy + 'H' + cx);
        if (st.champion) els.push(h('div', { class: 'mb champ', style: 'left:' + cx + 'px;top:' + (lastP.cy - BK.h / 2) + 'px;height:' + BK.h + 'px;width:' + BK.fw + 'px' }, h('div', { class: 'r win' }, h('span', { class: 'nm', title: nm(st.champion) }, nm(st.champion))), h('div', { class: 'r' }, h('span', { class: 'nm muted' }, 'Champion, ' + lossTxt(st.loss[st.champion])))));
        wMax = Math.max(wMax, cx + BK.fw + BK.pad);
      }
    }
    (st.ladder ? [] : st.final).forEach((f, i) => {
      const m = { kind: 'real', a: f.a, b: f.b, sa: f.sa, sb: f.sb, tb: f.tb, winner: f.winner, done: f.done, s: f.s, no: f.no, g: f.g, la: f.la, lb: f.lb };
      if (i === 0) (f.s || []).forEach((sl) => { const a = sl && byNo.get(sl[1]); if (a) segs.push('M' + (a.x + BK.w) + ' ' + a.cy + 'H' + (fx - BK.colGap / 2) + 'V' + (fy + BK.h / 2) + 'H' + fx); });
      els.push(h('div', { class: 'ch', style: 'left:' + fx + 'px;top:' + (fy - 30) + 'px;width:' + BK.fw + 'px' }, st.losses === 2 ? (i === 0 ? 'Grand final' : 'Deciding match') : 'Final ' + (i + 1)));
      els.push(box(m, fx, fy, BK.h, BK.fw));
      fy += BK.rowH + 22;
    });
    if (st.champion && !st.ladder) els.push(h('div', { class: 'mb champ', style: 'left:' + fx + 'px;top:' + (fy + 6) + 'px;height:' + BK.h + 'px;width:' + BK.fw + 'px' }, h('div', { class: 'r win' }, h('span', { class: 'nm', title: nm(st.champion) }, nm(st.champion))), h('div', { class: 'r' }, h('span', { class: 'nm muted' }, 'Champion, ' + lossTxt(st.loss[st.champion])))));
    const W = wMax, H = Math.max(y, fy + BK.h + 30);
    const lines = [];
    for (let k = 0; k < st.losses; k++) {            // how many players are in with how many losses (the names only when there are few)
      const ids = T.snap.cfg.engine_ids.filter((id) => st.loss[id] === k && !st.out.some((o) => o.id === id));
      if (ids.length) lines.push(h('div', {}, h('b', {}, lossTxt(k) + ' (' + ids.length + ')'), ids.length <= 8 ? [': ', ids.map((id) => nm(id)).join(', ')] : null));
    }
    if (st.out.length) lines.push(h('div', { class: 'muted' }, 'Out (' + st.out.length + ')'));
    if (st.ladder) { if (st.final.length) lines.push(h('div', { class: 'muted' }, ladderText())); }
    else if (st.losses >= 2) lines.push(h('div', { class: 'muted' }, st.losses === 2 ? 'Grand final: the two champions meet; if the champion of the losers bracket wins it, both have one loss and the deciding match follows.'
      : 'Final stage: the two players with the most losses play each other, again and again, until one of them is out (' + st.losses + ' losses). The losses behind a name are those before that match.'));
    standBody.appendChild(h('div', { class: 'help', style: 'padding:2px 2px 8px' }, lines));
    standBody.appendChild(h('div', { class: 'brk', style: 'width:' + W + 'px;height:' + H + 'px' }, svg('svg', { width: W, height: H }, svg('path', { d: segs.join(''), fill: 'none', stroke: '#8a8a8a', 'stroke-width': 1 })), els));
  }

  function applyWide() {            // a bracket needs the width of the window: the boards and the viewer move below it
    const on = isBracketTree() && S.wideBracket !== false;
    if (hdr.grid) hdr.grid.classList.toggle('wide', on);
    if (hdr.wide) hdr.wide.classList.toggle('hidden', !isBracketTree());
    standBody.style.maxHeight = on ? 'calc(100vh - 250px)' : 'calc(100vh - 330px)';
  }
  const isBracketTree = () => T.tab === 'bracket' && !!T.structure && T.structure.kind === 'bracket';

  function renderElimBracket(st) {
    const nm = (id) => (id ? T.names[id] : 'bye');
    const side = (m, who) => {            // one player of a match: the name (with his loss count when the two players differ in it) and the score
      const id = who === 'a' ? m.a : m.b, sc = who === 'a' ? m.sa : m.sb, lv = who === 'a' ? m.la : m.lb;
      return h('div', { class: m.winner && m.winner === id ? 'win' : '' },
        h('span', { class: 'ellipsis', style: 'max-width:105px', title: nm(id) }, nm(id), id && !m.bye && m.la !== m.lb ? h('span', { class: 'lv' }, ' [' + lv + ']') : null), h('span', { class: 'mono' }, m.bye ? '' : fmt.pts(sc)));
    };
    const lines = [];
    for (let k = 0; k < st.losses; k++) {            // who is in with how many losses, who is out
      const ids = T.snap.cfg.engine_ids.filter((id) => st.loss[id] === k && !st.out.some((o) => o.id === id));
      if (ids.length) lines.push(h('div', {}, h('b', {}, lossTxt(k) + ' (' + ids.length + '): '), ids.map((id) => T.names[id]).join(', ')));
    }
    if (st.out.length) lines.push(h('div', { class: 'muted' }, 'Out (' + st.out.length + '): ', st.out.map((o, i) => [i ? ', ' : '', h('span', { 'data-notr': '' }, T.names[o.id]), ' (round ' + o.round + ')'])));
    const cols = st.rounds.map((r) => {
      const groups = [];
      for (const m of r.matches) {            // inside a round the matches are grouped by the number of losses of the players
        const key = m.final ? 'final' : m.group === null ? 'between' : String(m.group);
        let g = groups.find((x) => x.key === key);
        if (!g) groups.push((g = { key, label: m.final ? 'Final' : m.group === null ? 'Final stage' : lossTxt(m.group), matches: [] }));
        g.matches.push(m);
      }
      return h('div', { class: 'rnd' }, h('div', { class: 'muted center' }, 'Round ' + r.round),
        groups.map((g) => h('div', { class: 'grp' }, h('div', { class: 'cap' }, g.label), g.matches.map((m) => h('div', { class: 'match' }, side(m, 'a'), side(m, 'b'))))));
    });
    if (st.champion) cols.push(h('div', { class: 'rnd' }, h('div', { class: 'muted center' }, 'Champion'), h('div', { class: 'match' }, h('div', { class: 'win' }, nm(st.champion), h('span', { class: 'mono' }, lossTxt(st.loss[st.champion]))))));
    standBody.appendChild(h('div', { class: 'help', style: 'padding:2px 2px 8px' }, lines));
    standBody.appendChild(h('div', { class: 'bracket elim' }, cols));
  }

  function renderBracket() {
    const st = T.structure;
    if (st && st.kind === 'bracket') return renderBracketTree(st);
    if (st && st.kind === 'elimination') return renderElimBracket(st);
    if (!st || !st.rounds) { standBody.appendChild(h('div', { class: 'empty' }, 'The bracket appears when the first games finish.')); return; }
    const nm = (id) => (id ? T.names[id] : 'bye');
    standBody.appendChild(h('div', { class: 'bracket' }, st.rounds.map((r) => h('div', { class: 'rnd' }, h('div', { class: 'muted center' }, 'Round ' + r.round),
      r.matches.map((m) => h('div', { class: 'match' },
        h('div', { class: m.winner && m.winner === m.a ? 'win' : '' }, h('span', { class: 'ellipsis', style: 'max-width:105px', title: nm(m.a) }, nm(m.a)), h('span', { class: 'mono' }, m.bye ? '' : fmt.pts(m.sa))),
        h('div', { class: m.winner && m.winner === m.b ? 'win' : '' }, h('span', { class: 'ellipsis', style: 'max-width:105px', title: nm(m.b) }, nm(m.b)), h('span', { class: 'mono' }, m.bye ? '' : fmt.pts(m.sb))))))).concat(
      st.champion ? [h('div', { class: 'rnd' }, h('div', { class: 'muted center' }, 'Champion'), h('div', { class: 'match' }, h('div', { class: 'win' }, nm(st.champion))))] : [])));
  }

  function exportCsv() {
    const res = currentStandings();
    const el = elimInfo();
    if (el) orderElimination(res, el);
    const lines = ['rank,engine,elo,error95,points,games,wins,draws,losses,score,sonneborn_berger' + (el ? ',matches_lost,status' : '')];
    for (const r of res.rows) lines.push([r.rank, '"' + r.name.replace(/"/g, '""') + '"', r.games ? r.elo.toFixed(1) : '', r.err ? r.err.toFixed(1) : '', r.pts, r.games, r.w, r.d, r.l, r.score === null ? '' : r.score.toFixed(4), r.sb.toFixed(2)].concat(el ? [el.st.loss[r.id], elimStatus(el, r)] : []).join(','));
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    const a = h('a', { href: url, download: (T.snap.cfg.name || 'standings').replace(/[^\w.-]+/g, '_') + '.csv' }); document.body.appendChild(a); a.click(); a.remove();
  }

  // ---- live games -------------------------------------------------------------------------------------------------
  function renderLive() {
    A.clear(liveEl);
    cards = new Map();
    const act = Array.from(T.active.values()).sort((a, b) => a.gid - b.gid);
    liveEl.appendChild(h('div', { class: 'h' }, 'Live games', h('span', { class: 'badge' }, String(act.length)), h('span', { class: 'spacer' }), h('span', { class: 'muted' }, 'click a board to follow it')));
    if (!act.length) {
      const st = T.snap.status;
      liveEl.appendChild(h('div', { class: 'empty' }, h('b', {}, st === 'finished' ? 'Tournament finished' : st === 'running' ? 'Starting games...' : 'No game is running'), st === 'finished' ? 'Click a finished game below to replay it.' : ''));
      return;
    }
    const grid = h('div', { class: 'live-grid' });
    for (const g of act) { const c = makeCard(g); cards.set(g.gid, c); grid.appendChild(c.el); }
    liveEl.appendChild(grid);
  }

  function makeCard(g) {
    const board = new A.Board({ size: 214, coords: false });
    const wN = h('span', { class: 'nm ellipsis', style: 'max-width:120px' }), bN = h('span', { class: 'nm ellipsis', style: 'max-width:120px' });
    const wC = h('span', { class: 'clk' }), bC = h('span', { class: 'clk' });
    const ev = h('span', { class: 'mono' }), info = h('span', { class: 'muted' });
    const el = h('div', { class: 'live-card' + (T.sel && T.sel.gid === g.gid ? ' sel' : ''), onclick: () => selectLive(g.gid) },
      h('div', { class: 'player' }, h('i', { class: 'swatch b' }), bN, bC), h('div', { style: 'text-align:center' }, board.el),
      h('div', { class: 'player' }, h('i', { class: 'swatch w' }), wN, wC), h('div', { class: 'meta' }, ev, info));
    const c = { el, board, wN, bN, wC, bC, ev, info, g };
    updateCard(c, g);
    return c;
  }

  function updateCard(c, g) {
    c.g = g;
    c.wN.textContent = T.names[g.white] || g.white; c.bN.textContent = T.names[g.black] || g.black;
    const last = g.last ? [g.last.slice(0, 2), g.last.slice(2, 4)] : null;
    c.board.setPosition(g.fen, { last, check: g.check });
    c.ev.textContent = g.cp === null || g.cp === undefined ? '' : fmt.eval(g.cp);
    c.ev.className = 'mono ' + (g.cp > 30 ? 'good' : g.cp < -30 ? 'bad' : '');
    c.info.textContent = 'move ' + Math.ceil((g.ply + 1) / 2) + (g.depth ? ' - d' + g.depth : '');
    tickCard(c);
    c.el.classList.toggle('sel', !!(T.sel && T.sel.gid === g.gid));
  }

  function tickCard(c) {
    const g = c.g;
    if (!g.clocks) { c.wC.textContent = ''; c.bC.textContent = ''; c.wC.style.display = c.bC.style.display = 'none'; return; }
    c.wC.style.display = c.bC.style.display = '';
    const whiteTurn = g.fen.split(' ')[1] === 'w', dt = performance.now() - (g._t || performance.now());
    const w = g.clocks[0] - (whiteTurn ? dt : 0), b = g.clocks[1] - (whiteTurn ? 0 : dt);
    c.wC.textContent = fmt.clock(w); c.bC.textContent = fmt.clock(b);
    c.wC.parentNode.classList.toggle('turn', whiteTurn); c.bC.parentNode.classList.toggle('turn', !whiteTurn);
  }
  setInterval(() => { if (!inRun) return; cards.forEach(tickCard); if (viewerLive) tickViewer(); if (T.snap && T.snap.status === 'running') refreshHeaderT(); }, 200);

  // ---- viewer (live or finished game) -----------------------------------------------------------------------------
  let viewerLive = false, vParts = {};
  function selectLive(gid) { T.sel = { gid, live: true }; T.view = null; cards.forEach((c, id) => c.el.classList.toggle('sel', id === gid)); renderViewer(); }
  async function selectFinished(gid) {
    T.sel = { gid, live: false };
    cards.forEach((c) => c.el.classList.remove('sel'));
    try { T.view = await A.get('/api/tournament/' + T.snap.id + '/game/' + gid); T.ply = T.view.plies; } catch (e) { A.toast(e.message, 'err'); return; }
    renderViewer(); renderList();
    viewerEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function renderViewer() {
    A.clear(viewerEl);
    viewerLive = false;
    if (!T.sel) { viewerEl.classList.add('hidden'); return; }
    viewerEl.classList.remove('hidden');
    const live = T.sel.live, g = live ? T.active.get(T.sel.gid) : T.view;
    if (!g) { if (live) { T.sel = null; viewerEl.classList.add('hidden'); } return; }
    viewerLive = live;
    const board = (viewerBoard = new A.Board({ size: 400 }));
    const wName = T.names[g.white] || g.white, bName = T.names[g.black] || g.black;
    vParts = { board, moves: h('div', { class: 'mv-list scroll', style: 'max-height:250px;margin-top:8px' }), graph: h('svg', {}), info: h('div'), wClk: h('span', { class: 'clk' }), bClk: h('span', { class: 'clk' }) };
    vParts.graph = svg('svg', { class: 'graph', viewBox: '0 0 300 90', preserveAspectRatio: 'none' });
    const nav = h('div', { class: 'nav' },
      live ? null : [h('button', { class: 'btn sm', onclick: () => gotoPly(0) }, '|<'), h('button', { class: 'btn sm', onclick: () => gotoPly(T.ply - 1) }, '<'), h('button', { class: 'btn sm', onclick: () => gotoPly(T.ply + 1) }, '>'), h('button', { class: 'btn sm', onclick: () => gotoPly(1e9) }, '>|')],
      h('button', { class: 'btn sm', onclick: () => board.flip() }, 'Flip'), h('span', { class: 'spacer grow' }),
      h('button', { class: 'btn sm ghost', onclick: () => { T.sel = null; T.view = null; renderViewer(); cards.forEach((c) => c.el.classList.remove('sel')); renderList(); } }, 'Close'));
    viewerEl.appendChild(h('div', { class: 'viewer' },
      h('div', {}, h('div', { class: 'player' }, h('i', { class: 'swatch b' }), h('span', { class: 'nm' }, bName), vParts.bClk), board.el, h('div', { class: 'player' }, h('i', { class: 'swatch w' }), h('span', { class: 'nm' }, wName), vParts.wClk), nav),
      h('div', { class: 'side' }, vParts.info, vParts.moves, h('div', { style: 'margin-top:10px' }, h('div', { class: 'muted', style: 'font-size:12px;margin-bottom:3px' }, 'Evaluation (white point of view)'), vParts.graph))));
    if (!live) { vParts.graph.addEventListener('click', (ev) => { const r = vParts.graph.getBoundingClientRect(); const n = T.view.plies; gotoPly(Math.round((ev.clientX - r.left) / r.width * n)); }); }
    updateViewer();
  }

  function gotoPly(p) {
    if (!T.view) return;
    T.ply = Math.max(0, Math.min(T.view.plies, p));
    updateViewer();
  }

  function movesRows(san, startFen, book, curPly, clickable) {
    const parts = (startFen || 'x w - - 0 1').split(' ');
    let blackFirst = parts[1] === 'b', num = parseInt(parts[5] || '1', 10) || 1;
    const out = [];
    let i = 0;
    while (i < san.length) {
      out.push(h('div', { class: 'n' }, num + '.'));
      for (let side = 0; side < 2; side++) {
        if (side === 0 && blackFirst) { out.push(h('div', { class: 'm' }, '...')); blackFirst = false; continue; }
        if (i >= san.length) { out.push(h('div', {})); continue; }
        const ply = i + 1;
        out.push(h('div', { class: 'm' + (curPly === ply ? ' cur' : '') + (ply <= book ? ' book' : ''), onclick: clickable ? () => gotoPly(ply) : null }, san[i]));
        i++;
      }
      num++;
    }
    return out;
  }

  function updateViewer() {
    if (!T.sel) return;
    const live = T.sel.live;
    const g = live ? T.active.get(T.sel.gid) : T.view;
    if (!g) return;
    const p = vParts;
    if (live) {
      p.board.setPosition(g.fen, { last: g.last ? [g.last.slice(0, 2), g.last.slice(2, 4)] : null, check: g.check });
      A.clear(p.moves); p.moves.append(...movesRows(g.san || [], null, 0, (g.san || []).length, false));
      A.clear(p.info);
      p.info.append(h('div', {}, h('span', { class: 'badge good' }, 'live'), ' ', h('b', {}, fmt.eval(g.cp)), g.depth ? h('span', { class: 'muted' }, '  depth ' + g.depth + (g.nodes ? ' - ' + fmt.num(g.nodes) + ' nodes' : '') + (g.nps ? ' - ' + fmt.num(g.nps) + ' nps' : '')) : null),
        g.pv ? h('div', { class: 'mono muted', style: 'font-size:12px;margin-top:3px' }, g.pv) : null);
      A.clear(p.graph);
      tickViewer();
      p.moves.scrollTop = p.moves.scrollHeight;
      return;
    }
    const ply = T.ply, fen = g.fens[ply], lastU = ply > 0 ? g.uci[ply - 1] : null;
    p.board.setPosition(fen, { last: lastU ? [lastU.slice(0, 2), lastU.slice(2, 4)] : null, check: g.checks[ply] });
    A.clear(p.moves); p.moves.append(...movesRows(g.san, g.start_fen, g.book, ply, true));
    const cur = p.moves.querySelector('.cur'); if (cur) cur.scrollIntoView({ block: 'nearest' });
    p.wClk.textContent = ''; p.bClk.textContent = '';
    const res = g.result, resTxt = res === '1-0' ? (T.names[g.white] + ' wins') : res === '0-1' ? (T.names[g.black] + ' wins') : 'Draw';
    A.clear(p.info);
    const cpHere = ply > 0 ? g.cp[ply - 1] : null;
    p.info.append(h('div', {}, h('span', { class: 'badge acc' }, res), ' ', h('b', {}, resTxt), h('span', { class: 'muted' }, ' - ' + g.reason + ' - ' + g.plies + ' plies - ' + fmt.dur(g.dur))),
      h('div', { class: 'mono muted', style: 'font-size:12px;margin-top:3px' }, ply === 0 ? 'start position' : 'ply ' + ply + (cpHere !== null && cpHere !== undefined ? ' - eval ' + fmt.eval(cpHere) : (ply <= g.book ? ' - book move' : '')) + (g.depth[ply - 1] ? ' - depth ' + g.depth[ply - 1] : '') + (g.ms[ply - 1] ? ' - ' + (g.ms[ply - 1] / 1000).toFixed(2) + 's' : '')));
    drawGraph(p.graph, g, ply);
  }

  function tickViewer() {
    const g = T.active.get(T.sel && T.sel.gid);
    if (!g || !g.clocks) { vParts.wClk.textContent = vParts.bClk.textContent = ''; return; }
    const whiteTurn = g.fen.split(' ')[1] === 'w', dt = performance.now() - (g._t || performance.now());
    vParts.wClk.textContent = fmt.clock(g.clocks[0] - (whiteTurn ? dt : 0)); vParts.bClk.textContent = fmt.clock(g.clocks[1] - (whiteTurn ? 0 : dt));
  }

  function drawGraph(el, g, ply) {
    A.clear(el);
    const n = g.plies || 1, W = 300, H = 90;
    const y = (cp) => { const c = Math.max(-800, Math.min(800, cp)); return H / 2 - (Math.sign(c) * Math.pow(Math.abs(c) / 800, 0.6)) * (H / 2 - 4); };
    const x = (i) => (i / n) * W;
    el.appendChild(svg('line', { x1: 0, x2: W, y1: H / 2, y2: H / 2, stroke: 'var(--line)', 'stroke-width': 1, 'vector-effect': 'non-scaling-stroke' }));
    let d = '', last = null;
    g.cp.forEach((cp, i) => { if (cp === null || cp === undefined) return; d += (last === null ? 'M' : 'L') + x(i + 1).toFixed(1) + ' ' + y(cp).toFixed(1) + ' '; last = cp; });
    if (d) {
      el.appendChild(svg('path', { d: d + 'L' + x(last === null ? 0 : n).toFixed(1) + ' ' + (H / 2) + ' L' + x(1).toFixed(1) + ' ' + (H / 2) + ' Z', fill: 'rgba(0,0,0,.08)', stroke: 'none' }));
      el.appendChild(svg('path', { d, fill: 'none', stroke: '#222', 'stroke-width': 1.3, 'vector-effect': 'non-scaling-stroke' }));
    }
    el.appendChild(svg('line', { x1: x(ply), x2: x(ply), y1: 0, y2: H, stroke: '#707070', 'stroke-width': 1.2, 'vector-effect': 'non-scaling-stroke' }));
  }

  // ---- finished games list ----------------------------------------------------------------------------------------
  function renderList() {
    if (!listEl) return;
    A.clear(listEl);
    const ids = T.snap.cfg.engine_ids;
    A.clear(listSel);
    listSel.appendChild(h('option', { value: '' }, 'All engines'));
    ids.forEach((id) => listSel.appendChild(h('option', { value: id, selected: T.filter === id }, T.names[id])));
    listEl.appendChild(h('div', { class: 'h' }, 'Finished games', h('span', { class: 'badge' }, String(T.games.length)), h('span', { class: 'spacer' }), listSel));
    let list = T.games.slice().sort((a, b) => b.gid - a.gid);
    if (T.filter) list = list.filter((g) => g.white === T.filter || g.black === T.filter);
    if (!list.length) { listEl.appendChild(h('div', { class: 'muted' }, 'Nothing yet.')); return; }
    const rows = list.slice(0, 400).map((g) => h('tr', { class: T.sel && !T.sel.live && T.sel.gid === g.gid ? 'sel' : '', onclick: () => selectFinished(g.gid) },
      h('td', { class: 'muted right' }, g.gid), h('td', {}, T.names[g.white]), h('td', {}, T.names[g.black]),
      h('td', { class: 'mono c', style: 'font-weight:700' }, g.result), h('td', { class: 'muted' }, g.reason), h('td', { class: 'right mono muted' }, g.plies)));
    listEl.appendChild(h('div', { class: 'scroll h420' }, h('table', { class: 'tbl click' }, h('thead', {}, h('tr', {}, h('th', { class: 'r' }, '#'), h('th', {}, 'White'), h('th', {}, 'Black'), h('th', { class: 'c' }, 'Result'), h('th', {}, 'Reason'), h('th', { class: 'r' }, 'Plies'))), h('tbody', {}, rows))));
    if (list.length > 400) listEl.appendChild(h('div', { class: 'help' }, 'Showing the latest 400 of ' + list.length + ' games - filter by engine to see more.'));
  }

  // ======================================================================================================== views
  function showSetup() {
    inRun = false;
    A.clear(root); root.appendChild(setupEl);
    const back = A.$('#back-btn'); if (back) back.classList.toggle('hidden', !T.snap);
    renderSaved(); renderParticipants(); refreshSummary();
  }
  function showRun() {
    if (!T.snap) return;
    inRun = true;
    A.clear(root); root.appendChild(runEl);
    refreshAll();
  }

  // ======================================================================================================== events
  A.on('tournament', (ev) => {
    if (!T.snap || ev.tid !== T.snap.id) return;
    T.evSeq++;
    const prev = T.snap.status;
    T.snap.status = ev.status; T.snap.total = ev.total; T.snap.error = ev.error; T.structure = ev.structure;
    T.elapsed0 = ev.elapsed; T.t0 = performance.now();
    if (inRun) { refreshHeaderT(); refreshStanding(); if (prev !== ev.status && !T.active.size) renderLive(); }
  });
  A.on('game_start', (ev) => {
    if (!T.snap || ev.tid !== T.snap.id) return;
    ev.game._t = performance.now();
    T.active.set(ev.game.gid, ev.game);
    if (inRun) { if (!cards.size) renderLive(); else { const c = makeCard(ev.game); cards.set(ev.game.gid, c); const grid = A.$('.live-grid', liveEl); if (grid) grid.appendChild(c.el); } updateLiveCount(); }
  });
  A.on('game_update', (ev) => {
    if (!T.snap || ev.tid !== T.snap.id) return;
    const g = T.active.get(ev.gid); if (!g) return;
    Object.assign(g, { ply: ev.ply, fen: ev.fen, san: ev.san, clocks: ev.clocks, cp: ev.cp, depth: ev.depth, nodes: ev.nodes, nps: ev.nps, pv: ev.pv, check: ev.check, last: ev.last, _t: performance.now() });
    if (!inRun) return;
    const c = cards.get(ev.gid); if (c) updateCard(c, g);
    if (T.sel && T.sel.live && T.sel.gid === ev.gid) updateViewer();
  });
  function endActive(gid) {
    T.active.delete(gid);
    const c = cards.get(gid); if (c) { c.el.remove(); cards.delete(gid); }
    if (T.sel && T.sel.live && T.sel.gid === gid) { T.sel = null; renderViewer(); }
    updateLiveCount();
    if (inRun && !T.active.size) renderLive();
  }
  function updateLiveCount() { const b = A.$('.badge', liveEl); if (b) b.textContent = String(T.active.size); }
  A.on('game_end', (ev) => {
    if (!T.snap || ev.tid !== T.snap.id) return;
    if (!T.games.some((g) => g.gid === ev.game.gid)) T.games.push(ev.game);
    endActive(ev.game.gid);
    if (inRun) refreshStanding();
  });
  A.on('game_aborted', (ev) => { if (T.snap && ev.tid === T.snap.id) endActive(ev.gid); });
  A.on('tournament_deleted', (ev) => {
    if (T.snap && ev.tid === T.snap.id) {            // the one on show: forget it and go back to the setup
      T.snap = null; T.games = []; T.active = new Map(); T.sel = null; T.view = null; T.structure = null;
      if (inRun) showSetup(); else { const back = A.$('#back-btn'); if (back) back.classList.add('hidden'); }
    }
    A.get('/api/tournaments').then((r) => { A.state.tournaments = r.tournaments; renderSaved(); }).catch(() => {});
  });
  document.addEventListener('keydown', (e) => {          // arrow keys step through a finished game
    if (!inRun || !T.sel || T.sel.live || !T.view || !root || root.classList.contains('hidden')) return;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes((document.activeElement || {}).tagName) || document.querySelector('.modal-back')) return;
    const k = e.key;
    if (k === 'ArrowLeft') gotoPly(T.ply - 1); else if (k === 'ArrowRight') gotoPly(T.ply + 1); else if (k === 'Home') gotoPly(0); else if (k === 'End') gotoPly(1e9); else return;
    e.preventDefault();
  });
  A.on('game_start', (ev) => { if (inRun && T.snap && ev.tid === T.snap.id && isBracketTree()) refreshStanding(); });          // (the bracket marks the matches that are being played)
  A.on('resync', () => A.emit('hello', {}));          // the server dropped events this window had not read: load the state again, as after a reconnect
  A.on('hello', async () => {          // (re)connected: resynchronise with the server
    if (!T.snap) return;
    try { const s = await A.get('/api/tournament/' + T.snap.id); const keep = inRun; loadSnapshot(s); if (keep) showRun(); } catch (e) { /* ignore */ }
  });
  A.on('engines', () => { if (partBody) { renderParticipants(); refreshSummary(); } });

  A.register({
    id: 'tournament', title: 'Tournament',
    init(el) {
      root = el;
      const saved = Object.assign({}, A.pref('t_setup', {}));
      if ((saved.v || 1) < 2) saved.margin_ms = DEFAULT_S.margin_ms;          // setup version 2: the time margin is 600 ms like in the fastchess runs (older saved setups had 250)
      Object.assign(S, JSON.parse(JSON.stringify(DEFAULT_S)), saved);
      S.v = 2;
      S.openings = Object.assign({}, DEFAULT_S.openings, S.openings); S.adj = Object.assign({}, DEFAULT_S.adj, S.adj);
      const logical = A.state.cpu.logical || 8;
      if (!A.pref('t_setup')) { S.concurrency = Math.max(1, Math.min(8, Math.floor(logical / 4))); S.selected = A.state.engines.slice(0, 2).map((e) => e.id); }
      S.selected = S.selected.filter((id) => A.engineById(id));
      if (S.selected.length < 2 && A.state.engines.length >= 2) S.selected = A.state.engines.slice(0, 2).map((e) => e.id);
      buildSetup();
      const cur = A.state.current;
      if (cur) { loadSnapshot(cur); showRun(); } else showSetup();
    },
    onShow() { if (!inRun) { A.get('/api/tournaments').then((r) => { A.state.tournaments = r.tournaments; renderSaved(); }).catch(() => {}); } },
  });
})();
