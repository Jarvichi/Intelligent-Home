(() => {
  // Brain page: one line with the shadow-mode local sorting result. Hidden while the feature is off and nothing was compared.
  const el = document.querySelector('#local-sort');
  if (!el) return;
  fetch(new URL('api/local-sort', location.href), {credentials: 'same-origin'}).then((r) => r.json()).then((d) => {
    if (!d || (!d.enabled && !d.compared)) return;
    el.textContent = 'Local note sorting (shadow mode, nothing is moved): ' + d.text + ' notes filed in the same folder as Claude' +
      (d.same_area !== undefined ? ' (' + d.same_area + ' in the same top-level area)' : '') + '.';
    el.hidden = false;
  }).catch(() => {});
})();
