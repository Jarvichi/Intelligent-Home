'use strict';
(function(root) {
  function expensesAt(scenario, bucket, month) {
    const breakdown=scenario.expense_breakdown;
    if(!breakdown || !(bucket in breakdown)) return bucket==='essentials'?scenario.essentials:scenario.misc;
    const entries=breakdown[bucket];
    if(!Array.isArray(entries) || entries.some(e=>!e || typeof e.name!=='string' || !e.name.trim() || !['bill','loan','car','other'].includes(e.category) || !Number.isFinite(e.amount) || e.amount<0 || (e.start!=null && !Number.isInteger(e.start)) || (e.end!=null && !Number.isInteger(e.end)) || (e.start!=null && e.end!=null && e.end<e.start))) throw Error('Check the expenditure amounts and dates.');
    return entries.reduce((sum,e)=>sum+((e.start==null||month>=e.start)&&(e.end==null||month<=e.end)?e.amount:0),0);
  }
  function model(data) {
    const a=data.anchors, i=data.scenario, required=['START_MONTH','START_BALANCE','CURRENT_PAYMENT','FIXED_RATE','FIXED_ENDS','ERC_CAP','LISA_CURRENT','LISA_TARGET','VW_ENDS','KITCHEN_ENDS','BMW_ENDS','LEASE_VW','LEASE_KITCHEN','LEASE_BMW','LEASE_PHONES'];
    if(!required.every(k=>Number.isFinite(a[k])) || !Array.isArray(i.payHistory) || !i.payHistory.length) throw Error('The imported planner data is incomplete.');
    const numeric=['moveMonth','growth','essentials','postMove','family','groceries','misc','pensionNetCost','plannedOverpayment','shareValue','salePrice','buyPrice','newRate','newTerm','targetAge','polestarCost','svrRate','sellCostPct','buyCosts'];
    if(!numeric.every(k=>Number.isFinite(i[k])) || i.plannedOverpayment<0 || i.newTerm<=0 || i.newTerm>50 || i.growth<0 || i.growth>1 || i.newRate<0 || i.newRate>1 || i.svrRate<0 || i.svrRate>1 || a.FIXED_RATE<0 || a.FIXED_RATE>1 || i.moveMonth<a.START_MONTH || i.moveMonth>a.START_MONTH+600 || !Number.isFinite(a['BIRTH_'+String(i.anchor).toUpperCase()])) throw Error('Check the planner dates and values.');
    if(i.payHistory.some(p=>!Number.isFinite(p.month)||!Number.isFinite(p.income)))throw Error('Check the income history.');
    const birth=a['BIRTH_'+String(i.anchor).toUpperCase()], target=birth+i.targetAge*12;
    if(target<a.START_MONTH || target>a.START_MONTH+1200)throw Error('Check the target age.');
    const income=m=>{let p=i.payHistory[0];for(const x of i.payHistory)if(x.month<=m)p=x;const start=Math.floor((p.month-1)/12)+1;return p.income*Math.pow(1+i.growth,Math.max(0,Math.floor(m/12)-start+1));};
    const pots=Object.entries(i).filter(([k])=>k.startsWith('pot')).reduce((s,[,v])=>s+(Number(v)||0),0);
    expensesAt(i,'essentials',a.START_MONTH);expensesAt(i,'other',a.START_MONTH);
    const headroom=m=>income(m)-(a.CURRENT_PAYMENT+a.LEASE_PHONES+(m<=a.VW_ENDS?a.LEASE_VW:0)+(m<=a.KITCHEN_ENDS?a.LEASE_KITCHEN:(i.polestar?i.polestarCost:0))+((i.replaceBMW||m<=a.BMW_ENDS)?a.LEASE_BMW:0)+expensesAt(i,'essentials',m)-(m>i.moveMonth?i.postMove:0)+i.family+i.groceries+expensesAt(i,'other',m)+i.pensionNetCost+(i.lisaOn?a.LISA_TARGET:a.LISA_CURRENT)+pots);
    const extra=i.plannedOverpayment, points=[{month:a.START_MONTH,balance:a.START_BALANCE,phase:'current'}];
    let balance=a.START_BALANCE;
    for(let m=a.START_MONTH+1;m<=i.moveMonth;m++){balance=Math.max(0,balance*(1+(m<=a.FIXED_ENDS?a.FIXED_RATE:i.svrRate)/12)-a.CURRENT_PAYMENT-extra);points.push({month:m,balance,phase:'current'});}
    const atMove=balance, erc=!i.porting&&i.moveMonth<=a.FIXED_ENDS?Math.min(Math.max(balance*.9,0)*.01*((a.FIXED_ENDS+1-i.moveMonth)/12),a.ERC_CAP):0;
    const selling=i.salePrice*i.sellCostPct/100, equity=i.salePrice-balance-selling-erc+(i.useShares?i.shareValue:0);
    const bands=[[0,125000,0],[125000,250000,.02],[250000,925000,.05],[925000,1500000,.1],[1500000,Infinity,.12]];
    const tax=bands.reduce((sum,[lo,hi,rate])=>sum+(i.buyPrice>lo?(Math.min(i.buyPrice,hi)-lo)*rate:0),0), mortgage=Math.max(i.buyPrice+tax+i.buyCosts-equity,0);
    const rate=i.newRate/12, months=i.newTerm*12, payment=mortgage===0?0:rate===0?mortgage/months:mortgage*rate/(1-Math.pow(1+rate,-months));
    balance=mortgage;let m=i.moveMonth, cleared=null;points.push({month:m,balance,phase:'move'});
    while(balance>0 && m<target){m++;balance=Math.max(0,balance*(1+rate)-(Math.max(a.CURRENT_PAYMENT,payment)+extra));points.push({month:m,balance,phase:'target'});if(!balance)cleared=m;}
    const targetBalance=balance, stalled=balance>0 && payment<=balance*rate;
    const pushPayment=Math.max(a.CURRENT_PAYMENT,payment)+extra;
    let fastBalance=targetBalance, fastMonth=cleared || target;
    if(fastBalance>0)for(let n=0;fastBalance>0 && n<600;n++){fastMonth++;fastBalance=Math.max(0,fastBalance*(1+rate)-pushPayment);}
    if(!stalled)for(let n=0;balance>0 && n<600;n++){m++;balance=Math.max(0,balance*(1+rate)-payment);points.push({month:m,balance,phase:'tail'});}
    let insufficientBudget=false;
    for(const point of points){
      if(!['current','target'].includes(point.phase) || point.balance<=0)continue;
      const available=headroom(point.month)-(point.phase==='target'?Math.max(0,payment-a.CURRENT_PAYMENT):0);
      if(available<extra){insufficientBudget=true;break;}
    }
    return {points, atMove, erc, selling, equity, tax, mortgage, payment, targetBalance, target, cleared, stalled, finalMonth:balance===0?m:null, fastMonth:fastBalance===0?fastMonth:null, pushPayment, monthlyRelief:Math.max(0,pushPayment-payment), headroom:headroom(a.START_MONTH), insufficientBudget};
  }
  root.IHMoneyPlannerModel={model,expensesAt};
  if(typeof module!=='undefined')module.exports={model,expensesAt};
})(typeof window==='undefined'?globalThis:window);
