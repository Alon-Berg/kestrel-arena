/* Kestrel Arena - Play tab: play against an engine (default: the bundled Kestrel) and analyse positions with it */
(function () {
  const A = window.A;
  const { h, fmt } = A;
  const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  const G = {
    mode: 'game', st: null, view: null, flipped: false, sel: null, cache: new Map(),
    AH: { fens: [START], san: [], uci: [], checks: [null], ply: 0 },
    cfg: { engine: '', color: 'white', strength: 'full', elo: 2000, limit: 'movetime', movetime: 1000, nodes: 100000, depth: 12, fen: '', mpv: 2, aengine: '' },
    analysing: false, lines: [], info: null, evalCp: null, evalMate: null, busy: false,
  };
  const probes = {};
  let root, board, evalBar, evalW, evalTop, evalBot, topStrip, bottomStrip, statusEl, movesEl, linesEl, panelPlay, panelAna, bannerEl, infoEl, fenIn, seg;
  const save = A.throttle(() => A.setPref('play_cfg', G.cfg), 600);

  // ---------------------------------------------------------------------------------------------- positions
  function curPos() {
    if (G.mode === 'game') {
      const st = G.st;
      if (!st) return { fen: START, last: null, check: null, canMove: false, ply: 0, len: 0 };
      const len = st.uci.length, ply = G.view === null ? len : G.view;
      const last = ply > 0 ? st.uci[ply - 1] : null;
      const mine = (st.human === 'white') === (st.turn === 'w');
      return { fen: st.fens[ply], last, check: ply === len ? st.check : null, ply, len, canMove: !st.over && ply === len && mine && !st.thinking };
    }
    const a = G.AH, ply = a.ply, last = ply > 0 ? a.uci[ply - 1] : null;
    return { fen: a.fens[ply], last, check: a.checks[ply], ply, len: a.uci.length, canMove: true };
  }

  async function legalFor(fen) {
    if (G.cache.has(fen)) return G.cache.get(fen);
    const li = await A.post('/api/legal', { fen });
    if (G.cache.size > 400) G.cache.clear();
    G.cache.set(fen, li);
    return li;
  }

  async function render() {
    const pos = curPos();
    let targets = [];
    if (G.sel && pos.canMove) {
      try {
        const li = await legalFor(pos.fen);
        targets = li.moves.filter((m) => m.from === G.sel).map((m) => ({ sq: m.to, capture: !!board.pieceAtFen(pos.fen, m.to) || /x/.test(m.san) }));
      } catch (e) { /* ignore */ }
    }
    board.setPosition(pos.fen, { last: pos.last ? [pos.last.slice(0, 2), pos.last.slice(2, 4)] : null, check: pos.check, selected: pos.canMove ? G.sel : null, targets });
    renderStrips(pos);
    renderMoves(pos);
    renderBanner();
    renderEval();
  }

  function renderStrips(pos) {
    const st = G.st;
    const oppName = st ? st.engine : 'Engine', white = (st ? st.human === 'white' : true);
    const set = (el, name, isEngine, color) => {
      A.clear(el);
      el.appendChild(h('i', { class: 'swatch ' + color })); el.appendChild(h('span', { class: 'nm' }, name));
      if (isEngine && st && st.thinking) el.appendChild(h('span', { class: 'badge acc' }, 'thinking...'));
    };
    const bottomWhite = !G.flipped;
    if (G.mode === 'game' && st) {
      const nameFor = (color) => ((color === 'white') === white ? 'You' : oppName);
      const eng = (color) => (color === 'white') !== white;
      set(topStrip, nameFor(bottomWhite ? 'black' : 'white'), eng(bottomWhite ? 'black' : 'white'), bottomWhite ? 'b' : 'w');
      set(bottomStrip, nameFor(bottomWhite ? 'white' : 'black'), eng(bottomWhite ? 'white' : 'black'), bottomWhite ? 'w' : 'b');
    } else {
      set(topStrip, G.mode === 'game' ? 'Opponent' : 'Black', false, bottomWhite ? 'b' : 'w');
      set(bottomStrip, G.mode === 'game' ? 'You' : 'White', false, bottomWhite ? 'w' : 'b');
    }
  }

  function movesRows(san, fen0, cur, onclick) {
    const parts = (fen0 || START).split(' ');
    let blackFirst = parts[1] === 'b', num = parseInt(parts[5] || '1', 10) || 1;
    const out = []; let i = 0;
    while (i < san.length) {
      out.push(h('div', { class: 'n' }, num + '.'));
      for (let s = 0; s < 2; s++) {
        if (s === 0 && blackFirst) { out.push(h('div', { class: 'm' }, '...')); blackFirst = false; continue; }
        if (i >= san.length) { out.push(h('div', {})); continue; }
        const ply = i + 1;
        out.push(h('div', { class: 'm' + (cur === ply ? ' cur' : ''), onclick: () => onclick(ply) }, san[i])); i++;
      }
      num++;
    }
    return out;
  }
  function renderMoves(pos) {
    A.clear(movesEl);
    const san = G.mode === 'game' ? (G.st ? G.st.san : []) : G.AH.san;
    const fen0 = G.mode === 'game' ? (G.st ? G.st.fens[0] : START) : G.AH.fens[0];
    movesEl.append(...movesRows(san, fen0, pos.ply, (p) => gotoPly(p)));
    const cur = movesEl.querySelector('.cur'); if (cur) cur.scrollIntoView({ block: 'nearest' }); else movesEl.scrollTop = movesEl.scrollHeight;
  }

  function renderBanner() {
    A.clear(bannerEl);
    const st = G.st;
    if (G.mode !== 'game' || !st) { bannerEl.classList.add('hidden'); return; }
    bannerEl.classList.remove('hidden');
    if (st.over) {
      const youWhite = st.human === 'white';
      const txt = st.result === '1/2-1/2' ? 'Draw' : ((st.result === '1-0') === youWhite ? 'You win' : (st.result === '*' ? 'Game stopped' : 'You lose'));
      bannerEl.appendChild(h('div', { class: 'row' }, h('span', { class: 'badge acc' }, st.result), h('b', {}, txt), h('span', { class: 'muted' }, st.reason || '')));
    } else if (st.thinking) bannerEl.appendChild(h('div', { class: 'muted' }, st.engine + ' is thinking...'));
    else bannerEl.appendChild(h('div', {}, (st.turn === 'w') === (st.human === 'white') ? 'Your move.' : 'Waiting for ' + st.engine + '...'));
  }

  function renderEval() {
    let cp = G.evalCp, mate = G.evalMate;
    if (cp === null && mate === null) { evalW.style.height = '50%'; evalTop.textContent = ''; evalBot.textContent = ''; return; }
    const p = mate !== null ? (mate > 0 ? 1 : 0) : 1 / (1 + Math.pow(10, -cp / 400));
    const txt = mate !== null ? 'M' + Math.abs(mate) : (Math.abs(cp) / 100).toFixed(1);
    evalW.style.height = (p * 100).toFixed(1) + '%';
    evalW.style.top = G.flipped ? '0' : 'auto'; evalW.style.bottom = G.flipped ? 'auto' : '0';
    const whiteBetter = (mate !== null ? mate > 0 : cp >= 0);
    evalTop.textContent = ''; evalBot.textContent = '';
    const el = (whiteBetter !== G.flipped) ? evalBot : evalTop;      // the label sits in the bar part of the better side
    el.textContent = txt;
  }

  // ---------------------------------------------------------------------------------------------- moves
  function gotoPly(p) {
    G.sel = null;
    if (G.mode === 'game') {
      if (!G.st) return;
      const len = G.st.uci.length;
      p = Math.max(0, Math.min(len, p));
      G.view = p === len ? null : p;
    } else G.AH.ply = Math.max(0, Math.min(G.AH.uci.length, p));
    render(); analysePosition();
  }

  function askPromotion(cands, turn) {
    return new Promise((resolve) => {
      const dlg = A.modal({ title: 'Promote to', width: '380px', dismiss: true, body: h('div', { class: 'promo' }, ['q', 'r', 'b', 'n'].map((t) => {
        const m = cands.find((c) => c.promo === t);
        return h('button', { onclick: () => { resolve(m); dlg.close(); } }, h('span', { style: { color: turn === 'w' ? '#fdfdfb' : '#1c1a18', textShadow: '0 0 3px ' + (turn === 'w' ? '#000' : '#ddd') } }, A.Board.glyph(t)));
      })), buttons: [{ label: 'Cancel', onClick: () => resolve(null) }] });
    });
  }

  async function onSquare(sq) {
    if (G.busy) return;
    const pos = curPos();
    if (!pos.canMove) return;
    let li;
    try { li = await legalFor(pos.fen); } catch (e) { return; }
    if (G.sel) {
      const cands = li.moves.filter((m) => m.from === G.sel && m.to === sq);
      if (cands.length) {
        let mv = cands[0];
        if (cands.length > 1) mv = await askPromotion(cands, li.turn);
        if (mv) return doMove(mv.uci);
      }
    }
    const pc = board.pieceAt(sq);
    G.sel = pc && ((pc === pc.toUpperCase()) === (li.turn === 'w')) && li.moves.some((m) => m.from === sq) ? sq : null;
    render();
  }

  async function doMove(uci) {
    G.busy = true;
    try {
      if (G.mode === 'game') {
        G.st = await A.post('/api/play/move', { uci }); G.view = null; G.sel = null; G.evalCp = G.evalMate = null;
      } else {
        const a = G.AH, r = await A.post('/api/apply', { fen: a.fens[a.ply], uci });
        a.fens.length = a.ply + 1; a.san.length = a.ply; a.uci.length = a.ply; a.checks.length = a.ply + 1;
        a.fens.push(r.fen); a.san.push(r.san); a.uci.push(r.uci); a.checks.push(r.check); a.ply++; G.sel = null;
        analysePosition();
      }
    } catch (e) { A.toast(e.message, 'err'); G.sel = null; }
    G.busy = false;
    render();
  }

  // ---------------------------------------------------------------------------------------------- game control
  async function probeEngine(id) {
    if (probes[id]) return probes[id];
    const e = A.engineById(id);
    const r = e ? await A.post('/api/engines/probe', { path: e.path, cwd: e.cwd, args: e.args }).catch(() => null) : null;
    const opt = (n) => (r && r.options || []).find((o) => o.name === n);
    // Kestrel plays at full strength only, as on its web page: its UCI_Elo option cannot go below the strength of a 2048-node search (see the README)
    probes[id] = { elo: opt('UCI_Elo'), skill: opt('Skill Level'), kestrel: !!(r && /^kestrel/i.test(r.id_name || '')) };
    return probes[id];
  }

  async function newGame() {
    const c = G.cfg;
    const body = { engine: c.engine, color: c.color, limit: c.limit, movetime: c.movetime, nodes: c.nodes, depth: c.depth, fen: c.fen.trim() };
    if (c.strength === 'elo') body.elo = c.elo;
    try {
      G.st = await A.post('/api/play/new', body); G.view = null; G.sel = null; G.evalCp = G.evalMate = null; G.info = null;
      G.flipped = G.st.human === 'black';
      board.flip(G.flipped);
      render();
    } catch (e) { A.toast(e.message, 'err'); }
  }
  async function undo() { try { G.st = await A.post('/api/play/undo'); G.view = null; G.sel = null; render(); } catch (e) { A.toast(e.message, 'err'); } }
  async function resign() { try { G.st = await A.post('/api/play/resign'); render(); } catch (e) { A.toast(e.message, 'err'); } }

  // ---------------------------------------------------------------------------------------------- analysis
  const analysePosition = A.throttle(async () => {
    if (!G.analysing) return;
    const pos = curPos();
    G.lines = []; renderLines();
    try { await A.post('/api/analysis/start', { engine: G.cfg.aengine, fen: pos.fen, multipv: G.cfg.mpv }); } catch (e) { A.toast(e.message, 'err'); G.analysing = false; renderAnaButton(); }
  }, 120);

  function renderAnaButton() {
    const b = A.$('#ana-btn', root); if (!b) return;
    b.textContent = G.analysing ? 'Stop analysis' : 'Start analysis'; b.className = 'btn ' + (G.analysing ? 'danger' : 'primary');
  }
  async function toggleAnalysis() {
    G.analysing = !G.analysing; renderAnaButton();
    if (G.analysing) analysePosition(); else { try { await A.post('/api/analysis/stop'); } catch (e) { /* ignore */ } }
  }

  function renderLines() {
    A.clear(linesEl);
    if (!G.analysing) { linesEl.appendChild(h('div', { class: 'muted', style: 'padding:6px' }, 'Analysis is off. Start it to see the engine\'s best lines for the position on the board.')); return; }
    if (!G.lines.length) { linesEl.appendChild(h('div', { class: 'muted', style: 'padding:6px' }, 'Thinking...')); return; }
    G.lines.forEach((l) => {
      const sc = l.mate !== null && l.mate !== undefined ? '#' + l.mate : fmt.eval(l.cp);
      linesEl.appendChild(h('div', { class: 'pvline' }, h('span', { class: 'sc ' + ((l.mate ? l.mate > 0 : l.cp > 30) ? 'good' : (l.mate ? l.mate < 0 : l.cp < -30) ? 'bad' : '') }, sc), h('span', { class: 'muted' }, 'd' + (l.depth || '?')), h('span', { class: 'mvs', title: l.pv }, l.pv)));
    });
  }

  document.addEventListener('keydown', (e) => {          // arrow keys step through the moves
    if (!root || root.classList.contains('hidden') || ['INPUT', 'SELECT', 'TEXTAREA'].includes((document.activeElement || {}).tagName) || document.querySelector('.modal-back')) return;
    const k = e.key;
    if (k === 'ArrowLeft') gotoPly(curPos().ply - 1); else if (k === 'ArrowRight') gotoPly(curPos().ply + 1); else if (k === 'Home') gotoPly(0); else if (k === 'End') gotoPly(1e9); else return;
    e.preventDefault();
  });
  A.on('analysis', (ev) => {
    if (!G.analysing) return;
    const pos = curPos();
    if (ev.fen.split(' ').slice(0, 4).join(' ') !== pos.fen.split(' ').slice(0, 4).join(' ')) return;      // only the position on the board (first four FEN fields)
    G.lines = ev.lines;
    if (ev.lines.length) { G.evalCp = ev.lines[0].mate ? null : ev.lines[0].cp; G.evalMate = ev.lines[0].mate || null; if (ev.lines[0].mate === 0) G.evalMate = null; }
    renderLines(); renderEval();
  });
  A.on('play_state', (ev) => {
    if (G.mode !== 'game') return;
    const atLatest = G.view === null;
    G.st = ev;
    if (atLatest) G.view = null;
    G.sel = null;
    render();
  });
  A.on('play_info', (ev) => {
    if (G.mode !== 'game') return;
    G.info = ev; G.evalCp = ev.mate ? null : ev.cp; G.evalMate = ev.mate || null;
    A.clear(infoEl);
    infoEl.appendChild(h('span', { class: 'mono' }, (ev.mate ? '#' + ev.mate : fmt.eval(ev.cp)) + '  d' + ev.depth + (ev.nps ? '  ' + fmt.num(ev.nps) + ' nps' : '') + '  ' + (ev.pv || '').slice(0, 70)));
    renderEval();
  });

  // ---------------------------------------------------------------------------------------------- layout
  function field(label, ...ctl) { return h('div', { class: 'field', style: 'grid-template-columns:110px 1fr' }, h('label', {}, label), h('div', { class: 'inline' }, ...ctl)); }
  function setMode(m) {
    G.mode = m; G.sel = null;
    A.clear(infoEl);
    panelPlay.classList.toggle('hidden', m !== 'game'); panelAna.classList.toggle('hidden', m !== 'analysis');
    A.$$('button', seg).forEach((b) => b.classList.toggle('on', b.dataset.m === m));
    if (m === 'analysis') { G.evalCp = G.evalMate = null; if (G.analysing) analysePosition(); } else { G.evalCp = G.evalMate = null; if (G.analysing) { G.analysing = false; renderAnaButton(); A.post('/api/analysis/stop').catch(() => {}); } }
    A.setPref('play_mode', m);
    render();
  }

  A.register({
    id: 'play', title: 'Play',
    init(el) {
      root = el;
      Object.assign(G.cfg, A.pref('play_cfg', {}));
      const def = A.state.engines.find((e) => e.builtin) || A.state.engines[0];
      if (!A.engineById(G.cfg.engine)) G.cfg.engine = def ? def.id : '';
      if (!A.engineById(G.cfg.aengine)) G.cfg.aengine = def ? def.id : '';
      const size = Math.max(360, Math.min(640, window.innerHeight - 250));
      board = new A.Board({ size, interactive: true, onSquare });
      board.pieceAtFen = (fen, sq) => { const rows = fen.split(' ')[0].split('/'); const f = 'abcdefgh'.indexOf(sq[0]), r = 8 - parseInt(sq[1], 10); let c = 0; for (const ch of rows[r]) { if (ch >= '1' && ch <= '8') c += +ch; else { if (c === f) return ch; c++; } } return null; };
      evalW = h('div', { class: 'w' }); evalTop = h('div', { class: 't top' }); evalBot = h('div', { class: 't bot' });
      evalBar = h('div', { class: 'evalbar', style: { height: (size + 64) + 'px', marginTop: '0' } }, evalW, evalTop, evalBot);
      topStrip = h('div', { class: 'player' }); bottomStrip = h('div', { class: 'player' });
      infoEl = h('div', { class: 'muted', style: 'min-height:20px;font-size:12px;max-width:' + size + 'px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis' });
      const nav = h('div', { class: 'nav' }, h('button', { class: 'btn sm', onclick: () => gotoPly(0) }, '|<'), h('button', { class: 'btn sm', onclick: () => gotoPly(curPos().ply - 1) }, '<'), h('button', { class: 'btn sm', onclick: () => gotoPly(curPos().ply + 1) }, '>'), h('button', { class: 'btn sm', onclick: () => gotoPly(1e9) }, '>|'),
        h('button', { class: 'btn sm', onclick: () => { G.flipped = !G.flipped; board.flip(G.flipped); render(); } }, 'Flip board'), h('button', { class: 'btn sm', onclick: () => { navigator.clipboard && navigator.clipboard.writeText(curPos().fen).then(() => A.toast('FEN copied', 'ok')); } }, 'Copy FEN'));
      const boardCol = h('div', { style: 'display:flex;gap:12px;align-items:flex-start' }, evalBar, h('div', {}, topStrip, board.el, bottomStrip, nav, infoEl));

      movesEl = h('div', { class: 'mv-list scroll', style: 'max-height:220px;min-height:90px;margin-top:10px;padding:4px;background:#fff;border:1px solid var(--line2)' });
      bannerEl = h('div', { class: 'hidden', style: 'margin-top:10px;padding:6px 8px;background:var(--panel2);border:1px solid var(--line2)' });
      linesEl = h('div', { style: 'margin-top:8px;background:#fff;border:1px solid var(--line2);min-height:60px' });

      // play panel
      const engSel = (key, onchg) => h('select', { class: 'select', onchange: (e) => { G.cfg[key] = e.target.value; save(); if (onchg) onchg(); } }, A.state.engines.map((e) => h('option', { value: e.id, selected: G.cfg[key] === e.id }, e.name)));
      const strengthBox = h('div', { class: 'inline' });
      async function renderStrength() {
        A.clear(strengthBox);
        const p = await probeEngine(G.cfg.engine);
        const canElo = !!(p && p.elo) && !p.kestrel;                // not for Kestrel: below about 2300 every UCI_Elo setting plays alike, so it plays at full strength only
        if (G.cfg.strength === 'level' || (!canElo && G.cfg.strength === 'elo')) G.cfg.strength = 'full';      // 'level' = a setting saved while Kestrel still had levels
        strengthBox.appendChild(h('select', { class: 'select', style: 'width:auto', onchange: (e) => { G.cfg.strength = e.target.value; save(); renderStrength().then(renderLimit); } }, [['full', 'Full strength']].concat(canElo ? [['elo', 'Limit to Elo...']] : []).map(([v, l]) => h('option', { value: v, selected: G.cfg.strength === v }, l))));
        if (G.cfg.strength === 'elo' && canElo) {
          const lo = p.elo.min || 1320, hi = p.elo.max || 3200;
          G.cfg.elo = Math.max(lo, Math.min(hi, G.cfg.elo));
          const lab = h('span', { class: 'mono', style: 'min-width:44px' }, G.cfg.elo);
          strengthBox.appendChild(h('input', { type: 'range', min: lo, max: hi, step: 10, value: G.cfg.elo, style: 'max-width:180px', oninput: (e) => { G.cfg.elo = +e.target.value; lab.textContent = G.cfg.elo; save(); } })); strengthBox.appendChild(lab);
        }
      }
      const limBox = h('div', { class: 'inline' });
      function renderLimit() {
        A.clear(limBox);
        limBox.appendChild(h('select', { class: 'select', style: 'width:auto', onchange: (e) => { G.cfg.limit = e.target.value; save(); renderLimit(); } }, [['movetime', 'Time per move'], ['nodes', 'Nodes per move'], ['depth', 'Depth']].map(([v, l]) => h('option', { value: v, selected: G.cfg.limit === v }, l))));
        const k = G.cfg.limit;
        limBox.appendChild(h('input', { class: 'input num', type: 'number', value: G.cfg[k], min: 1, onchange: (e) => { G.cfg[k] = parseInt(e.target.value, 10) || G.cfg[k]; save(); } }));
        limBox.appendChild(h('span', { class: 'muted' }, k === 'movetime' ? 'ms' : ''));
      }
      const colorBox = h('div', { class: 'seg' });
      function renderColor() { A.clear(colorBox); [['white', 'White'], ['black', 'Black'], ['random', 'Random']].forEach(([v, l]) => colorBox.appendChild(h('button', { class: G.cfg.color === v ? 'on' : '', onclick: () => { G.cfg.color = v; save(); renderColor(); } }, l))); }
      fenIn = h('input', { class: 'input', placeholder: 'Start from FEN (optional)', value: G.cfg.fen, onchange: (e) => { G.cfg.fen = e.target.value; save(); } });
      panelPlay = h('div', {}, field('Opponent', engSel('engine', () => renderStrength().then(renderLimit))), field('Strength', strengthBox), field('Thinking', limBox), field('You play', colorBox), field('Position', fenIn),
        h('div', { class: 'row', style: 'margin-top:10px' }, h('button', { class: 'btn primary', onclick: newGame }, 'New game'), h('button', { class: 'btn', onclick: undo }, 'Take back'), h('button', { class: 'btn danger', onclick: resign }, 'Resign'),
          h('a', { class: 'btn', href: A.url('/api/play/pgn'), download: '' }, 'PGN')));
      renderStrength().then(renderLimit); renderLimit(); renderColor();

      // analysis panel
      fenIn.id = 'fen-in';
      const anaFen = h('input', { class: 'input', placeholder: 'Paste a FEN and press Enter' });
      anaFen.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        try { const li = await A.post('/api/legal', { fen: anaFen.value.trim() }); G.AH = { fens: [li.fen], san: [], uci: [], checks: [li.check], ply: 0 }; anaFen.value = ''; G.sel = null; render(); analysePosition(); } catch (err) { A.toast(err.message, 'err'); }
      });
      panelAna = h('div', { class: 'hidden' }, field('Engine', engSel('aengine')),
        field('Lines', h('select', { class: 'select', style: 'width:auto', onchange: (e) => { G.cfg.mpv = +e.target.value; save(); if (G.analysing) analysePosition(); } }, [1, 2, 3, 4, 5].map((n) => h('option', { value: n, selected: G.cfg.mpv === n }, n)))),
        field('Position', anaFen),
        h('div', { class: 'row', style: 'margin-top:8px' }, h('button', { id: 'ana-btn', class: 'btn primary', onclick: toggleAnalysis }, 'Start analysis'), h('button', { class: 'btn', onclick: () => { G.AH = { fens: [START], san: [], uci: [], checks: [null], ply: 0 }; G.sel = null; render(); analysePosition(); } }, 'Reset board')),
        linesEl, h('div', { class: 'help', style: 'margin-top:6px' }, 'Move the pieces for both sides; the engine analyses whatever is on the board. Use the arrows or click a move to go back.'));
      seg = h('div', { class: 'seg' }, h('button', { 'data-m': 'game', class: 'on', onclick: () => setMode('game') }, 'Play'), h('button', { 'data-m': 'analysis', onclick: () => setMode('analysis') }, 'Analysis'));
      const side = h('div', { class: 'card', style: 'min-width:360px;max-width:520px;flex:1' }, h('div', { class: 'h' }, seg), panelPlay, panelAna, bannerEl, movesEl);
      el.appendChild(h('div', { style: 'display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;max-width:1300px;margin:0 auto' }, boardCol, side));
      renderLines(); renderAnaButton();
      setMode(A.pref('play_mode', 'game'));
      A.get('/api/play/state').then((s) => { if (!s.none) { G.st = s; G.flipped = s.human === 'black'; board.flip(G.flipped); render(); } }).catch(() => {});
    },
    onShow() { render(); },
  });
})();
