'use strict';
(()=>{
  const $=s=>document.querySelector(s), form=$('#editor'), status=$('#status'), container=$('#profiles');
  let editing=null, rows=[];
  async function api(path,method='GET',body){const r=await fetch('api/v1/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'Request failed.');return value;}
  function message(s){status.textContent=s;}
  function clear(){editing=null;form.reset();$('#form-title').textContent='Add a display';}
  function field(name){return form.elements.namedItem(name);}
  function read(){return {name:field('name').value,kind:field('kind').value,width:Number(field('width').value),height:Number(field('height').value),colour:field('colour').value,power:field('power').value,transport:field('transport').value,refresh_seconds:Number(field('refresh_seconds').value),touch:field('touch').checked};}
  function edit(row){editing={id:row.id,revision:row.revision};for(const key of ['name','kind','width','height','colour','power','transport','refresh_seconds'])field(key).value=row[key];field('touch').checked=row.touch;$('#form-title').textContent='Edit display';form.scrollIntoView({behavior:'smooth',block:'nearest'});}
  function button(label,fn){const b=document.createElement('button');b.type='button';b.textContent=label;b.addEventListener('click',fn);return b;}
  function render(){container.replaceChildren();if(!rows.length){const p=document.createElement('p');p.textContent='No displays added yet.';container.append(p);return;}for(const row of rows){const box=document.createElement('article');box.className='profile';const h=document.createElement('h3');h.textContent=row.name;const p=document.createElement('p');p.className='muted';p.textContent=`${row.width} × ${row.height} · ${row.colour} · ${row.touch?'Touch':'No touch'} · ${row.refresh_seconds}s refresh`;const identifier=document.createElement('p');identifier.className='muted';identifier.textContent='Profile ID: '+row.id;const actions=document.createElement('div');actions.className='actions';actions.append(button('Edit',()=>edit(row)),button('Remove',async()=>{if(!confirm('Remove this display profile?'))return;try{await api('profiles/'+row.id,'DELETE',{revision:row.revision});if(editing&&editing.id===row.id)clear();await load();message('Display removed.');}catch(e){message(e.message);}}));box.append(h,p,identifier,actions);container.append(box);}}
  async function load(){const result=await api('profiles');rows=result.profiles;render();}
  $('#cancel').addEventListener('click',clear);
  form.addEventListener('submit',async e=>{e.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;try{const profile=read();if(editing)await api('profiles/'+editing.id,'PUT',{profile,revision:editing.revision});else await api('profiles','POST',profile);clear();await load();message('Display saved.');}catch(e){message(e.message);}finally{submit.disabled=false;}});
  load().catch(()=>message('Could not load displays. Try reloading the page.'));
})();
