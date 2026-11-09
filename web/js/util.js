/* Kestrel Arena - shared helpers: DOM builder, API calls, server events, toasts, modals, formatting */
(function () {
  const A = (window.A = window.A || {});
  A.token = (document.querySelector('meta[name=arena-token]') || {}).content || '';
  A.state = { engines: [], tournaments: [], ui: {}, cpu: { logical: 8 } };
  A.modules = [];
  A.register = (m) => A.modules.push(m);

  // ---- DOM ---------------------------------------------------------------------------------------------------------
  const SVGNS = 'http://www.w3.org/2000/svg';
  function build(el, props, kids) {
    for (const k in props || {}) {
      const v = props[k];
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.setAttribute('class', v);
      else if (k === 'style') { if (typeof v === 'string') el.setAttribute('style', v); else Object.assign(el.style, v); }
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (!(el instanceof SVGElement) && (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected' || k === 'indeterminate')) el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    (function add(list) {
      for (const c of list) {
        if (c === null || c === undefined || c === false) continue;
        if (Array.isArray(c)) add(c);
        else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
      }
    })(kids);
    return el;
  }
  A.h = (tag, props, ...kids) => build(document.createElement(tag), props, kids);
  A.svg = (tag, props, ...kids) => build(document.createElementNS(SVGNS, tag), props, kids);
  A.$ = (s, r) => (r || document).querySelector(s);
  A.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  A.clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  A.esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  A.throttle = function (fn, ms) {
    let timer = null, last = 0;
    return function () {
      const now = Date.now();
      if (timer) return;
      const wait = Math.max(0, ms - (now - last));
      timer = setTimeout(() => { timer = null; last = Date.now(); fn(); }, wait);
    };
  };

  // ---- API ---------------------------------------------------------------------------------------------------------
  A.api = async function (method, path, body) {
    const opt = { method, headers: { 'X-Arena-Token': A.token } };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, opt); } catch (e) { throw new Error('Cannot reach the Kestrel Arena server (is the app still running?)'); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
    return data;
  };
  A.get = (p) => A.api('GET', p);
  A.post = (p, b) => A.api('POST', p, b === undefined ? {} : b);
  A.url = (p) => p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(A.token);

  // ---- server events -----------------------------------------------------------------------------------------------
  const listeners = {};
  A.on = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
  A.emit = (type, ev) => { (listeners[type] || []).forEach((fn) => { try { fn(ev); } catch (e) { console.error(type, e); } }); };
  A.connect = function () {
    const es = new EventSource(A.url('/api/events'));
    es.onmessage = (m) => { let ev; try { ev = JSON.parse(m.data); } catch (e) { return; } A.emit(ev.type, ev); A.emit('*', ev); };
    es.onerror = () => A.emit('disconnected', {});
    A.es = es;
  };

  // ---- toast & modal -----------------------------------------------------------------------------------------------
  A.toast = function (msg, kind, ms) {
    const t = A.h('div', { class: 'toast ' + (kind || '') }, msg);
    A.$('#toast-root').appendChild(t);
    setTimeout(() => t.remove(), ms || (kind === 'err' ? 6500 : 3200));
  };
  A.modal = function (o) {
    const root = A.$('#modal-root');
    const back = A.h('div', { class: 'modal-back' });
    const close = () => { back.remove(); document.removeEventListener('keydown', onkey); };
    const onkey = (e) => { if (e.key === 'Escape' && o.dismiss !== false) close(); };
    document.addEventListener('keydown', onkey);
    const foot = A.h('footer', {}, (o.buttons || []).map((b) => A.h('button', {
      class: 'btn ' + (b.cls || ''), onclick: async () => { const r = b.onClick ? await b.onClick(api) : undefined; if (r !== false) close(); }
    }, b.label)));
    const box = A.h('div', { class: 'modal', style: o.width ? { width: o.width } : null }, A.h('header', {}, o.title || ''), A.h('div', { class: 'body' }, o.body), (o.buttons && o.buttons.length) ? foot : null);
    back.appendChild(box);
    back.addEventListener('mousedown', (e) => { if (e.target === back && o.dismiss !== false) close(); });
    root.appendChild(back);
    const api = { close, el: box };
    return api;
  };
  A.confirm = (title, text, okLabel, danger) => new Promise((res) => {
    A.modal({ title, body: A.h('div', {}, text), width: '460px', dismiss: true,
      buttons: [{ label: 'Cancel', onClick: () => res(false) }, { label: okLabel || 'OK', cls: danger ? 'danger' : 'primary', onClick: () => res(true) }] });
  });

  // ---- formatting --------------------------------------------------------------------------------------------------
  const F = (A.fmt = {});
  F.dur = (s) => {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    if (h) return h + 'h ' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm ' + String(x).padStart(2, '0') + 's';
    return x + 's';
  };
  F.clock = (ms) => {
    if (ms === null || ms === undefined) return '-';
    ms = Math.max(0, ms);
    const s = ms / 1000, m = Math.floor(s / 60);
    if (s < 10) return '0:0' + s.toFixed(1);
    return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  };
  F.eval = (cp) => {
    if (cp === null || cp === undefined) return '';
    if (Math.abs(cp) >= 29000) return (cp > 0 ? '+' : '-') + 'M' + (30000 - Math.abs(cp));
    return (cp > 0 ? '+' : cp < 0 ? '' : '') + (cp / 100).toFixed(2);
  };
  F.pct = (x, d) => (x === null || x === undefined || isNaN(x) ? '-' : (100 * x).toFixed(d === undefined ? 1 : d) + '%');
  F.pts = (x) => { const s = (Math.round(x * 2) / 2).toString(); return s; };
  F.num = (n) => (n === null || n === undefined ? '-' : n >= 1e9 ? (n / 1e9).toFixed(1) + 'G' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(n));
  F.sign = (x, d) => (x >= 0 ? '+' : '-') + Math.abs(x).toFixed(d === undefined ? 0 : d);

  // ---- persisted UI preferences (server side settings.json) ---------------------------------------------------------
  let saveTimer = null;
  A.setPref = function (k, v) {
    A.state.ui[k] = v;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => A.post('/api/settings/save', { ui: A.state.ui }).catch(() => {}), 600);
  };
  A.pref = (k, d) => (A.state.ui[k] === undefined ? d : A.state.ui[k]);
})();
