'use strict';
(() => {
  const $ = s => document.querySelector(s), editor = $('#editor'), form = $('#form');
  let items = [], projections = [], series = {}, baselines = {}, mortgageTerms = {}, recipients = [], current = null, tab = 'leases';
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
    const term=mortgageTerms[contract.id];if(term?.agreed?.length>1)return mortgageGraph(term,contract);
    if (!Array.isArray(points) || points.length < 2 || !(points[0].balance > 0)) return null;
    const loan=group(contract)==='loans';
    const note=loan?(contract.loan_changes?.length?'Illustration: changes are applied in their recorded month, with annual interest divided monthly. A recorded balance replaces the calculation for its month. Payments are assumed, and fees and daily interest are not included; lender figures may differ.':'Illustration: '+contract.interest_rate_percent+'% annual interest divided monthly and the same monthly payment from the month after the recorded balance. Assumes payments were made, the rate stays unchanged and there are no fees; lender calculations may differ.'):'Based on scheduled payments, including past payments assumed to have been made.';
    const box=document.createElement('section');box.className='balance-graph';box.append(text('h3','Projected balance'),text('p',note,'muted'));
    if(loan && points[1].balance>=points[0].balance)box.append(text('p','The payment may not cover monthly interest; this illustration shows up to 12 months.','muted'));
    else if(loan && points[points.length-1].balance>0)box.append(text('p','The illustration stops at the known rate or contract end, or the model’s limit.','muted'));
    const baseline=baselines[contract.id]||[], ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 600 190');svg.setAttribute('role','img');svg.setAttribute('aria-label','Recorded balance '+money(points[0].balance)+' on '+points[0].date+'; projected balance '+money(points[points.length-1].balance)+' after '+(points.length-1)+' scheduled payments.');
    function shape(tag,attrs,label){const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));if(label)node.textContent=label;svg.append(node);return node;}
    for(const y of [20,85,150])shape('line',{x1:65,y1:y,x2:575,y2:y,class:'graph-grid'});
    const maxBalance=Math.max(...points.map(p=>p.balance),...baseline.map(p=>p.balance)), maxSteps=Math.max(points.length,baseline.length)-1;shape('text',{x:5,y:25,class:'graph-label'},money(maxBalance));shape('text',{x:5,y:90,class:'graph-label'},money(maxBalance/2));shape('text',{x:5,y:155,class:'graph-label'},money(0));
    const coords=points.map((p,i)=>[65+510*i/maxSteps,150-130*p.balance/maxBalance]);
    if(baseline.length>1)shape('polyline',{points:baseline.map((p,i)=>(65+510*i/maxSteps)+','+(150-130*p.balance/maxBalance)).join(' '),class:'graph-baseline'});
    shape('polyline',{points:coords.map(([x,y])=>x+','+y).join(' '),class:'graph-line'});
    shape('circle',{cx:coords[0][0],cy:coords[0][1],r:6,class:'graph-recorded'});
    shape('circle',{cx:coords[coords.length-1][0],cy:coords[coords.length-1][1],r:5,class:'graph-end'});
    shape('text',{x:65,y:178,class:'graph-label'},points[0].date);shape('text',{x:575,y:178,class:'graph-label','text-anchor':'end'},(baseline.length>points.length?baseline:points).at(-1).date);
    box.append(svg,text('p',baseline.length>1?'Green: with planned overpayments · Grey: without planned overpayments':'● Recorded balance · · · Scheduled estimate','muted'));
    const details=document.createElement('details'),summary=text('summary','See payment estimates');details.append(summary);
    const list=document.createElement('ol');for(const [i,p] of points.entries())list.append(text('li',(i===0||p.recorded?'Recorded: ':'Estimated: ')+p.date+' — '+money(p.balance)));details.append(list);box.append(details);return box;
  }
  function mortgageGraph(term, contract) {
    const agreed=term.agreed, plan=term.plan||[], expiry=(contract.interest_rate_until||contract.end).slice(0,7), ns='http://www.w3.org/2000/svg';
    const box=document.createElement('section');box.className='balance-graph';box.append(text('h3','Mortgage balance'),text('p','Green shows the agreed deal; grey continues the estimate using the last known rate unless a later rate is recorded. Monthly estimates assume payments are made and exclude fees and daily interest.','muted'));
    const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 600 190');svg.setAttribute('role','img');svg.setAttribute('aria-label','Mortgage balance: agreed deal in green, remaining term estimate in grey; any planned overpayments shown in blue.');
    function shape(tag,attrs,label){const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));if(label)node.textContent=label;svg.append(node);}
    const max=Math.max(...agreed.map(p=>p.balance),...plan.map(p=>p.balance)), steps=Math.max(agreed.length,plan.length)-1;
    if(!(max>0)||steps<1)return null;
    for(const y of [20,85,150])shape('line',{x1:65,y1:y,x2:575,y2:y,class:'graph-grid'});
    shape('text',{x:5,y:25,class:'graph-label'},money(max));shape('text',{x:5,y:155,class:'graph-label'},money(0));
    const coords=agreed.map((p,i)=>[65+510*i/steps,150-130*p.balance/max]), split=Math.max(0,agreed.findLastIndex(p=>p.date.slice(0,7)<=expiry));
    const xy=arr=>arr.map(p=>p.join(',')).join(' ');
    shape('polyline',{points:xy(coords.slice(0,split+1)),class:'graph-agreed'});
    shape('polyline',{points:xy(coords.slice(split)),class:'graph-baseline'});
    if(plan.length>1 && JSON.stringify(plan)!==JSON.stringify(agreed))shape('polyline',{points:xy(plan.map((p,i)=>[65+510*i/steps,150-130*p.balance/max])),class:'graph-plan'});
    shape('text',{x:65,y:178,class:'graph-label'},agreed[0].date);shape('text',{x:575,y:178,class:'graph-label','text-anchor':'end'},(plan.length>agreed.length?plan:agreed).at(-1).date);
    box.append(svg,text('p','Green: agreed deal · Grey: remaining term estimate'+(plan.length>1 && JSON.stringify(plan)!==JSON.stringify(agreed)?' · Blue: planned overpayments':''),'muted'));
    const details=document.createElement('details');details.append(text('summary','See balance estimates'));const list=document.createElement('ol');for(const p of agreed)list.append(text('li',p.date+' — '+money(p.balance)+(p.recorded?' (recorded)':'')));details.append(list);box.append(details);return box;
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
      if(group(c)==='loans' && c.provider)metric(grid,'Provider',String(c.provider));
      if(group(c)==='loans' && c.account_number)metric(grid,'Account number',String(c.account_number));
      card.append(grid);const chart=graph(series[c.id],c);if(chart)card.append(chart);else if(p.projected_balance!==undefined)card.append(text('p','The projected balance assumes scheduled payments have been made.','muted'));else if(group(c)==='loans')card.append(text('p','No illustration available. Check the recorded balance, monthly payment, interest rate and any rate or contract end dates.','muted'));list.append(card);
      if(group(c)==='loans' && c.notes)card.append(text('p',String(c.notes)));
      if(group(c)==='loans' && c.loan_changes?.length){const d=document.createElement('details');d.append(text('summary','Dated changes'));const ol=document.createElement('ol');for(const x of c.loan_changes)ol.append(text('li',x.date+' · '+changeNames[x.type]+' · '+(['provider','account'].includes(x.type)?x.value:x.type==='rate'?x.value+'%':money(x.value))));d.append(ol);card.append(d);}
    }
  }
  function open(c) {
    current=c || null;form.reset();$('#form-status').textContent='';$('#edit-title').textContent=(c?'Edit ':'Add ')+names[c?group(c):tab];
    const v=c||{};for(const key of ['name','start','end','monthly_payment','payment_day','interest_rate_percent','interest_rate_until','provider','policy_number','account_number','notes','mortgage_term_end'])field(key).value=v[key]??'';
    $('#change-list').replaceChildren();for(const change of v.loan_changes||[])addChange(change);
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
    $('#status').textContent='Loading contracts…';try {const r=await fetch('api/contracts',{cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();items=data.contracts;projections=data.projections;series=data.series||{};baselines=data.series_without_overpayments||{};mortgageTerms=data.mortgage_terms||{};recipients=data.recipients;$('#status').textContent='';render();}
    catch(e){$('#status').textContent='Could not load contracts. Please try again.';}
  }
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));editor.hidden=true;render();});
  const changeNames={balance:'Recorded balance',rate:'Interest rate (%)',payment:'Monthly payment (£)',overpayment:'Planned one-off overpayment (£)',recurring_overpayment:'Planned monthly overpayment (£)',provider:'Provider',account:'Account number'};
  function addChange(value={}){
    const row=document.createElement('div');row.className='change-row';
    const kind=document.createElement('select');kind.setAttribute('aria-label','Change type');
    for(const [id,label] of Object.entries(changeNames)){const option=text('option',label);option.value=id;kind.append(option);}kind.value=value.type||'balance';
    const date=document.createElement('input');date.type='date';date.required=true;date.value=value.date||'';date.setAttribute('aria-label','Change date');
    const amount=document.createElement('input');amount.type='text';amount.required=true;amount.value=value.value??'';amount.setAttribute('aria-label','Change value');
    const remove=text('button','Remove');remove.type='button';remove.onclick=()=>row.remove();row.append(kind,date,amount,remove);$('#change-list').append(row);
  }
  $('#add-change').onclick=()=>addChange();
  function syncType(){const custom=field('kind').value==='__custom__';$('#custom-type-label').hidden=!custom;field('kind_custom').required=custom;const k=(custom?field('kind_custom').value:field('kind').value).toLowerCase();const isLoan=k.includes('loan')||k.includes('mortgage'), isInsurance=k.includes('insurance');$('#rate-label').hidden=!isLoan;$('#rate-until-label').hidden=!isLoan;$('#loan-changes').hidden=!isLoan;$('#mortgage-term').hidden=!k.includes('mortgage');$('#account-label').hidden=!isLoan;$('#provider-label').hidden=!(isLoan||isInsurance);$('#notes-label').hidden=!(isLoan||isInsurance);$('#policy-label').hidden=!isInsurance;}
  field('kind').addEventListener('change',syncType);
  field('kind_custom').addEventListener('input',syncType);
  $('#add').onclick=()=>open(null);$('#close').onclick=$('#cancel').onclick=()=>{editor.hidden=true;};
  form.addEventListener('submit',async e=>{e.preventDefault();const v=current?JSON.parse(JSON.stringify(current)):{};
    for(const key of ['name','start','end'])v[key]=field(key).value.trim();v.kind=field('kind').value==='__custom__'?field('kind_custom').value.trim():field('kind').value;
    if(!v.kind){$('#form-status').textContent='Choose a contract type.';return;}
    if(['insurance','loans'].includes(group(v)))for(const key of ['provider','notes',...(group(v)==='loans'?['account_number']:['policy_number'])]){const value=field(key).value.trim();if(value)v[key]=value;else delete v[key];}
    if(group(v)==='loans'){
      const changes=[];for(const row of $('#change-list').children){const [kind,date,value]=row.querySelectorAll('select,input');const raw=value.value.trim(), isText=['provider','account'].includes(kind.value);if(!date.value||!raw||(!isText&&(!Number.isFinite(Number(raw))||Number(raw)<0))){$('#form-status').textContent='Enter a date and valid value for each loan change.';return;}const initial=field('balance_date').value;if(initial && date.value <= (initial.length===7?initial+'-01':initial)){$('#form-status').textContent='Each change must be after the starting balance date.';return;}changes.push({type:kind.value,date:date.value,value:isText?raw:Number(raw)});}
      v.loan_changes=changes;
    }
    for(const key of ['monthly_payment','payment_day','interest_rate_percent']){const value=field(key).value;if(value==='')delete v[key];else v[key]=Number(value);}
    if(field('interest_rate_until').value)v.interest_rate_until=field('interest_rate_until').value;else delete v.interest_rate_until;
    if(group(v)==='loans' && field('mortgage_term_end').value)v.mortgage_term_end=field('mortgage_term_end').value;else delete v.mortgage_term_end;
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
