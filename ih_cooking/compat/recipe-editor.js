/* Compatibility controls for retained recipe editors. No API credentials. */
window.IHRecipeCompat = (() => {
  let entries = [];
  function mountSteps(host, steps) {
    entries = steps.map((step, i) => step._new_step ? Object.assign({}, step) : Object.assign({}, step, {
      _original_index: step._original_index ?? i,
      _original_text: step._original_text ?? step.text
    }));
    function draw() {
      host.replaceChildren();
      entries.forEach((step, index) => {
        const row = document.createElement('fieldset');
        row.style.cssText = 'margin:0 0 12px;min-width:0;border:1px solid var(--line,#ddd);border-radius:8px;padding:12px';
        const legend = document.createElement('legend'); legend.textContent = 'Step ' + (index + 1); row.append(legend);
        const minutes = document.createElement('input'); minutes.type = 'number'; minutes.min = '0'; minutes.max = '600'; minutes.value = step.before;
        minutes.setAttribute('aria-label', 'Minutes before serving for step ' + (index + 1));
        minutes.oninput = () => { step.before = minutes.value === '' ? NaN : Number(minutes.value); };
        const label = document.createElement('label'); label.textContent = 'Minutes before serving'; label.append(minutes); row.append(label);
        const text = document.createElement('textarea'); text.value = step.text; text.setAttribute('aria-label', 'Instruction for step ' + (index + 1)); text.oninput = () => { step.text = text.value; };
        const instruction = document.createElement('label'); instruction.textContent = 'Instruction'; instruction.append(text); row.append(instruction);
        const actions = document.createElement('div'); actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap';
        for (const [title, move, disabled] of [['Move up', -1, index === 0], ['Move down', 1, index === entries.length - 1], ['Remove', 0, false]]) {
          const button = document.createElement('button'); button.type = 'button'; button.textContent = title; button.disabled = disabled;
          button.style.minHeight = '44px'; button.setAttribute('aria-label', title + ' step ' + (index + 1));
          button.onclick = () => { if (move) [entries[index], entries[index + move]] = [entries[index + move], entries[index]]; else entries.splice(index, 1); draw(); };
          actions.append(button);
        }
        row.append(actions); host.append(row);
      });
      const add = document.createElement('button'); add.type = 'button'; add.textContent = 'Add step'; add.style.minHeight = '44px';
      add.onclick = () => { entries.push({before: 0, text: '', _new_step: true}); draw(); }; host.append(add);
    }
    draw(); labelFields();
  }
  function labelFields() {
    for (const label of document.querySelectorAll('label:not([for])')) {
      const field=label.nextElementSibling;
      if (field?.matches('input[id],select[id],textarea[id]')) label.htmlFor=field.id;
    }
  }
  function readSteps() { return entries.map(step => Object.assign({}, step)); }
  function requestId() { const bytes = crypto.getRandomValues(new Uint8Array(16)); return [...bytes].map(v => v.toString(16).padStart(2, '0')).join(''); }
  async function waitForSave(id) {
    for (let attempt = 0; attempt < 60; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const response = await fetch('/local/recipes/save-status/' + id + '.json?t=' + Date.now(), {cache: 'no-store'});
      if (!response.ok) { if (response.status === 404) continue; throw Error('Could not check the save. Reload before retrying.'); }
      const result = await response.json();
      if (result.state === 'saved') return result;
      if (result.state === 'error') throw Error(result.error);
    }
    throw Error('Save is taking longer than expected. Reload before retrying.');
  }
  return {mountSteps, readSteps, requestId, waitForSave, labelFields};
})();
