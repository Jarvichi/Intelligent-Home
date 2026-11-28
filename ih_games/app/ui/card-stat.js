/* Pure helpers for the game card (no DOM): the stat box next to "Last played" and the per-tier trophy list.
   Loaded by index.html before ui.js; also loaded by the unit tests under Node. */
(function (scope) {
  'use strict';
  const STAT_ORDER = ['quests', 'trophies', 'playtime'];   // first kind present wins; none = no box
  const MINUTES_PER_HOUR = 60;
  const TROPHY_MARK = '🏆';
  const TIER_LABELS = { platinum: 'Platinum', gold: 'Gold', silver: 'Silver', bronze: 'Bronze' };

  function wholeNumber(value) {
    const n = Number(value);
    return value !== null && value !== undefined && Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  }
  function formatPlaytime(value) {
    const minutes = wholeNumber(value);
    if (minutes === null) return 'Unavailable';
    const hours = Math.floor(minutes / MINUTES_PER_HOUR);
    const remainder = minutes % MINUTES_PER_HOUR;
    if (!hours) return minutes.toLocaleString() + ' min';
    return hours.toLocaleString() + (hours === 1 ? ' hour' : ' hours') + (remainder ? ' ' + remainder + ' min' : '');
  }
  function playtimeBox(found, formatDate) {
    const minutes = found.reduce(function (sum, s) { return sum + (wholeNumber(s.minutes) || 0); }, 0);
    if (!minutes) return null;
    const sinceDates = found.filter(function (s) { return s.tracked && s.since; }).map(function (s) { return String(s.since); }).sort();
    const allTracked = found.every(function (s) { return s.tracked; });
    const note = allTracked && sinceDates.length ? 'Tracked since ' + (formatDate ? formatDate(sinceDates[0]) : sinceDates[0]) : '';   // shown as an info tooltip
    return { kind: 'playtime', label: 'Play time', value: formatPlaytime(minutes), note: note };
  }
  /* The play time box on its own, for a card that also shows quests or trophies. Same text as the playtime stat; null = none. */
  function cardPlaytime(game, formatDate) {
    const members = game && Array.isArray(game.platformEntries) && game.platformEntries.length ? game.platformEntries : [game || {}];
    const found = members.map(function (m) { return m && m.card_playtime; }).filter(function (s) { return s && s.kind === 'playtime'; });
    return found.length ? playtimeBox(found, formatDate) : null;
  }
  /* game: a library entry, or a merged entry with platformEntries. formatDate(value) is used for "since" dates.
     Returns {kind, label, value} or null (hide the box). */
  function cardStat(game, formatDate) {
    const members = game && Array.isArray(game.platformEntries) && game.platformEntries.length ? game.platformEntries : [game || {}];
    const stats = members.map(function (m) { return m && m.card_stat; }).filter(function (s) { return s && s.kind; });
    for (let i = 0; i < STAT_ORDER.length; i += 1) {
      const kind = STAT_ORDER[i];
      const found = stats.filter(function (s) { return s.kind === kind; });
      if (!found.length) continue;
      if (kind === 'playtime') {
        const box = playtimeBox(found, formatDate);
        if (!box) continue;
        return box;
      }
      const stat = found[0];
      const total = wholeNumber(stat.total);
      if (!total) continue;
      if (kind === 'quests') {
        const done = wholeNumber(stat.done);
        if (done === null) continue;
        return { kind: kind, label: stat.label || 'Quests', value: done + ' / ' + total };
      }
      const earned = wholeNumber(stat.earned);
      if (earned === null) continue;
      const box = { kind: kind, label: 'Trophies', value: earned + ' out of ' + total + ' ' + TROPHY_MARK };
      if (stat.stale) {   // the last PSN refresh failed: these numbers are old
        box.stale = true;
        box.note = 'Out of date' + (stat.as_of ? ', last updated ' + (formatDate ? formatDate(stat.as_of) : stat.as_of) : '');
      }
      return box;
    }
    return null;
  }
  /* listing: the /trophies answer. Returns {title, summary, rows} for one tier; rows keep the server order
     (earned first, newest first; then not earned). */
  function tierView(listing, tier) {
    const all = listing && Array.isArray(listing.trophies) ? listing.trophies : [];
    const rows = all.filter(function (t) { return t && t.tier === tier; });
    const earned = rows.filter(function (t) { return t.earned; }).length;
    const name = TIER_LABELS[tier] || String(tier || 'Trophy');
    const complete = !!(listing && listing.complete);
    return {
      title: name + ' trophies',
      summary: complete ? earned + ' of ' + rows.length + ' earned' : earned + ' earned with a recorded name',
      complete: complete,
      rows: rows
    };
  }
  const api = { cardStat: cardStat, cardPlaytime: cardPlaytime, formatPlaytime: formatPlaytime, tierView: tierView, STAT_ORDER: STAT_ORDER, TIER_LABELS: TIER_LABELS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.IHGamesCardStat = api;
}(typeof window !== 'undefined' ? window : this));
