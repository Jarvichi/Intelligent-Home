'use strict';
(() => {
  const $=s=>document.querySelector(s), content=$('#planner-content'), status=$('#planner-status');
  let data=null, revision=0, potTotal=null;
  const el=(tag,value,cls)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;if(cls)e.className=cls;return e;};
  const cash=n=>new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP',maximumFractionDigits:0}).format(n);
  const labels={moveMonth:'When will you move?',salePrice:'Expected sale price',buyPrice:'Expected purchase price',plannedOverpayment:'Monthly overpayment',targetAge:'Target age',anchor:'Whose age is the target?',porting:'Move the current mortgage to the new home',newRate:'New mortgage annual interest rate',newTerm:'New mortgage term (years)',svrRate:'Annual rate after the fixed deal ends',sellCostPct:'Selling costs',buyCosts:'Other buying costs',growth:'Annual income growth',essentials:'Essential household costs',postMove:'Monthly running costs saved after moving',family:'Family spending',groceries:'Groceries',misc:'Other monthly spending',pensionNetCost:'Additional pension cost',lisaOn:'Use the target Lifetime ISA contribution',useShares:'Use shares towards the move',shareValue:'Value of shares used',polestar:'Include replacement vehicle',polestarCost:'Replacement vehicle monthly cost',replaceBMW:'Continue second vehicle payment',START_MONTH:'Balance recorded in',START_BALANCE:'Mortgage balance at that date',CURRENT_PAYMENT:'Current mortgage monthly payment',FIXED_RATE:'Current fixed annual interest rate',FIXED_ENDS:'Current fixed deal ends',ERC_CAP:'Maximum early repayment charge',LEASE_VW:'First vehicle monthly payment',LEASE_BMW:'Second vehicle monthly payment',LEASE_KITCHEN:'Home finance monthly payment',LEASE_PHONES:'Phone payments per month',VW_ENDS:'First vehicle payments end',BMW_ENDS:'Second vehicle payments end',KITCHEN_ENDS:'Home finance payments end',LISA_CURRENT:'Current Lifetime ISA contribution per month',LISA_TARGET:'Target Lifetime ISA contribution per month'};
  const help={ERC_CAP:'The maximum fee estimated if you repay the mortgage before the fixed deal ends. Check your lender’s terms; the planner uses an approximation.',porting:'Porting means moving your existing mortgage deal to the new property. Check whether your lender permits it and whether charges apply.',svrRate:'The assumed annual interest rate after the current fixed deal ends, if the move has not happened yet.',lisaOn:'Choose whether to budget for the target rather than the current monthly Lifetime ISA contribution.',postMove:'The estimated reduction in monthly household running costs after the move.'};
  const label=k=>labels[k]||(k.startsWith('BIRTH_')?'Birth month: '+k.slice(6).toLowerCase():k.replace(/^pot/,'').replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2')).replace(/\b\w/g,c=>c.toUpperCase());
  const asMonth=n=>`${Math.floor(n/12)}-${String(n%12+1).padStart(2,'0')}`;
  const fromMonth=s=>{const [y,m]=s.split('-').map(Number);return y*12+m-1;};
  const displayMonth=n=>new Intl.DateTimeFormat('en-GB',{month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(Date.UTC(Math.floor(n/12),n%12,1)));
  async function request(method,body){const r=await fetch('api/planner',{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const v=await r.json();if(!r.ok)throw Error(v.error||'Planner request failed');return v;}
  async function load(){try{const r=await request('GET');data=r.data;revision=r.revision;$('#planner-export').disabled=!data;$('.planner-data').open=!data;status.textContent=data?'':'Import your planner data to get started.';render();}catch(e){status.textContent=e.message;}}
  async function save(next){window.IHMoneyPlannerModel.model(next);const r=await request('POST',{data:next,revision});revision=r.revision;data=next;$('#planner-export').disabled=false;$('.planner-data').open=false;status.textContent='Planner saved.';render();}
  $('#planner-import').onclick=async()=>{try{const f=$('#planner-file').files[0];if(!f)throw Error('Choose a planner JSON file first.');if(f.size>60000)throw Error('The file is too large.');await save(JSON.parse(await f.text()));}catch(e){status.textContent=e.message||'Could not import the planner.';}};
  $('#planner-export').onclick=()=>{if(!data)return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='money-planner-backup.json';a.click();URL.revokeObjectURL(url);};
  function row(table,name,value,strong=false){const tr=el('tr');if(strong)tr.className='planner-total';tr.append(el('td',name),el('td',value));table.append(tr);}
  function drawGraph(r){
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'), points=r.points, start=points[0].month,end=Math.max(start+1,points.at(-1).month),max=Math.max(...points.map(p=>p.balance),1);
    svg.setAttribute('viewBox','0 0 600 230');svg.setAttribute('role','img');svg.setAttribute('aria-label','Estimated mortgage balance from '+displayMonth(start)+' to '+displayMonth(end));
    function shape(type,attrs,text){const n=document.createElementNS(ns,type);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text)n.textContent=text;svg.append(n);}
    for(const y of [20,90,160])shape('line',{x1:62,y1:y,x2:584,y2:y,class:'planner-chart-grid'});
    for(const [y,v] of [[24,max],[94,max/2],[164,0]])shape('text',{x:2,y,class:'planner-chart-label'},cash(v));
    const xy=p=>`${62+522*(p.month-start)/(end-start)},${160-140*p.balance/max}`;
    const current=points.filter(p=>p.phase==='current'),after=points.filter(p=>p.phase!=='current');
    if(current.length>1)shape('polyline',{points:current.map(xy).join(' '),class:'planner-line-current'});
    if(after.length>1)shape('polyline',{points:after.map(xy).join(' '),class:'planner-line-after'});
    shape('text',{x:62,y:195,class:'planner-chart-label'},displayMonth(start));shape('text',{x:584,y:195,'text-anchor':'end',class:'planner-chart-label'},displayMonth(end));
    return svg;
  }
  function calculate(){const results=$('#planner-results');if(!results)return;try{
    if(potTotal)potTotal.textContent=cash(window.IHMoneyPlannerModel.savingsTotal(data.scenario))+'/mo';
    const r=window.IHMoneyPlannerModel.model(data), age=data.scenario.targetAge;results.replaceChildren();
    const strip=el('div',undefined,'planner-strip');for(const [key,value] of [['Move',displayMonth(data.scenario.moveMonth)],['Sell',cash(data.scenario.salePrice)],['Buy',cash(data.scenario.buyPrice)],['Overpayment',cash(data.scenario.plannedOverpayment)+'/mo']]){const cell=el('div',undefined,'planner-strip-item');cell.append(el('span',key,'planner-kicker'),el('strong',value));strip.append(cell);}results.append(strip);
    const hero=el('section',undefined,'planner-card planner-hero');hero.append(el('p','Balance at '+age+' · '+displayMonth(r.target),'planner-kicker'),el('p',r.targetBalance<=0?'Clear':cash(r.targetBalance),'planner-hero-value'),el('p',r.targetBalance<=0?(r.cleared?'Estimated to clear in '+displayMonth(r.cleared):'No new mortgage needed'):'Estimated balance on a '+cash(r.mortgage)+' mortgage','planner-sub'));
    if(r.targetBalance>0){const compare=el('div',undefined,'planner-compare');for(const [name,amount,date] of [['Ease off',r.payment,r.finalMonth],['Keep overpaying',r.pushPayment,r.fastMonth]]){const item=el('div');item.append(el('strong',name),el('span',cash(amount)+'/mo','planner-sub'),el('b',date?'Clear '+displayMonth(date):'Not cleared in estimate'));compare.append(item);}hero.append(compare);if(r.monthlyRelief>0)hero.append(el('p','Easing off frees '+cash(r.monthlyRelief)+' per month.','planner-callout'));}
    if(r.insufficientBudget)hero.append(el('p','The planned overpayment is included in the projection, but the income and spending entered do not cover it for the whole plan. Review the budget before relying on this estimate.','planner-warning'));results.append(hero);
    const chart=el('section',undefined,'planner-card');chart.append(el('p','Mortgage balance','planner-kicker'),el('h3','The journey to mortgage-free'),drawGraph(r),el('p','Green: current mortgage · Teal: after the move','planner-sub'));results.append(chart);
    const move=el('section',undefined,'planner-card');move.append(el('p','The move','planner-kicker'),el('h3','From sale to new mortgage'));const table=el('table',undefined,'planner-table');for(const [name,value] of [['Mortgage at move',r.atMove],['Selling costs',r.selling],['Early repayment estimate',r.erc],['Equity available',r.equity],['Stamp duty estimate',r.tax],['New mortgage',r.mortgage]])row(table,name,cash(value),name==='Equity available'||name==='New mortgage');move.append(table,el('p','Illustrative figures. Check the lender’s charges and current stamp duty rules.','planner-footnote'));results.append(move);
    }catch(e){results.replaceChildren(el('p',e.message||'Check the planner inputs.','planner-warning'));}}
  function inputFor(holder,key,value){
    const wrap=el('label',undefined,'planner-field'), top=el('span',undefined,'planner-field-top');top.append(el('span',label(key)));wrap.append(top);
    const rate=['FIXED_RATE','growth','newRate','svrRate'].includes(key),month=key==='moveMonth'||key.endsWith('_MONTH')||key.endsWith('_ENDS')||key.startsWith('BIRTH_');let input;
    if(typeof value==='boolean'){wrap.classList.add('planner-toggle');input=document.createElement('input');input.type='checkbox';input.checked=value;input.onchange=()=>{holder[key]=input.checked;calculate();};}
    else if(key==='anchor'){input=document.createElement('select');for(const k of Object.keys(data.anchors).filter(x=>x.startsWith('BIRTH_'))){const v=k.slice(6).toLowerCase(),o=el('option',label(v));o.value=v;input.append(o);}input.value=value;input.onchange=()=>{holder[key]=input.value;calculate();};}
    else if(typeof value==='number'){
      input=document.createElement('input');input.type=month?'month':'number';if(!month)input.step='any';input.value=month?asMonth(value):rate?+(value*100).toFixed(4):value;
      if(rate||key==='sellCostPct')top.append(el('span','%','planner-unit'));else if(!month&&key!=='targetAge'&&key!=='newTerm')top.append(el('span','£','planner-unit'));
      input.oninput=()=>{if(!input.value)return;const n=month?fromMonth(input.value):Number(input.value)/(rate?100:1);if(Number.isFinite(n)){holder[key]=n;if(slider)slider.value=Number(input.value);calculate();}};
      const sliderKeys={plannedOverpayment:[0,Math.max(5000,value*2),25],targetAge:[Math.min(45,value),Math.max(80,value),1],salePrice:[0,Math.max(1000000,value*2),5000],buyPrice:[0,Math.max(1000000,value*2),5000]};
      let slider=null;if(sliderKeys[key]){slider=document.createElement('input');slider.type='range';[slider.min,slider.max,slider.step]=sliderKeys[key];slider.value=value;slider.setAttribute('aria-label',label(key)+' slider');slider.oninput=()=>{input.value=slider.value;holder[key]=Number(slider.value);calculate();};wrap.append(input,slider);if(help[key])wrap.append(el('span',help[key],'planner-field-help'));return wrap;}
    } else return null;
    wrap.append(input);if(help[key])wrap.append(el('span',help[key],'planner-field-help'));return wrap;
  }
  function group(title,keys,holder,open=true){const details=el('details',undefined,'planner-card planner-controls');details.open=open;details.append(el('summary',title));const grid=el('div',undefined,'planner-fields');for(const key of keys){if(!(key in holder))continue;const input=inputFor(holder,key,holder[key]);if(input)grid.append(input);}details.append(grid);return details;}
  function savingsGroup(scenario){
    const details=group('Savings pots',Object.keys(scenario).filter(k=>k.startsWith('pot')),scenario,false);potTotal=el('span','','planner-summary-total');details.querySelector('summary').append(potTotal);
    details.append(el('p','Edit the monthly amount for each pot, or add another. These contributions are included in the household budget.','planner-footnote'));
    const rows=el('div');details.append(rows);
    function draw(){rows.replaceChildren();for(const [index,pot] of (scenario.extra_pots||[]).entries()){
      const line=el('div',undefined,'planner-saving-row'),name=document.createElement('input'),amount=document.createElement('input'),remove=el('button','Remove');
      name.value=pot.name;name.placeholder='Pot name';name.setAttribute('aria-label','Savings pot name');name.oninput=()=>{pot.name=name.value;calculate();};
      amount.type='number';amount.min='0';amount.step='0.01';amount.placeholder='£ / month';amount.value=pot.amount;amount.setAttribute('aria-label','Savings pot monthly amount');amount.oninput=()=>{pot.amount=amount.value===''?NaN:Number(amount.value);calculate();};
      remove.type='button';remove.onclick=()=>{scenario.extra_pots.splice(index,1);draw();calculate();};line.append(name,amount,remove);rows.append(line);
    }}
    const add=el('button','Add savings pot');add.type='button';add.onclick=()=>{scenario.extra_pots??=[];scenario.extra_pots.push({name:'New savings pot',amount:0});draw();calculate();};draw();details.append(add);return details;
  }
  function expenditureGroup(scenario, anchors){
    const details=el('details',undefined,'planner-card planner-controls');details.append(el('summary','Bills and other expenditure'));
    details.append(el('p','Split the existing totals into individual items. Amounts replace the corresponding total, so reduce the unallocated amount as you add items.','planner-sub'));
    const fixed=el('p','The current mortgage and existing vehicle, finance and phone payments are already included separately in this plan. Do not add them again below. Edit them in Current mortgage and Vehicle and finance payments.','planner-footnote');details.append(fixed);
    const table=el('table',undefined,'planner-table');
    for(const [name,key] of [['Mortgage payment','CURRENT_PAYMENT'],['Vehicle lease','LEASE_VW'],['Vehicle payment','LEASE_BMW'],['Finance payment','LEASE_KITCHEN'],['Phone payments','LEASE_PHONES']])if(Number.isFinite(anchors[key]))row(table,name,cash(anchors[key])+'/mo');
    details.append(table);
    for(const [bucket,title,field] of [['essentials','Essential bills','essentials'],['other','Other expenditure','misc']]){
      const area=el('section',undefined,'planner-expenses');details.append(area);
      function draw(){
        area.replaceChildren();const breakdown=scenario.expense_breakdown||{}, entries=breakdown[bucket];
        const heading=el('div',undefined,'planner-expense-heading');heading.append(el('h3',title));const total=el('strong');heading.append(total);area.append(heading);
        function totalNow(){try{total.textContent=cash(window.IHMoneyPlannerModel.expensesAt(scenario,bucket,anchors.START_MONTH))+'/mo at plan start';}catch{total.textContent='Check entries';}calculate();}
        if(!entries){
          const fallback=inputFor(scenario,field,scenario[field]);if(fallback){const input=fallback.querySelector('input'), update=input.oninput;input.oninput=()=>{update();totalNow();};area.append(fallback);}
          const split=el('button','Break down this total');split.type='button';split.onclick=()=>{scenario.expense_breakdown??={};scenario.expense_breakdown[bucket]=[{name:'Unallocated',category:'other',amount:scenario[field]}];draw();};area.append(split);
        }else{
          area.append(el('p','Set optional start and end months for payments that change over time.','planner-sub'));
          for(const [index,item] of entries.entries()){
            const line=el('div',undefined,'planner-expense-row'),name=document.createElement('input'),kind=document.createElement('select'),amount=document.createElement('input'),start=document.createElement('input'),end=document.createElement('input'),remove=el('button','Remove');
            name.value=item.name;name.placeholder='Name';name.setAttribute('aria-label',title+' item name');name.oninput=()=>{item.name=name.value;totalNow();};
            for(const [value,caption] of [['bill','Bill'],['loan','Loan'],['car','Car'],['other','Other']]){const option=el('option',caption);option.value=value;kind.append(option);}kind.value=item.category;kind.setAttribute('aria-label',title+' category');kind.onchange=()=>{item.category=kind.value;totalNow();};
            amount.type='number';amount.min='0';amount.step='0.01';amount.placeholder='£ / month';amount.value=item.amount;amount.setAttribute('aria-label',title+' monthly amount');amount.oninput=()=>{item.amount=amount.value===''?NaN:Number(amount.value);totalNow();};
            start.type='month';start.value=item.start==null?'':asMonth(item.start);start.setAttribute('aria-label',title+' start month');start.onchange=()=>{item.start=start.value?fromMonth(start.value):null;totalNow();};
            end.type='month';end.value=item.end==null?'':asMonth(item.end);end.setAttribute('aria-label',title+' end month');end.onchange=()=>{item.end=end.value?fromMonth(end.value):null;totalNow();};
            const startLabel=el('label','From (optional)'),endLabel=el('label','Until (optional)');startLabel.append(start);endLabel.append(end);
            remove.type='button';remove.onclick=()=>{entries.splice(index,1);draw();};line.append(name,kind,amount,startLabel,endLabel,remove);area.append(line);
          }
          const add=el('button','Add expenditure');add.type='button';add.onclick=()=>{entries.push({name:'New expense',category:'other',amount:0});draw();};area.append(add);
        }
        totalNow();
      }
      draw();
    }
    return details;
  }
  function render(){content.replaceChildren();potTotal=null;if(!data){content.append(el('p','Import a planner to explore your move.','planner-empty'));return;}
    const intro=el('header',undefined,'planner-intro');intro.append(el('p','Your move · Your plan','planner-kicker'),el('h2','What’s still owed at your target age?'),el('p','See how the move and your choices affect the mortgage, then adjust the assumptions below.','planner-sub'));content.append(intro);
    const results=el('div');results.id='planner-results';content.append(results);calculate();
    const scenario=data.scenario,mainKeys=['moveMonth','salePrice','buyPrice','sellCostPct','buyCosts','porting'],goalKeys=['plannedOverpayment','targetAge','anchor'],mortgageKeys=['START_MONTH','START_BALANCE','CURRENT_PAYMENT','FIXED_RATE','FIXED_ENDS','ERC_CAP'],afterMoveKeys=['newRate','newTerm','svrRate'],incomeKeys=['growth'],budgetKeys=['postMove','family','groceries','pensionNetCost'],carKeys=['LEASE_VW','VW_ENDS','LEASE_BMW','BMW_ENDS','LEASE_KITCHEN','KITCHEN_ENDS','LEASE_PHONES'],carPlanKeys=['polestar','polestarCost','replaceBMW'],savingKeys=['lisaOn','useShares','shareValue'],savingAnchorKeys=['LISA_CURRENT','LISA_TARGET'];
    const pots=savingsGroup(scenario);
    content.append(group('Your move',mainKeys,scenario),group('Mortgage goal',goalKeys,scenario),group('Current mortgage',mortgageKeys,data.anchors,false),group('Mortgage after the move',afterMoveKeys,scenario,false),group('Income and growth',incomeKeys,scenario,false));
    const afterIncome=[group('Household spending',budgetKeys,scenario,false),expenditureGroup(scenario,data.anchors),group('Vehicle and finance payments',carKeys,data.anchors,false),group('Vehicle plans',carPlanKeys,scenario,false),pots,group('Other savings and shares',savingKeys,scenario,false),group('Lifetime ISA contributions',savingAnchorKeys,data.anchors,false),group('Birth months for target age',Object.keys(data.anchors).filter(k=>k.startsWith('BIRTH_')),data.anchors,false)];calculate();
    const history=el('details',undefined,'planner-card planner-controls');history.append(el('summary','Income history'));const historyList=el('div');function drawHistory(){historyList.replaceChildren();for(const [idx,p] of scenario.payHistory.entries()){const row=el('div',undefined,'change-row'), date=document.createElement('input'),income=document.createElement('input'),remove=el('button','Remove');date.type='month';date.value=asMonth(p.month);date.setAttribute('aria-label','Income from month');income.type='number';income.step='0.01';income.value=p.income;income.setAttribute('aria-label','Monthly income');date.onchange=()=>{p.month=fromMonth(date.value);scenario.payHistory.sort((a,b)=>a.month-b.month);drawHistory();calculate();};income.oninput=()=>{if(income.value){p.income=Number(income.value);calculate();}};remove.type='button';remove.disabled=scenario.payHistory.length===1;remove.onclick=()=>{scenario.payHistory.splice(idx,1);drawHistory();calculate();};row.append(date,income,remove);historyList.append(row);}}
    const add=el('button','Add income change');add.type='button';add.onclick=()=>{const last=scenario.payHistory.at(-1);scenario.payHistory.push({month:last.month+1,income:last.income});drawHistory();};drawHistory();history.append(historyList,add);content.append(history,...afterIncome);
    const saveButton=el('button','Save this plan','primary planner-save');saveButton.type='button';saveButton.onclick=async()=>{try{await save(data);}catch(e){status.textContent=e.message;}};content.append(saveButton,el('p','This is an illustration, not a lender quote or tax advice. The original planner’s repayment-charge estimate and stamp duty bands may not apply; check your lender’s terms and current rules. Changes here do not update your loan records.','planner-footnote'));
  }
  window.IHMoneyPlanner={load};
})();
