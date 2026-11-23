'use strict';
(() => {
  const $=s=>document.querySelector(s), content=$('#planner-content'), status=$('#planner-status');
  let data=null, revision=0;
  const el=(tag,value,cls)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;if(cls)e.className=cls;return e;};
  const cash=n=>new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP',maximumFractionDigits:0}).format(n);
  const labels={moveMonth:'When will you move?',salePrice:'Sale price',buyPrice:'Purchase price',plannedOverpayment:'Monthly overpayment',targetAge:'Mortgage-free target age',anchor:'Whose age is the target?',porting:'Port the current mortgage',newRate:'New mortgage interest rate',newTerm:'New mortgage term (years)',svrRate:'Rate after current deal',sellCostPct:'Selling costs (%)',buyCosts:'Other buying costs',growth:'Annual income growth',essentials:'Essential household costs',postMove:'Monthly running costs saved after the move',family:'Family spending',groceries:'Groceries',misc:'Other monthly spending',pensionNetCost:'Additional pension cost',lisaOn:'Use target savings contribution',useShares:'Use shares towards the move',shareValue:'Value of shares used',polestar:'Include replacement vehicle',polestarCost:'Replacement vehicle monthly cost',replaceBMW:'Continue vehicle payment'};
  const label=k=>labels[k]||k.replace(/^pot/,'').replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/\b\w/g,c=>c.toUpperCase());
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
    const r=window.IHMoneyPlannerModel.model(data), age=data.scenario.targetAge;results.replaceChildren();
    const strip=el('div',undefined,'planner-strip');for(const [key,value] of [['Move',displayMonth(data.scenario.moveMonth)],['Sell',cash(data.scenario.salePrice)],['Buy',cash(data.scenario.buyPrice)],['Overpayment',cash(data.scenario.plannedOverpayment)+'/mo']]){const cell=el('div',undefined,'planner-strip-item');cell.append(el('span',key,'planner-kicker'),el('strong',value));strip.append(cell);}results.append(strip);
    const hero=el('section',undefined,'planner-card planner-hero');hero.append(el('p','Balance at '+age+' · '+displayMonth(r.target),'planner-kicker'),el('p',r.targetBalance<=0?'Clear':cash(r.targetBalance),'planner-hero-value'),el('p',r.targetBalance<=0?(r.cleared?'Estimated to clear in '+displayMonth(r.cleared):'No new mortgage needed'):'Estimated balance on a '+cash(r.mortgage)+' mortgage','planner-sub'));
    if(r.targetBalance>0){const compare=el('div',undefined,'planner-compare');for(const [name,amount,date] of [['Ease off',r.payment,r.finalMonth],['Keep overpaying',r.pushPayment,r.fastMonth]]){const item=el('div');item.append(el('strong',name),el('span',cash(amount)+'/mo','planner-sub'),el('b',date?'Clear '+displayMonth(date):'Not cleared in estimate'));compare.append(item);}hero.append(compare);if(r.monthlyRelief>0)hero.append(el('p','Easing off frees '+cash(r.monthlyRelief)+' per month.','planner-callout'));}
    if(r.headroom<0)hero.append(el('p','Your starting commitments exceed the income entered. Review the budget before relying on this estimate.','planner-warning'));results.append(hero);
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
      if(rate)top.append(el('span','%','planner-unit'));else if(!month&&key!=='targetAge'&&key!=='newTerm')top.append(el('span','£','planner-unit'));
      input.oninput=()=>{if(!input.value)return;const n=month?fromMonth(input.value):Number(input.value)/(rate?100:1);if(Number.isFinite(n)){holder[key]=n;if(slider)slider.value=Number(input.value);calculate();}};
      const sliderKeys={plannedOverpayment:[0,Math.max(5000,value*2),25],targetAge:[Math.min(45,value),Math.max(80,value),1],salePrice:[0,Math.max(1000000,value*2),5000],buyPrice:[0,Math.max(1000000,value*2),5000]};
      let slider=null;if(sliderKeys[key]){slider=document.createElement('input');slider.type='range';[slider.min,slider.max,slider.step]=sliderKeys[key];slider.value=value;slider.setAttribute('aria-label',label(key)+' slider');slider.oninput=()=>{input.value=slider.value;holder[key]=Number(slider.value);calculate();};wrap.append(input,slider);return wrap;}
    } else return null;
    wrap.append(input);return wrap;
  }
  function group(title,keys,holder,open=true){const details=el('details',undefined,'planner-card planner-controls');details.open=open;details.append(el('summary',title));const grid=el('div',undefined,'planner-fields');for(const key of keys){if(!(key in holder))continue;const input=inputFor(holder,key,holder[key]);if(input)grid.append(input);}details.append(grid);return details;}
  function render(){content.replaceChildren();if(!data){content.append(el('p','Import a planner to explore your move.','planner-empty'));return;}
    const intro=el('header',undefined,'planner-intro');intro.append(el('p','Your move · Your plan','planner-kicker'),el('h2','What’s still owed at your target age?'),el('p','See how the move and your choices affect the mortgage, then adjust the assumptions below.','planner-sub'));content.append(intro);
    const results=el('div');results.id='planner-results';content.append(results);calculate();
    const scenario=data.scenario,mainKeys=['moveMonth','salePrice','buyPrice','plannedOverpayment','targetAge','anchor','porting'],mortgageKeys=['newRate','newTerm','svrRate','sellCostPct','buyCosts'],budgetKeys=['growth','essentials','postMove','family','groceries','misc','pensionNetCost','lisaOn','useShares','shareValue','polestar','polestarCost','replaceBMW'];
    content.append(group('Your plan',mainKeys,scenario),group('Mortgage and moving costs',mortgageKeys,scenario,false),group('Household budget',budgetKeys,scenario,false),group('Savings pots',Object.keys(scenario).filter(k=>k.startsWith('pot')),scenario,false),group('Starting figures',Object.keys(data.anchors),data.anchors,false));
    const history=el('details',undefined,'planner-card planner-controls');history.append(el('summary','Income history'));const historyList=el('div');function drawHistory(){historyList.replaceChildren();for(const [idx,p] of scenario.payHistory.entries()){const row=el('div',undefined,'change-row'), date=document.createElement('input'),income=document.createElement('input'),remove=el('button','Remove');date.type='month';date.value=asMonth(p.month);date.setAttribute('aria-label','Income from month');income.type='number';income.step='0.01';income.value=p.income;income.setAttribute('aria-label','Monthly income');date.onchange=()=>{p.month=fromMonth(date.value);scenario.payHistory.sort((a,b)=>a.month-b.month);drawHistory();calculate();};income.oninput=()=>{if(income.value){p.income=Number(income.value);calculate();}};remove.type='button';remove.disabled=scenario.payHistory.length===1;remove.onclick=()=>{scenario.payHistory.splice(idx,1);drawHistory();calculate();};row.append(date,income,remove);historyList.append(row);}}
    const add=el('button','Add income change');add.type='button';add.onclick=()=>{const last=scenario.payHistory.at(-1);scenario.payHistory.push({month:last.month+1,income:last.income});drawHistory();};drawHistory();history.append(historyList,add);content.append(history);
    const saveButton=el('button','Save this plan','primary planner-save');saveButton.type='button';saveButton.onclick=async()=>{try{await save(data);}catch(e){status.textContent=e.message;}};content.append(saveButton,el('p','This is an illustration, not a lender quote or tax advice. The original planner’s repayment-charge estimate and stamp duty bands may not apply; check your lender’s terms and current rules. Changes here do not update your loan records.','planner-footnote'));
  }
  window.IHMoneyPlanner={load};
})();
