/* Dependency-free IH component primitives. Page CSS uses the canonical tokens.css. */
(() => {
  const defs = {
    'ih-app': 'main', 'ih-header': 'header', 'ih-tabs': 'nav', 'ih-card': 'section',
    'ih-status': 'span', 'ih-badge': 'span', 'ih-metric': 'div', 'ih-button': 'button',
    'ih-field': 'div', 'ih-table': 'div', 'ih-empty': 'div', 'ih-loading': 'div',
    'ih-error': 'div', 'ih-dialog': 'dialog'
  };
  for (const [name, tag] of Object.entries(defs)) {
    if (!customElements.get(name)) customElements.define(name, class extends HTMLElement {
      connectedCallback() {
        this.setAttribute('data-ih-component', name);
        if (name === 'ih-button' && !this.hasAttribute('type')) this.setAttribute('type', 'button');
      }
    });
  }
})();

/* Reusable behaviour for native disclosures and accessible tabs. Pages own their content. */
window.IHUI = Object.assign(window.IHUI || {}, {
  menu(details, onClose) {
    const close = (focus) => {
      details.open = false;
      if (typeof onClose === 'function') onClose();
      const summary = details.querySelector('summary');
      if (focus && summary) summary.focus();
    };
    const click = (event) => { if (details.open && !details.contains(event.target)) close(false); };
    const key = (event) => { if (details.open && event.key === 'Escape') { event.preventDefault(); close(true); } };
    document.addEventListener('click', click);
    document.addEventListener('keydown', key);
    return { close, destroy() { document.removeEventListener('click', click); document.removeEventListener('keydown', key); } };
  },
  tabs(tablist, panels) {
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    if (!tabs.length || tabs.length !== panels.length) return;
    const select = (index, focus) => {
      tabs.forEach((tab, i) => { tab.setAttribute('aria-selected', i === index ? 'true' : 'false'); tab.tabIndex = i === index ? 0 : -1; panels[i].hidden = i !== index; });
      if (focus) tabs[index].focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => select(i, false));
      tab.addEventListener('keydown', (event) => {
        const next = event.key === 'ArrowRight' ? (i + 1) % tabs.length : event.key === 'ArrowLeft' ? (i + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); select(next, true); }
      });
    });
    select(Math.max(0, tabs.findIndex(tab => tab.getAttribute('aria-selected') === 'true')), false);
    return { select };
  }
});

/* IHToast.show('Saved'): shadcn style toast, text via textContent, gone after 4 s. */
window.IHToast = {
  show(text, ms) {
    let r = document.querySelector('.toast-region');
    if (!r) { r = document.createElement('div'); r.className = 'toast-region'; r.setAttribute('role', 'status'); r.setAttribute('aria-live', 'polite'); document.body.appendChild(r); }
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = String(text);
    r.appendChild(t);
    setTimeout(() => t.remove(), ms || 4000);
  }
};

/* ih-nav: sticky top bar. Declarative: <ih-nav data-home-href="/app/32c8114c_ih_orchestrator" data-title="Jobs"></ih-nav>
   Without data-home-href (the hub itself) it shows the title only. Sub-screens call
   IHNav.setTrail([{label: 'Jobs', onClick: fn}], 'Request 12'): the bar then shows "‹ Jobs / Request 12"
   and the back button runs onClick. IHNav.setTrail([], title) returns to the top-level look.
   Text is always set with textContent. Links to other Home Assistant pages navigate the WHOLE window:
   the same-origin parent gets a Home Assistant style location change, else the link's target="_top". */
(() => {
  const HOME_LABEL = 'Intelligent Home';
  const goTop = (href) => {
    try {
      window.top.history.pushState(null, '', href);
      window.top.dispatchEvent(new CustomEvent('location-changed', { detail: { replace: false } }));
    } catch (e) {
      try { window.top.location.href = href; } catch (e2) { window.open(href, '_top'); }
    }
  };
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const sep = () => { const s = el('span', 'ih-nav-sep', '/'); s.setAttribute('aria-hidden', 'true'); return s; };
  class IHNavElement extends HTMLElement {
    constructor() { super(); this._trail = []; this._title = null; }
    connectedCallback() {
      if (!this.hasAttribute('role')) this.setAttribute('role', 'navigation');
      if (!this.hasAttribute('aria-label')) this.setAttribute('aria-label', 'Page navigation');
      this.setAttribute('data-ih-component', 'ih-nav');
      this.render();
    }
    static get observedAttributes() { return ['data-title', 'data-home-href']; }
    attributeChangedCallback() { if (this.isConnected) this.render(); }
    setTrail(trail, title) {
      this._trail = Array.isArray(trail) ? trail.filter((t) => t && t.label) : [];
      if (title !== undefined && title !== null) this._title = String(title);
      this.render();
    }
    render() {
      const title = this._title !== null ? this._title : (this.getAttribute('data-title') || '');
      const home = this.getAttribute('data-home-href');
      this.textContent = '';
      let back = null;
      if (this._trail.length) {
        const t = this._trail[this._trail.length - 1];
        back = el('button', 'ih-nav-link', '‹ ' + t.label);
        back.type = 'button';
        back.addEventListener('click', () => { if (typeof t.onClick === 'function') t.onClick(); });
      } else if (home) {
        back = el('a', 'ih-nav-link', '‹ ' + HOME_LABEL);
        back.setAttribute('href', home);
        back.setAttribute('target', '_top');
        back.addEventListener('click', (ev) => {
          if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
          ev.preventDefault();
          goTop(home);
        });
      }
      if (back) { this.appendChild(back); this.appendChild(sep()); }
      const here = el('span', 'ih-nav-here', title);
      if (title) here.title = title;
      here.setAttribute('aria-current', 'page');
      this.appendChild(here);
    }
  }
  if (!customElements.get('ih-nav')) customElements.define('ih-nav', IHNavElement);
  const bar = () => document.querySelector('ih-nav');
  window.IHNav = {
    goTop,
    setTrail(trail, title) { const b = bar(); if (b && b.setTrail) b.setTrail(trail, title); },
    setTitle(title) { const b = bar(); if (b && b.setTrail) b.setTrail(b._trail, title); }
  };
})();

