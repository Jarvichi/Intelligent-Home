(function () {
  'use strict';
  const h = window.IHGameComponents = window.IHGameComponents || {};

  function render(data, api) {
    const wrap = api.el('div', null, 'details-content arc-raiders-details');
    if (data.error) wrap.appendChild(api.el('p', data.error, 'notice notice-warning'));
    if (data.quests_completed != null) {
      wrap.appendChild(api.section('Quest progress', api.el('p', data.quests_completed + ' quests completed', 'ih-muted')));
    }
    if (data.open_quests) wrap.appendChild(api.section('Open quests (' + data.open_quests.length + ')', api.dataTable([
      { label: 'Quest', value: function (item) { return item.name; } },
      { label: 'Maps', value: function (item) { return (item.maps || []).join(', ') || 'Any map'; } }
    ], data.open_quests)));
    if (data.needs) wrap.appendChild(api.section('Needs and materials (' + data.needs.length + ')', api.dataTable([
      { label: 'Item', value: function (item) { return item.item; } },
      { label: 'Now', value: function (item) { return item.now; } },
      { label: 'Later', value: function (item) { return item.later; } },
      { label: 'Have', value: function (item) { return item.have; } },
      { label: 'Status', value: function (item) { return item.status; } },
      { label: 'For', value: function (item) { return item.for; } }
    ], data.needs)));
    if (data.maps) wrap.appendChild(api.section('Quest maps (' + data.maps.length + ')', api.dataTable([
      { label: 'Map', value: function (item) { return item.map; } },
      { label: 'Quests', value: function (item) { return item.count; } },
      { label: 'Quest names', value: function (item) { return (item.quests || []).join(', '); } }
    ], data.maps)));
    if (data.inventory) wrap.appendChild(api.section('Inventory advice (' + data.inventory.length + ')', api.dataTable([
      { label: 'Item', value: function (item) { return item.item; } },
      { label: 'Surplus', value: function (item) { return item.surplus; } },
      { label: 'Advice', value: function (item) { return item.action; } },
      { label: 'Value', value: function (item) { return item.gain; } }
    ], data.inventory)));
    if (!wrap.childElementCount) wrap.appendChild(api.el('p', 'No Arc Raiders details are available right now.', 'ih-muted'));
    return wrap;
  }

  h.arc_raiders = { render: render };
}());
