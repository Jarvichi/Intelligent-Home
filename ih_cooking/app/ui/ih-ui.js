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