/* The active appearance is stored by Orchestrator and shared over HA ingress. */
(() => {
  var palettes = [
    { id: 'neutral', name: 'Neutral', description: 'Crisp and understated', light: ['#ffffff','#171717','#ffffff','#f5f5f5','#737373','#e5e5e5','#171717','#fafafa'], dark: ['#0a0a0a','#fafafa','#171717','#262626','#a1a1a1','#404040','#e5e5e5','#171717'] },
    { id: 'coastal', name: 'Coastal', description: 'Cool blue, calm surfaces', light: ['#f6f9fc','#152a3d','#ffffff','#eaf1f7','#526b80','#cbdbe7','#235b86','#ffffff'], dark: ['#101b26','#edf5fb','#192b3a','#243b4d','#acc1d0','#385264','#8ec8ef','#102b40'] },
    { id: 'woodland', name: 'Woodland', description: 'Soft greens, natural warmth', light: ['#f7f9f5','#203029','#ffffff','#edf3ea','#5b7062','#cfddd0','#31694c','#ffffff'], dark: ['#141d18','#eef5ed','#1e2c23','#2a3b2e','#b2c5b3','#415547','#9bd4a9','#173525'] },
    { id: 'terracotta', name: 'Terracotta', description: 'Warm neutrals, earthy accent', light: ['#fcf8f5','#392820','#ffffff','#f6eee9','#82685c','#e8d8cf','#a64d31','#ffffff'], dark: ['#201713','#fff0e8','#30221c','#443029','#d0b7aa','#5e453a','#f0ab8e','#3d2015'] }
  ];
  const cacheKey = 'ih-active-theme-v1';
  const keys = ['--background','--foreground','--card','--muted','--muted-foreground','--border','--primary','--primary-foreground'];
  const valid = value => value && palettes.some(p => p.id === value.palette) && ['system','light','dark'].includes(value.mode);
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  let current = null;
  const apply = value => {
    if (!valid(value)) return;
    current = {palette: value.palette, mode: value.mode};
    const mode = value.mode === 'system' ? (media && media.matches ? 'dark' : 'light') : value.mode;
    const values = palettes.find(p => p.id === value.palette)[mode], style = document.documentElement.style;
    keys.forEach((key, i) => style.setProperty(key, values[i]));
    [['--card-foreground',1],['--popover',2],['--popover-foreground',1],['--secondary',3],['--secondary-foreground',1],['--accent',3],['--accent-foreground',1],['--input',5],['--ring',6]].forEach(([key, i]) => style.setProperty(key, values[i]));
    style.setProperty('color-scheme', mode);
  };
  const remember = value => { if (valid(value)) try { localStorage.setItem(cacheKey, JSON.stringify(value)); } catch (_) {} };
  const editor = document.documentElement.hasAttribute('data-ih-theme-editor');
  const refresh = async () => {
    if (editor) return;
    try {
      const hass = window.top.document.querySelector('home-assistant').hass;
      const info = await hass.callWS({type: 'supervisor/ingress/info', slug: '32c8114c_ih_orchestrator'});
      const url = new URL(info.ingress_url, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/hassio_ingress/')) return;
      url.pathname = url.pathname.replace(/\/$/, '') + '/api/theme'; url.search = ''; url.hash = '';
      const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) return;
      const value = await response.json(); if (valid(value)) { remember(value); apply(value); }
    } catch (_) { /* Keep the last known appearance while Orchestrator is unavailable. */ }
  };
  window.IHTheme = {apply, remember, refresh};
  if (media) { const changed = () => { if (current && current.mode === 'system') apply(current); }; if (media.addEventListener) media.addEventListener('change', changed); else if (media.addListener) media.addListener(changed); }
  if (!editor) {
    try { const saved = JSON.parse(localStorage.getItem(cacheKey)); if (valid(saved)) apply(saved); } catch (_) {}
    window.addEventListener('storage', e => { if (e.key === cacheKey) try { const value = JSON.parse(e.newValue); if (valid(value)) apply(value); } catch (_) {} });
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh(); setInterval(refresh, 30000);
  }
})();
