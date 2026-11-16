'use strict';
(() => {
  const $ = s => document.querySelector(s), editor = $('#editor'), form = $('#form');
  let items = [], projections = [], series = {}, recipients = [], current = null, tab = 'leases';
  const field = name => form.elements.namedItem(name);
  const money = n => new Intl.NumberFormat('en-GB', {style:'currency', currency:'GBP'}).format(n);
  function group(c) { const k = c.kind.toLowerCase(); return k.includes('lease') ? 'leases' : k.includes('insurance') ? 'insurance' : k.includes('loan') || k.includes('mortgage') ? 'loans' : 'other'; }
  function text(tag, value, cls) { const el = document.createElement(tag); el.textContent = value; if (cls) el.className = cls; return el; }
  function metric(parent, label, value, hint) { const item=document.createElement('div'); item.append(text('div',label,'muted'),text('div',value,'amount')); if(hint)item.append(text('div',hint,'muted')); parent.append(item); }
  function graph(points) {
    if (!Array.isArray(points) || points.length < 2 || !(points[0].balance > 0)) return null;
    const box=document.createElement('section');box.className='balance-graph';box.append(text('h3','Projected balance'),text('p','Based on scheduled payments, including past payments assumed to have been made.','muted'));
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 600 190');svg.setAttribute('role','img');svg.setAttribute('aria-label','Recorded balance '+money(points[0].balance)+' on '+points[0].date+'; projected balance '+money(points[points.length-1].balance)+' after '+(points.length-1)+' scheduled payments.');
    function shape(tag,attrs,label){const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));if(label)node.textContent=label;svg.append(node);return node;}
    for(const y of [20,85,150])shape('line',{x1:65,y1:y,x2:575,y2:y,class:'graph-grid'});
    shape('text',{x:5,y:25,class:'graph-label'},money(points[0].balance));shape('text',{x:5,y:90,class:'graph-label'},money(points[0].balance/2));shape('text',{x:5,y:155,class:'graph-label'},money(0));
    const coords=points.map((p,i)=>[65+510*i/(points.length-1),150-130*p.balance/points[0].balance]);
    shape('polyline',{points:coords.map(([x,y])=>x+','+y).join(' '),class:'graph-line'});
    shape('circle',{cx:coords[0][0],cy:coords[0][1],r:6,class:'graph-recorded'});
    shape('circle',{cx:coords[coords.length-1][0],cy:coords[coords.length-1][1],r:5,class:'graph-end'});
    shape('text',{x:65,y:178,class:'graph-label'},points[0].date);shape('text',{x:575,y:178,class:'graph-label','text-anchor':'end'},points[points.length-1].date);
    box.append(svg,text('p','● Recorded balance · · · Scheduled estimate','muted'));
    const details=document.createElement('details'),summary=text('summary','See payment estimates');details.append(summary);
    const list=document.createElement('ol');for(const [i,p] of points.entries())list.append(text('li',(i===0?'Recorded: ':'Scheduled: ')+p.date+' — '+money(p.balance)));details.append(list);box.append(details);return box;
  }
  function render() {
    const list=$('#list'); list.replaceChildren(); const filtered=items.filter(c=>group(c)===tab);
    if (!filtered.length) { const box=text('div','No '+tab+' recorded.','card empty');list.append(box);return; }
    for(const c of filtered) {
      const p=projections.find(x=>x.id===c.id)||{}, card=document.createElement('article');card.className='card';
      const head=document.createElement('div');head.className='cardhead';const title=document.createElement('div');title.append(text('h2',c.name),text('p',c.kind+(c.end?' · '+(group(c)==='insurance'?'renewal or end ':'ends ')+c.end:' · end date not recorded')));
      const edit=text('button','Edit');edit.type='button';edit.setAttribute('aria-label','Edit '+c.name);edit.onclick=()=>open(c);head.append(title,edit);card.append(head);
      const grid=document.createElement('div');grid.className='grid';
      if(c.monthly_payment!==undefined)metric(grid,'Monthly payment',money(c.monthly_payment));
      if(p.projected_balance!==undefined)metric(grid,'Projected outstanding',money(p.projected_balance),p.remaining_payments+' payments remaining');
      else if(p.estimated_scheduled_total!==undefined)metric(grid,'Estimated scheduled total',money(p.estimated_scheduled_total),p.estimated_payments+' estimated payments');
      else if(c.observed_balance)metric(grid,'Last recorded balance',money(c.observed_balance.amount),'Recorded '+c.observed_balance.as_of+' · not a current balance');
      else if(group(c)==='loans' && c.balance_snapshot)metric(grid,'Last recorded balance',money(c.balance_snapshot.amount),'Recorded '+c.balance_snapshot.as_of+' · not a current balance');
      if(c.settlement_quote)metric(grid,'Early settlement quote',money(c.settlement_quote.amount),'Quoted '+c.settlement_quote.as_of);
      card.append(grid);const chart=graph(series[c.id]);if(chart)card.append(chart);else if(p.projected_balance!==undefined)card.append(text('p','The projected balance assumes scheduled payments have been made.','muted'));list.append(card);
    }
  }
  function open(c) {
    current=c || null;form.reset();$('#form-status').textContent='';$('#edit-title').textContent=c?'Edit contract':'Add contract';
    const v=c||{};for(const key of ['name','start','end','monthly_payment','payment_day'])field(key).value=v[key]??'';
    const known=Array.from(field('kind').options).some(option=>option.value===v.kind && option.value!=='__custom__');field('kind').value=known?v.kind:v.kind?'__custom__':'';field('kind_custom').value=known?'':v.kind||'';syncType();
    field('balance').value=(v.observed_balance||v.balance_snapshot)?.amount??'';field('balance_date').value=(v.observed_balance||v.balance_snapshot)?.as_of??'';
    field('settlement').value=v.settlement_quote?.amount??'';field('settlement_date').value=v.settlement_quote?.as_of??'';
    field('remind_months').value=(v.remind_months||[]).join(', ');
    const box=$('#recipients');box.replaceChildren();for(const person of recipients) {const l=document.createElement('label'),i=document.createElement('input');i.type='checkbox';i.value=person;i.checked=(v.who||[]).includes(person);l.append(i,document.createTextNode(person));box.append(l);}
    const missing=(v.who||[]).filter(w=>!recipients.includes(w));$('#recipient-help').textContent=missing.length?'Some existing recipients are not configured. Configure them before editing this record.':recipients.length?'':'No reminder recipients are configured.';
    editor.hidden=false;editor.scrollIntoView({behavior:'smooth',block:'start'});field('name').focus();
  }
  async function load() {
    $('#status').textContent='Loading contracts…';try {const r=await fetch('api/contracts',{cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();items=data.contracts;projections=data.projections;series=data.series||{};recipients=data.recipients;$('#status').textContent='';render();}
    catch(e){$('#status').textContent='Could not load contracts. Please try again.';}
  }
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));editor.hidden=true;render();});
  function syncType(){const custom=field('kind').value==='__custom__';$('#custom-type-label').hidden=!custom;field('kind_custom').required=custom;}
  field('kind').addEventListener('change',syncType);
  $('#add').onclick=()=>open(null);$('#close').onclick=$('#cancel').onclick=()=>{editor.hidden=true;};
  form.addEventListener('submit',async e=>{e.preventDefault();const v=current?JSON.parse(JSON.stringify(current)):{};
    for(const key of ['name','start','end'])v[key]=field(key).value.trim();v.kind=field('kind').value==='__custom__'?field('kind_custom').value.trim():field('kind').value;
    if(!v.kind){$('#form-status').textContent='Choose a contract type.';return;}
    for(const key of ['monthly_payment','payment_day']){const value=field(key).value;if(value==='')delete v[key];else v[key]=Number(value);}
    const balance=field('balance').value, balanceDate=field('balance_date').value, settlement=field('settlement').value, settlementDate=field('settlement_date').value;
    if((balance==='')!==(!balanceDate)||(settlement==='')!==(!settlementDate)){ $('#form-status').textContent='Enter both the amount and date, or leave both blank.';return; }
    if(group(v)==='loans'){delete v.balance_snapshot;if(balance!=='')v.observed_balance={amount:Number(balance),as_of:balanceDate};else delete v.observed_balance;}
    else {if(balance!=='')v.balance_snapshot={amount:Number(balance),as_of:balanceDate};else delete v.balance_snapshot;}
    if(settlement!=='')v.settlement_quote={amount:Number(settlement),as_of:settlementDate};else delete v.settlement_quote;
    const reminders=field('remind_months').value.trim();if(reminders){const parts=reminders.split(',').map(x=>x.trim());if(parts.some(x=>!/^\d+$/.test(x)||Number(x)<1||Number(x)>120)){ $('#form-status').textContent='Enter reminder months as numbers from 1 to 120, separated by commas.';return; }v.remind_months=parts.map(Number);}else delete v.remind_months;
    const selected=Array.from($('#recipients').querySelectorAll('input:checked'),x=>x.value);
    const missing=(v.who||[]).filter(w=>!recipients.includes(w));if(missing.length){$('#form-status').textContent='Configure the existing reminder recipients before saving.';return;}v.who=selected;
    $('#form-status').textContent='Saving…';try{const r=await fetch('api/contracts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});const body=await r.json();if(!r.ok)throw Error(body.error||'Could not save.');editor.hidden=true;tab=group(v);document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x.dataset.tab===tab)));await load();$('#status').textContent='Contract saved. The dashboard refreshes hourly.';}
    catch(err){$('#form-status').textContent=err.message||'Could not save. Please try again.';}
  });load();
})();
