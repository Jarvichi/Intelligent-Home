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
