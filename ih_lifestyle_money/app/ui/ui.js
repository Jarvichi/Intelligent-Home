'use strict';
(() => {
  const $ = s => document.querySelector(s), editor = $('#editor'), form = $('#form');
  let items = [], projections = [], series = {}, recipients = [], current = null, tab = 'leases';
  const field = name => form.elements.namedItem(name);
  const money = n => new Intl.NumberFormat('en-GB', {style:'currency', currency:'GBP'}).format(n);
  function group(c) { const k = c.kind.toLowerCase(); return k.includes('loan') || k.includes('mortgage') ? 'loans' : k.includes('lease') ? 'leases' : k.includes('insurance') ? 'insurance' : 'other'; }
  function text(tag, value, cls) { const el = document.createElement(tag); el.textContent = value; if (cls) el.className = cls; return el; }
  function metric(parent, label, value, hint) { const item=document.createElement('div'); item.append(text('div',label,'muted'),text('div',value,'amount')); if(hint)item.append(text('div',hint,'muted')); parent.append(item); }
  const names = {leases:'lease', insurance:'insurance', loans:'loan', other:'record'};
  function insuranceCard(c) {
    const card=document.createElement('details');card.className='card insurance-card';
    const summary=document.createElement('summary'), title=document.createElement('span');
    title.className='insurance-title';title.append(text('span',c.name,'insurance-name'),text('span',c.kind+(c.end?' · renewal or end '+c.end:' · end date not recorded'),'muted'));
    summary.append(title,text('span','Details','insurance-prompt'));card.append(summary);
    const body=document.createElement('div');body.className='insurance-details';
    const grid=document.createElement('div');grid.className='grid';
    if(c.start)metric(grid,'Start date',c.start);
    if(c.end)metric(grid,'End or renewal date',c.end);
    if(c.monthly_payment!==undefined)metric(grid,'Monthly payment',money(c.monthly_payment));
    if(c.payment_day!==undefined)metric(grid,'Payment day',String(c.payment_day));
    if(c.provider)metric(grid,'Provider',String(c.provider));
    if(c.policy_number)metric(grid,'Policy number',String(c.policy_number));
    if(c.who?.length)metric(grid,'Reminder recipients',c.who.join(', '));
    if(c.remind_months?.length)metric(grid,'Reminder timing',c.remind_months.join(', ')+' months before');
    if(grid.childElementCount)body.append(grid);else body.append(text('p','No further details recorded.','muted'));
    if(c.notes)body.append(text('p',String(c.notes)));
    const edit=text('button','Edit insurance');edit.type='button';edit.onclick=()=>open(c);body.append(edit);card.append(body);return card;
  }
  function graph(points, contract) {
    if (!Array.isArray(points) || points.length < 2 || !(points[0].balance > 0)) return null;
    const loan=group(contract)==='loans';
    const note=loan?'Illustration: '+contract.interest_rate_percent+'% annual interest divided monthly and the same monthly payment from the month after the recorded balance. Assumes payments were made, the rate stays unchanged and there are no fees; lender calculations may differ.':'Based on scheduled payments, including past payments assumed to have been made.';
    const box=document.createElement('section');box.className='balance-graph';box.append(text('h3','Projected balance'),text('p',note,'muted'));
    if(loan && points[1].balance>=points[0].balance)box.append(text('p','The payment may not cover monthly interest; this illustration shows up to 12 months.','muted'));
    else if(loan && points[points.length-1].balance>0)box.append(text('p','The illustration stops at the known rate or contract end, or the model’s limit.','muted'));
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 600 190');svg.setAttribute('role','img');svg.setAttribute('aria-label','Recorded balance '+money(points[0].balance)+' on '+points[0].date+'; projected balance '+money(points[points.length-1].balance)+' after '+(points.length-1)+' scheduled payments.');
    function shape(tag,attrs,label){const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));if(label)node.textContent=label;svg.append(node);return node;}
    for(const y of [20,85,150])shape('line',{x1:65,y1:y,x2:575,y2:y,class:'graph-grid'});
    const maxBalance=Math.max(...points.map(p=>p.balance));shape('text',{x:5,y:25,class:'graph-label'},money(maxBalance));shape('text',{x:5,y:90,class:'graph-label'},money(maxBalance/2));shape('text',{x:5,y:155,class:'graph-label'},money(0));
    const coords=points.map((p,i)=>[65+510*i/(points.length-1),150-130*p.balance/maxBalance]);
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
    $('#add').textContent='Add '+names[tab];
    if (!filtered.length) { const box=text('div','No '+tab+' recorded.','card empty');list.append(box);return; }
    for(const c of filtered) {
      if(group(c)==='insurance'){list.append(insuranceCard(c));continue;}
      const p=projections.find(x=>x.id===c.id)||{}, card=document.createElement('article');card.className='card';
      const head=document.createElement('div');head.className='cardhead';const title=document.createElement('div');title.append(text('h2',c.name),text('p',c.kind+(c.end?' · '+(group(c)==='insurance'?'renewal or end ':'ends ')+c.end:' · end date not recorded')));
      const edit=text('button','Edit');edit.type='button';edit.setAttribute('aria-label','Edit '+c.name);edit.onclick=()=>open(c);head.append(title,edit);card.append(head);
      const grid=document.createElement('div');grid.className='grid';
      if(c.monthly_payment!==undefined)metric(grid,'Monthly payment',money(c.monthly_payment));
      if(group(c)==='loans' && c.interest_rate_percent!==undefined)metric(grid,'Annual interest rate',c.interest_rate_percent+'%',c.interest_rate_until?'Rate ends '+c.interest_rate_until:'');
      if(p.projected_balance!==undefined)metric(grid,'Projected outstanding',money(p.projected_balance),p.remaining_payments+' payments remaining');
      else if(p.estimated_scheduled_total!==undefined)metric(grid,'Estimated scheduled total',money(p.estimated_scheduled_total),p.estimated_payments+' estimated payments');
      else if(c.observed_balance)metric(grid,'Last recorded balance',money(c.observed_balance.amount),'Recorded '+c.observed_balance.as_of+' · not a current balance');
      else if(group(c)==='loans' && c.balance_snapshot)metric(grid,'Last recorded balance',money(c.balance_snapshot.amount),'Recorded '+c.balance_snapshot.as_of+' · not a current balance');
      if(c.settlement_quote)metric(grid,'Early settlement quote',money(c.settlement_quote.amount),'Quoted '+c.settlement_quote.as_of);
      card.append(grid);const chart=graph(series[c.id],c);if(chart)card.append(chart);else if(p.projected_balance!==undefined)card.append(text('p','The projected balance assumes scheduled payments have been made.','muted'));else if(group(c)==='loans')card.append(text('p','No illustration available. Check the recorded balance, monthly payment, interest rate and any rate or contract end dates.','muted'));list.append(card);
    }
  }
  function open(c) {
    current=c || null;form.reset();$('#form-status').textContent='';$('#edit-title').textContent=(c?'Edit ':'Add ')+names[c?group(c):tab];
    const v=c||{};for(const key of ['name','start','end','monthly_payment','payment_day','interest_rate_percent','interest_rate_until','provider','policy_number','notes'])field(key).value=v[key]??'';
    form.querySelector('[type="submit"]').textContent='Save '+names[c?group(c):tab];
    const known=Array.from(field('kind').options).some(option=>option.value===v.kind && option.value!=='__custom__');field('kind').value=known?v.kind:v.kind?'__custom__':tab==='loans'?'loan':tab==='other'?'other contract':'';field('kind_custom').value=known?'':v.kind||'';syncType();
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
  function syncType(){const custom=field('kind').value==='__custom__';$('#custom-type-label').hidden=!custom;field('kind_custom').required=custom;const k=(custom?field('kind_custom').value:field('kind').value).toLowerCase();const isLoan=k.includes('loan')||k.includes('mortgage');$('#rate-label').hidden=!isLoan;$('#rate-until-label').hidden=!isLoan;for(const id of ['provider-label','policy-label','notes-label'])$('#'+id).hidden=!k.includes('insurance');}
  field('kind').addEventListener('change',syncType);
  field('kind_custom').addEventListener('input',syncType);
  $('#add').onclick=()=>open(null);$('#close').onclick=$('#cancel').onclick=()=>{editor.hidden=true;};
  form.addEventListener('submit',async e=>{e.preventDefault();const v=current?JSON.parse(JSON.stringify(current)):{};
    for(const key of ['name','start','end'])v[key]=field(key).value.trim();v.kind=field('kind').value==='__custom__'?field('kind_custom').value.trim():field('kind').value;
    if(!v.kind){$('#form-status').textContent='Choose a contract type.';return;}
    if(group(v)==='insurance')for(const key of ['provider','policy_number','notes']){const value=field(key).value.trim();if(value)v[key]=value;else delete v[key];}
    for(const key of ['monthly_payment','payment_day','interest_rate_percent']){const value=field(key).value;if(value==='')delete v[key];else v[key]=Number(value);}
    if(field('interest_rate_until').value)v.interest_rate_until=field('interest_rate_until').value;else delete v.interest_rate_until;
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
