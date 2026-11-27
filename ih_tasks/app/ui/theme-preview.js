(function () {
  'use strict';
  var palettes = [
    { id: 'neutral', name: 'Neutral', description: 'Crisp and understated', light: ['#ffffff','#171717','#ffffff','#f5f5f5','#737373','#e5e5e5','#171717','#fafafa'], dark: ['#0a0a0a','#fafafa','#171717','#262626','#a1a1a1','#404040','#e5e5e5','#171717'] },
    { id: 'coastal', name: 'Coastal', description: 'Cool blue, calm surfaces', light: ['#f6f9fc','#152a3d','#ffffff','#eaf1f7','#526b80','#cbdbe7','#235b86','#ffffff'], dark: ['#101b26','#edf5fb','#192b3a','#243b4d','#acc1d0','#385264','#8ec8ef','#102b40'] },
    { id: 'woodland', name: 'Woodland', description: 'Soft greens, natural warmth', light: ['#f7f9f5','#203029','#ffffff','#edf3ea','#5b7062','#cfddd0','#31694c','#ffffff'], dark: ['#141d18','#eef5ed','#1e2c23','#2a3b2e','#b2c5b3','#415547','#9bd4a9','#173525'] },
    { id: 'terracotta', name: 'Terracotta', description: 'Warm neutrals, earthy accent', light: ['#fcf8f5','#392820','#ffffff','#f6eee9','#82685c','#e8d8cf','#a64d31','#ffffff'], dark: ['#201713','#fff0e8','#30221c','#443029','#d0b7aa','#5e453a','#f0ab8e','#3d2015'] }
  ];
  var selected = 'neutral', mode = 'light';
  var keys = ['--background','--foreground','--card','--muted','--muted-foreground','--border','--primary','--primary-foreground'];
  var box = document.getElementById('palettes');
  function apply() {
    var palette = palettes.filter(function (p) { return p.id === selected; })[0], values = palette[mode], root = document.documentElement;
    keys.forEach(function (key, i) { root.style.setProperty(key, values[i]); });
    root.style.setProperty('--card-foreground', values[1]);
    root.style.setProperty('--popover', values[2]); root.style.setProperty('--popover-foreground', values[1]);
    root.style.setProperty('--secondary', values[3]); root.style.setProperty('--secondary-foreground', values[1]);
    root.style.setProperty('--accent', values[3]); root.style.setProperty('--accent-foreground', values[1]);
    root.style.setProperty('--input', values[5]); root.style.setProperty('--ring', values[6]);
    root.style.setProperty('color-scheme', mode);
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
  apply();
}());
