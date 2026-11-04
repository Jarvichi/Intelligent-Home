/* Safe, local note rendering. Source text is retained separately by the reader.
 * No raw HTML, remote embeds or external image requests are executed.
 */
(() => {
  const element = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
  function slug(text) { return 'note-' + text.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s+/g, '-'); }
  function body(source) { return source.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').replace(/^---\n[\s\S]*?\n---(?:\n|$)/, ''); }
  function safeURL(value) {
    const raw = value.trim().replace(/^<|>$/g, '');
    if (/[\u0000-\u0020\u007f]/.test(raw)) return null;
    try { const url = new URL(raw); return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url.href : null; }
    catch (_) { return null; }
  }
  function tableCells(line) {
    const cells = []; let value = '', code = 0;
    line = line.trim(); if (line.startsWith('|')) line = line.slice(1); if (line.endsWith('|') && !line.endsWith('\\|')) line = line.slice(0, -1);
    for (let i = 0; i < line.length; i++) {
      if (line[i] === '\\' && line[i + 1]) { value += line[i + 1] === '|' ? '|' : line[i] + line[i + 1]; i++; }
      else if (line[i] === '`') { let count = 1; while (line[i + count] === '`') count++; value += '`'.repeat(count); if (!code) code = count; else if (code === count) code = 0; i += count - 1; }
      else if (line[i] === '|' && !code) { cells.push(value.trim()); value = ''; }
      else value += line[i];
    }
    cells.push(value.trim()); return cells;
  }
  function render(source, options = {}) {
    const article = element('article'); article.className = 'note-markdown';
    const lines = body(source).split('\n'), references = new Map(), headingCounts = new Map();
    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/^ {0,3}\[([^\]^]+)\]:\s*(\S+)(?:\s+["'].*["'])?\s*$/);
      if (match) { references.set(match[1].toLowerCase(), match[2]); lines[i] = ''; }
    }
    function noteLink(target, label) {
      const parts = target.split('#'), path = parts.shift(), fragment = parts.join('#');
      if (!path && fragment) { const a = element('a', label); a.href = '#' + slug(fragment); return a; }
      const resolved = options.resolveNote?.(path);
      if (resolved) { const a = element('a', label); a.href = '#'; a.dataset.notePath = resolved; if (fragment) a.dataset.noteHeading = slug(fragment); return a; }
      const span = element('span', label); span.className = 'unresolved-note'; span.title = 'Note reference: ' + target; return span;
    }
    function link(target, label, image) {
      if (image) { const span = element('span', 'Image: ' + (label || target)); span.className = 'note-attachment'; span.title = target; return span; }
      if (/^#/.test(target)) { const a = element('a', label); a.href = '#' + slug(target.slice(1)); return a; }
      if (!/^[a-z][a-z\d+.-]*:/i.test(target) && /\.md(?:#|$)/i.test(target)) return noteLink(target, label);
      const href = safeURL(target);
      if (!href) { const span = element('span', label); span.title = target; return span; }
      const a = element('a', label); a.href = href; a.rel = 'noopener noreferrer'; a.target = '_blank'; a.title = href; return a;
    }
    function inline(parent, text, depth = 0) {
      if (depth > 12) { parent.append(document.createTextNode(text)); return; }
      let plain = ''; const flush = () => { if (plain) { parent.append(document.createTextNode(plain)); plain = ''; } };
      for (let i = 0; i < text.length;) {
        if (text[i] === '\\' && /[\\`*{}\[\]()#+\-.!_>|~]/.test(text[i + 1] || '')) { plain += text[i + 1]; i += 2; continue; }
        if (text[i] === '`') {
          const fence = text.slice(i).match(/^`+/)[0], end = text.indexOf(fence, i + fence.length);
          if (end >= 0) { flush(); parent.append(element('code', text.slice(i + fence.length, end))); i = end + fence.length; continue; }
        }
        const wiki = text.slice(i).match(/^(!?)\[\[([^\]\n]+)\]\]/);
        if (wiki) { flush(); const [target, ...aliases] = wiki[2].split('|'), label = aliases.join('|') || target.replace(/\.md(?:#|$)/i, ''); parent.append(wiki[1] ? link(target, label, true) : noteLink(target, label)); i += wiki[0].length; continue; }
        const opening = text[i] === '!' && text[i + 1] === '[' ? i + 1 : text[i] === '[' ? i : -1;
        if (opening >= 0) {
          const end = text.indexOf(']', opening + 1), label = end < 0 ? '' : text.slice(opening + 1, end); let target = '', consumed = 0;
          if (end >= 0 && text[end + 1] === '(') {
            let cursor = end + 2, balance = 1;
            for (; cursor < text.length; cursor++) { if (text[cursor] === '\\') { cursor++; continue; } if (text[cursor] === '(') balance++; if (text[cursor] === ')' && --balance === 0) break; }
            if (!balance) { target = text.slice(end + 2, cursor).trim().replace(/\s+["'][\s\S]*["']$/, ''); consumed = cursor + 1 - i; }
          } else if (end >= 0) {
            const ref = text.slice(end + 1).match(/^\[([^\]]*)\]/), key = (ref?.[1] || label).toLowerCase();
            if (references.has(key)) { target = references.get(key); consumed = end + 1 - i + (ref?.[0].length || 0); }
          }
          if (consumed) { flush(); const a = link(target, label, opening !== i); if (a.tagName === 'A') { a.replaceChildren(); inline(a, label, depth + 1); } parent.append(a); i += consumed; continue; }
        }
        const autolink = text.slice(i).match(/^<(https?:\/\/[^<>\s]+|mailto:[^<>\s]+)>/);
        if (autolink) { flush(); parent.append(link(autolink[1], autolink[1], false)); i += autolink[0].length; continue; }
        const marker = ['**', '__', '~~', '*', '_'].find(m => text.startsWith(m, i));
        if (marker && !(marker === '_' && /[\p{L}\p{N}]/u.test(text[i - 1] || ''))) {
          const end = text.indexOf(marker, i + marker.length);
          if (end > i + marker.length) { flush(); const tag = marker === '~~' ? 's' : marker.length === 2 ? 'strong' : 'em', node = element(tag); inline(node, text.slice(i + marker.length, end), depth + 1); parent.append(node); i = end + marker.length; continue; }
        }
        if (text[i] === '\n') { flush(); parent.append(element('br')); i++; continue; }
        plain += text[i++];
      }
      flush();
    }
    const fence = line => line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    const item = line => line.match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/);
    const tableRule = line => tableCells(line).every(cell => /^:?-{3,}:?$/.test(cell)) && line.includes('-');
    const blockStart = (rows, i) => /^\s*$|^ {0,3}#{1,6}\s|^ {0,3}>|^ {0,3}(?:\*\s*){3,}$|^ {0,3}(?:-\s*){3,}$|^ {0,3}(?:_\s*){3,}$/.test(rows[i]) || fence(rows[i]) || item(rows[i]) || (rows[i + 1] && rows[i].includes('|') && tableRule(rows[i + 1]));
    function blocks(parent, rows, level = 0) {
      if (level > 20) { parent.append(element('pre', rows.join('\n'))); return; }
      for (let i = 0; i < rows.length;) {
        const line = rows[i]; if (!line.trim()) { i++; continue; }
        const codeFence = fence(line);
        if (codeFence) {
          const content = []; i++; const closing = new RegExp('^ {0,3}' + codeFence[1][0] + '{' + codeFence[1].length + ',}\\s*$');
          while (i < rows.length && !closing.test(rows[i])) content.push(rows[i++]);
          if (i < rows.length) i++; const pre = element('pre'), code = element('code', content.join('\n')); pre.append(code); parent.append(pre); continue;
        }
        if (/^(?: {4}|\t)/.test(line)) { const content = []; while (i < rows.length && (/^(?: {4}|\t)/.test(rows[i]) || !rows[i].trim())) content.push(rows[i++].replace(/^(?: {4}|\t)/, '')); const pre = element('pre'); pre.append(element('code', content.join('\n').replace(/\n+$/, ''))); parent.append(pre); continue; }
        if (i + 1 < rows.length && /^ {0,3}(?:={3,}|-{3,})\s*$/.test(rows[i + 1]) && !blockStart(rows, i)) { const node = element('h3'); inline(node, line.trim()); node.id = slug(node.textContent); parent.append(node); i += 2; continue; }
        const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/);
        if (heading) {
          if (parent === article && !parent.childNodes.length && heading[1].length === 1 && heading[2].trim() === options.title) { i++; continue; }
          const node = element('h' + Math.min(6, Math.max(3, heading[1].length + 1))); inline(node, heading[2]); const key = slug(node.textContent), count = headingCounts.get(key) || 0; headingCounts.set(key, count + 1); node.id = key + (count ? '-' + count : ''); parent.append(node); i++; continue;
        }
        if (/^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line)) { parent.append(element('hr')); i++; continue; }
        if (/^ {0,3}>/.test(line)) { const quote = []; while (i < rows.length && /^ {0,3}>/.test(rows[i])) quote.push(rows[i++].replace(/^ {0,3}> ?/, '')); const node = element('blockquote'); blocks(node, quote, level + 1); parent.append(node); continue; }
        const first = item(line);
        if (first) {
          const ordered = /^\d/.test(first[2]), list = element(ordered ? 'ol' : 'ul'), indent = first[1].length;
          if (ordered && parseInt(first[2], 10) !== 1) list.start = parseInt(first[2], 10);
          while (i < rows.length) {
            const match = item(rows[i]); if (!match || match[1].length !== indent || /^\d/.test(match[2]) !== ordered) break;
            const node = element('li'), task = match[3].match(/^\[([ xX])\]\s+(.*)$/), content = [task ? task[2] : match[3]], padding = match[1].length + match[2].length + 1; i++;
            while (i < rows.length) {
              if (!rows[i].trim()) { if (i + 1 < rows.length && (rows[i + 1].match(/^\s*/)[0].length > indent || item(rows[i + 1])?.[1].length === indent)) { content.push(''); i++; continue; } break; }
              if (rows[i].match(/^\s*/)[0].length <= indent) break;
              const continuation = rows[i++]; content.push(continuation.slice(Math.min(padding, continuation.match(/^\s*/)[0].length)));
            }
            if (task) { node.className = 'note-task'; const check = element('input'); check.type = 'checkbox'; check.checked = task[1].toLowerCase() === 'x'; check.disabled = true; check.setAttribute('aria-label', check.checked ? 'Completed' : 'Not completed'); node.append(check); }
            blocks(node, content, level + 1); list.append(node);
          }
          parent.append(list); continue;
        }
        if (i + 1 < rows.length && line.includes('|') && tableRule(rows[i + 1])) {
          const headers = tableCells(line), data = []; i += 2; while (i < rows.length && rows[i].trim() && rows[i].includes('|')) data.push(tableCells(rows[i++]));
          const count = Math.max(headers.length, ...data.map(row => row.length)); while (headers.length < count) headers.push('Column ' + (headers.length + 1));
          const wrap = element('div'), table = element('table'), head = element('thead'), headerRow = element('tr'), tbody = element('tbody'); wrap.className = 'note-table'; table.setAttribute('role', 'table');
          const labels = headers.map(value => { const th = element('th'); th.scope = 'col'; inline(th, value); headerRow.append(th); return th.textContent; }); head.append(headerRow); table.append(head, tbody);
          for (const values of data) { const row = element('tr'); for (let c = 0; c < count; c++) { const td = element('td'); td.dataset.label = labels[c]; inline(td, values[c] || ''); row.append(td); } tbody.append(row); }
          wrap.append(table); parent.append(wrap); continue;
        }
        const paragraph = [line]; i++; while (i < rows.length && !blockStart(rows, i)) paragraph.push(rows[i++]);
        const node = element('p'), text = paragraph.map((value, index) => value.replace(/(?: {2,}|\\)$/, '\n') + (index < paragraph.length - 1 && !/(?: {2,}|\\)$/.test(value) ? ' ' : '')).join(''); inline(node, text); parent.append(node);
      }
    }
    blocks(article, lines); return article;
  }
  window.IHBrainMarkdown = Object.freeze({render, body, slug});
})();
