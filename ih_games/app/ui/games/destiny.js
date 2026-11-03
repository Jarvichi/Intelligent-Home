(function () {
  'use strict';
  const h = window.IHGameComponents = window.IHGameComponents || {};

  function objectives(milestones, gameId, el) {
    const groups = ['Hunter', 'Titan', 'Warlock'];
    const buckets = { Hunter: [], Titan: [], Warlock: [], Other: [] };
    (milestones || []).forEach(function (item) {
      buckets[groups.includes(item.class) ? item.class : 'Other'].push(item);
    });
    if (buckets.Other.length) groups.push('Other');
    const safeId = String(gameId || 'destiny').replace(/[^A-Za-z0-9_-]/g, '-');
    const card = el('ih-card', null, 'data-card objective-browser');
    const heading = el('div', null, 'objective-heading');
    heading.append(el('div', 'WEEKLY PROGRESS', 'eyebrow'), el('h2', 'Objectives by character'));
    heading.appendChild(el('span', milestones.length + ' objectives', 'count-pill'));
    card.appendChild(heading);

    const tabs = el('div', null, 'objective-tabs');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Filter weekly objectives by character class');
    const panels = [];
    const firstWithData = groups.findIndex(function (name) { return buckets[name].length > 0; });
    const selected = firstWithData < 0 ? 0 : firstWithData;
    groups.forEach(function (name, index) {
      const panelId = 'destiny-objectives-' + safeId + '-' + name.toLowerCase();
      const button = el('button', null, 'objective-tab');
      button.type = 'button'; button.id = panelId + '-tab'; button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', panelId); button.setAttribute('aria-selected', String(index === selected));
      button.tabIndex = index === selected ? 0 : -1;
      button.append(el('span', name), el('small', buckets[name].length));

      const panel = el('section', null, 'objective-panel');
      panel.id = panelId; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);
      panel.hidden = index !== selected;
      if (buckets[name].length) {
        const list = el('ul', null, 'objective-list');
        buckets[name].forEach(function (item) {
          const row = el('li', null, 'objective-row');
          row.append(el('span', item.name || 'Weekly objective', 'objective-name'), el('span', item.progress || 'In progress', 'objective-progress'));
          list.appendChild(row);
        });
        panel.appendChild(list);
      } else panel.appendChild(el('p', 'No weekly objectives for this class right now.', 'ih-muted objective-empty'));

      button.addEventListener('click', function () {
        tabs.querySelectorAll('[role="tab"]').forEach(function (tab) { tab.setAttribute('aria-selected', 'false'); tab.tabIndex = -1; });
        panels.forEach(function (entry) { entry.hidden = true; });
        button.setAttribute('aria-selected', 'true'); button.tabIndex = 0; panel.hidden = false;
      });
      button.addEventListener('keydown', function (event) {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const current = groups.indexOf(name);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? groups.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : groups.length - 1)) % groups.length;
        tabs.children[next].focus(); tabs.children[next].click();
      });
      tabs.appendChild(button); panels.push(panel);
    });
    card.append(tabs, ...panels);
    return card;
  }

  function render(data, api) {
    const wrap = api.el('div', null, 'details-content destiny-details');
    if (data.error) wrap.appendChild(api.el('p', data.error, 'notice notice-warning'));
    if (data.stale) wrap.appendChild(api.el('p', 'Showing the last saved Destiny data; the provider has not refreshed successfully.', 'notice notice-warning'));
    if (data.fetched) wrap.appendChild(api.el('p', 'Data refreshed ' + api.fmtDate(data.fetched), 'ih-muted'));
    if (data.resets) wrap.appendChild(api.section('Reset schedule', data.resets.map(function (item) {
      return api.row(item.kind === 'weekly' ? 'Weekly reset' : 'Daily reset', item.when + ' · ' + item.in);
    })));
    if (data.characters) wrap.appendChild(api.section('Characters', api.dataTable([
      { label: 'Class', value: function (item) { return item.class; } },
      { label: 'Power', value: function (item) { return item.power == null ? 'Unknown' : item.power; } },
      { label: 'Hours', value: function (item) { return item.hours; } },
      { label: 'Last played', value: function (item) { return api.fmtDate(item.last); } }
    ], data.characters)));
    if (data.milestones) wrap.appendChild(objectives(data.milestones, api.gameId, api.el));
    if (!wrap.childElementCount) wrap.appendChild(api.el('p', 'No Destiny details are available right now.', 'ih-muted'));
    return wrap;
  }

  h.destiny = { render: render };
}());
