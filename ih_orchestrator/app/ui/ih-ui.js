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
