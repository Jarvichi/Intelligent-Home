/* Meal-plan controls: add-on writes stay disabled until the coordinated handover. */
'use strict';
(() => {
  const $=s=>document.querySelector(s), days=[['mon','Monday'],['tue','Tuesday'],['wed','Wednesday'],['thu','Thursday'],['fri','Friday'],['sat','Saturday'],['sun','Sunday']];
  let loaded=false,revision=null,editable=false,recipeListHidden=false,recipeViewHidden=true;
  const panel=$('#plan-panel'),message=$('#plan-message'),daysBox=$('#plan-days'),save=$('#plan-save');
  function option(value,label){const node=document.createElement('option');node.value=value;node.textContent=label;return node;}
  async function request(path,opts){const response=await fetch(path,opts);const body=await response.json();if(!response.ok)throw body;return body;}
  async function load(){
    message.textContent='Loading the week…';daysBox.replaceChildren();save.hidden=true;revision=null;
    try{
      const [view,recipes]=await Promise.all([request('api/plan'),request('api/recipes')]);
      if(!view||!view.plan||!Array.isArray(recipes))throw Error('invalid response');
      editable=view.editable===true;revision=view.revision;
      for(const [day,label] of days){
        const wrapper=document.createElement('label');wrapper.textContent=label;
        const select=document.createElement('select');select.name=day;select.disabled=!editable;select.append(option('','Not planned'));
        for(const v of ['Freestyle','Eating out','Takeaway','Leftovers'])select.append(option(v,v));
        const existing=String(view.plan[day]||'');
        const available=new Set(['','Freestyle','Eating out','Takeaway','Leftovers']);
        for(const recipe of recipes){if(!recipe||typeof recipe.id!=='string'||typeof recipe.title!=='string'||available.has(recipe.id))continue;select.append(option(recipe.id,recipe.title));available.add(recipe.id);}
        if(existing&&!available.has(existing)){const match=recipes.find(r=>r.title===existing);select.append(option(existing,existing==='-'?'Not planned':(match?match.title:existing)+' (current)'));}
        select.value=existing;wrapper.append(select);daysBox.append(wrapper);
      }
      save.hidden=!editable;message.textContent=editable?'':'The current week is managed on the Lifestyle dashboard until the handover is complete.';loaded=true;
    }catch(e){message.textContent='Could not load the meal plan. Try again.';loaded=false;}
  }
  function showPlan(){
    if(panel.hidden){recipeListHidden=$('#list').hidden;recipeViewHidden=$('#view').hidden;}
    $('#list').hidden=true;$('#view').hidden=true;$('#feedback').hidden=true;$('#notes-status').hidden=true;panel.hidden=false;
    $('#tab-plan').setAttribute('aria-pressed','true');$('#tab-recipes').setAttribute('aria-pressed','false');if(!loaded)load();
  }
  function showRecipes(){panel.hidden=true;$('#list').hidden=recipeListHidden;$('#view').hidden=recipeViewHidden;$('#feedback').hidden=false;$('#notes-status').hidden=false;$('#tab-plan').setAttribute('aria-pressed','false');$('#tab-recipes').setAttribute('aria-pressed','true');}
  $('#tab-plan').addEventListener('click',showPlan);$('#tab-recipes').addEventListener('click',showRecipes);$('#plan-reload').addEventListener('click',load);
  $('#plan-form').addEventListener('submit',async e=>{
    e.preventDefault();if(!editable||!revision)return;
    const plan={};for(const [day] of days)plan[day]=daysBox.querySelector('select[name="'+day+'"]').value;
    save.disabled=true;message.textContent='Saving the week…';
    try{const result=await request('api/plan',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan,revision})});revision=result.revision;message.textContent='Week saved.';}
    catch(e){message.textContent=(e&&Array.isArray(e.errors)?e.errors.join(' '):'Could not save the week. Reload before trying again.');}
    finally{save.disabled=false;}
  });
})();
