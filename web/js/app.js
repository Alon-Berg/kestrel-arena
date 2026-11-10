/* Kestrel Arena - application shell: tabs, bootstrap, server events */
(function () {
  const A = window.A;
  const { h, $ } = A;
  let current = null;

  A.showTab = function (id) {
    current = id;
    for (const m of A.modules) {
      m.btn.classList.toggle('active', m.id === id);
      m.el.classList.toggle('hidden', m.id !== id);
      if (m.id === id && m.onShow) m.onShow();
    }
    A.setPref('tab', id);
  };

  A.refreshEngines = async function () {
    const b = await A.get('/api/bootstrap');
    A.state.engines = b.engines;
    A.emit('engines', {});
  };

  async function start() {
    let boot;
    try {
      boot = await A.get('/api/bootstrap');
    } catch (e) {
      $('#main').appendChild(h('div', { class: 'empty' }, h('b', {}, 'Cannot start'), e.message));
      return;
    }
    Object.assign(A.state, { engines: boot.engines, ui: boot.ui || {}, cpu: boot.cpu, tournaments: boot.tournaments, current: boot.current, adjDefaults: boot.adj_defaults, projectDir: boot.project_dir, dataDir: boot.data_dir, levels: boot.levels || { default: 1300, list: [] } });
    $('#ver').textContent = 'v' + boot.version;
    const tabs = $('#tabs'), main = $('#main');
    const ORDER = ['tournament', 'play', 'engines'];
    A.modules.sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
    for (const m of A.modules) {
      m.btn = h('button', { class: 'tab-btn', onclick: () => A.showTab(m.id) }, m.title, h('span', { class: 'dot hidden', id: 'dot-' + m.id }, '(running)'));
      m.el = h('section', { class: 'tab hidden', id: 'tab-' + m.id });
      tabs.appendChild(m.btn); main.appendChild(m.el);
      m.init(m.el);
    }
    A.connect();
    A.on('disconnected', () => { $('#top-status').textContent = 'connection lost, retrying...'; });
    A.on('hello', () => { $('#top-status').textContent = ''; });
    const first = A.pref('tab', 'tournament');
    A.showTab(A.modules.some((m) => m.id === first) ? first : A.modules[0].id);
    if (boot.current && ['running', 'paused', 'stopping'].includes(boot.current.status)) A.showTab('tournament');
  }

  A.on('tournament', (ev) => {
    const dot = $('#dot-tournament');
    if (dot) dot.classList.toggle('hidden', !['running', 'stopping'].includes(ev.status));
    const s = $('#top-status');
    if (s && ['running', 'paused', 'stopping'].includes(ev.status)) s.textContent = 'Tournament ' + ev.done + '/' + ev.total + (ev.status === 'paused' ? ' (paused)' : '');
    else if (s && s.textContent.startsWith('Tournament')) s.textContent = '';
  });

  window.addEventListener('DOMContentLoaded', start);
})();
