(() => {
 const status=document.querySelector('#list-status'),list=document.querySelector('#shopping-items'),search=document.querySelector('#list-search'),refresh=document.querySelector('#list-refresh');
 let items=[],checked='',available=false;
 function render(){const q=search.value.trim().toLocaleLowerCase(),matches=items.filter(i=>i.summary.toLocaleLowerCase().includes(q));list.replaceChildren();
 if(!available)return;
 status.textContent=matches.length+' of '+items.length+' items · read '+checked;
 if(!matches.length){const p=document.createElement('li');p.textContent=items.length?'No items match. Clear search to see the list.':'The shopping list is empty.';list.append(p)}
 for(const item of matches){const li=document.createElement('li');li.textContent=item.summary;list.append(li)}
 }
 async function load(){refresh.disabled=true;list.setAttribute('aria-busy','true');status.textContent='Reading shopping list…';
 try{const r=await fetch('api/list',{cache:'no-store'});if(!r.ok)throw Error();const j=await r.json();if(!j.available||!Array.isArray(j.items))throw Error();items=j.items;checked=new Date(j.checked_at*1000).toLocaleString();available=true;render()}
 catch(e){available=false;items=[];list.replaceChildren();status.textContent='Shopping list unavailable. Try refreshing; this does not mean the list is empty.'}
 finally{refresh.disabled=false;list.setAttribute('aria-busy','false')}
 }
 search.addEventListener('input',render);refresh.addEventListener('click',load);load();
})();
