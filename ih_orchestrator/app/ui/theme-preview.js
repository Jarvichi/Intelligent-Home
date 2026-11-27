(function () {
  'use strict';
  var palettes = window.IHTheme.palettes;
  var selected = 'neutral', mode = 'light';
  var box = document.getElementById('palettes');
  var status = document.getElementById('theme-status'), save = document.getElementById('save-theme');
  function apply() {
    window.IHTheme.apply({palette: selected, mode: mode});
    Array.prototype.forEach.call(box.querySelectorAll('button'), function (button) { button.setAttribute('aria-pressed', button.dataset.palette === selected ? 'true' : 'false'); });
    Array.prototype.forEach.call(document.querySelectorAll('[data-mode]'), function (button) { button.setAttribute('aria-pressed', button.dataset.mode === mode ? 'true' : 'false'); });
  }
  palettes.forEach(function (palette) {
    var button = document.createElement('button'), name = document.createElement('strong'), desc = document.createElement('small'), swatches = document.createElement('span');
    button.type = 'button'; button.className = 'preview-palette'; button.dataset.palette = palette.id;
    name.textContent = palette.name; desc.textContent = palette.description; swatches.className = 'preview-swatches'; swatches.setAttribute('aria-hidden', 'true');
    [palette.light[0], palette.light[3], palette.light[6]].forEach(function (colour) { var dot = document.createElement('span'); dot.style.backgroundColor = colour; swatches.appendChild(dot); });
    button.appendChild(name); button.appendChild(swatches); button.appendChild(desc);
    button.addEventListener('click', function () { selected = palette.id; apply(); }); box.appendChild(button);
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-mode]'), function (button) { button.addEventListener('click', function () { mode = button.dataset.mode; apply(); }); });
  document.getElementById('sample-add').addEventListener('click', function () { document.getElementById('sample-message').textContent = 'This is an example, so no task was created.'; });
  document.getElementById('sample-clear').addEventListener('click', function () { document.querySelector('.preview-search input').value = ''; document.querySelector('.preview-filter select').selectedIndex = 0; });
  save.addEventListener('click', async function () {
    save.disabled = true; status.textContent = 'Saving…';
    try {
      var response = await fetch('api/theme', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({palette: selected, mode: mode})});
      if (!response.ok) throw new Error('save failed');
      var choice = await response.json(); window.IHTheme.remember(choice); window.IHTheme.apply(choice);
      status.textContent = 'Theme saved for Intelligent Home.';
    } catch (e) { status.textContent = 'Could not save the theme. Please try again.'; }
    save.disabled = false;
  });
  fetch('api/theme', {cache: 'no-store'}).then(function (response) { if (!response.ok) throw new Error('load failed'); return response.json(); }).then(function (choice) {
    selected = choice.palette; mode = choice.mode; window.IHTheme.remember(choice); apply(); save.disabled = false; status.textContent = 'Your active theme. Changes are previewed until you save.';
  }).catch(function () { status.textContent = 'Could not load your active theme. Reload this page to try again.'; });
}());
