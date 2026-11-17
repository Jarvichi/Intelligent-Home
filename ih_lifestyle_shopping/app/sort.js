/* Shopping display rules. The list's UIDs and source text stay untouched. */
'use strict';
function quantity(s) {
  let m = /^(.*?)\s+[x×]\s?(\d+)(\s*\(.*\))?\s*$/i.exec(s);
  if (m && m[1].trim()) return {name:(m[1]+(m[3]||'')).trim(),qty:parseInt(m[2],10)||1};
  m = /^(\d+)\s?[x×]\s+(.+)$/i.exec(s);
  if (m) return {name:m[2].trim(),qty:parseInt(m[1],10)||1};
  return {name:s.trim(),qty:1};
}
function rows(items) {
  const out=[], batteries=Object.create(null), seen=Object.create(null);
  items.forEach((item,pos)=>{
    const m=/^Batteries: (\d+)\s*[×x]\s*(\S+)/.exec(item.summary);
    if(m){const b=batteries[m[2]]||(batteries[m[2]]={qty:0,uids:[],pos,due:item.due||item.due_date||''});b.qty+=parseInt(m[1],10);b.uids.push(item.uid);return;}
    const q=quantity(item.summary),key=q.name.toLowerCase();
    if(seen[key]){seen[key].qty+=q.qty;seen[key].uids.push(item.uid);}
    else{seen[key]={label:q.name,qty:q.qty,uids:[item.uid],pos,due:item.due||item.due_date||''};out.push(seen[key]);}
  });
  Object.keys(batteries).sort().forEach(k=>{const b=batteries[k];out.push({label:k+' batteries × '+b.qty,uids:b.uids,batt:true,pos:b.pos,due:b.due});});
  return out;
}
function orderedGroups(items, mode, layout, storeId) {
  if (!['day','shop','added'].includes(mode)) throw new Error('invalid order');
  const list=rows(items),groups=Object.create(null);
  if(mode==='added')return [{label:'Added order',rows:list.sort((a,b)=>a.pos-b.pos)}];
  if(mode==='day'){
    list.forEach(r=>{const day=String(r.due||'').slice(0,10);(groups[day]||(groups[day]=[])).push(r);});
    return Object.keys(groups).sort((a,b)=>!a?1:!b?-1:a.localeCompare(b)).map(day=>({label:day,rows:groups[day].sort((a,b)=>a.pos-b.pos)}));
  }
  const stores=layout.stores.stores, store=stores.find(s=>s.id===storeId)||stores.find(s=>s.id===layout.stores.default)||stores[0];
  const rules=(layout.aisles.match||[]).map(m=>[m[0],new RegExp('\\b('+m[1]+')')]);
  const order=store.order||layout.aisles.order,rank=store.rank||layout.aisles.rank||{};
  list.forEach(r=>{const label=r.label.toLowerCase(),found=rules.find(rule=>rule[1].test(label)),key=found?found[0]:'Other';(groups[key]||(groups[key]=[])).push(r);});
  return order.concat(Object.keys(groups).filter(k=>!order.includes(k))).filter(k=>groups[k]).map(k=>({label:k,rows:groups[k].sort((a,b)=>{
    const av=rank[quantity(a.label).name.toLowerCase()]??2,bv=rank[quantity(b.label).name.toLowerCase()]??2;return av-bv||a.label.localeCompare(b.label);
  })}));
}
if(typeof module!=='undefined')module.exports={quantity,rows,orderedGroups};
