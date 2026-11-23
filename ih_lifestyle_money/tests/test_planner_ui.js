'use strict';
const assert=require('node:assert/strict');
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.attributes={};this.className='';this.classList={add(){}};this.textContent='';this.value='';this.files=[];}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(key,value){this.attributes[key]=value;}
  querySelector(tag){return find(this,e=>e!==this && e.tag===tag);}
  click(){if(this.onclick)this.onclick();}
}
const selectors={};for(const id of ['#planner-content','#planner-status','#planner-export','#planner-import','#planner-file','.planner-data'])selectors[id]=new Element();
const all=[];function find(root,pred){if(pred(root))return root;for(const child of root.children||[]){const x=find(child,pred);if(x)return x;}return null;}
global.window=global;global.document={querySelector(s){if(s==='#planner-results')return find(selectors['#planner-content'],e=>e.id==='planner-results');return selectors[s];},createElement(tag){const e=new Element(tag);all.push(e);return e;},createElementNS(ns,tag){return this.createElement(tag);}};
const start=2026*12, fixture={format:'ih-money-planner-v25',anchors:{START_MONTH:start,START_BALANCE:100000,CURRENT_PAYMENT:1000,FIXED_RATE:0,FIXED_ENDS:start+120,ERC_CAP:0,BIRTH_TEST:1970*12,LISA_CURRENT:0,LISA_TARGET:0,VW_ENDS:start,KITCHEN_ENDS:start,BMW_ENDS:start,LEASE_VW:0,LEASE_KITCHEN:0,LEASE_BMW:0,LEASE_PHONES:0},scenario:{anchor:'test',moveMonth:start+12,growth:0,essentials:0,postMove:0,family:0,groceries:0,misc:0,pensionNetCost:0,plannedOverpayment:0,shareValue:0,useShares:false,salePrice:200000,buyPrice:150000,newRate:0,newTerm:20,targetAge:60,polestar:false,polestarCost:0,svrRate:0,sellCostPct:0,buyCosts:0,replaceBMW:false,lisaOn:false,porting:true,payHistory:[{month:start,income:3000}]}};
fixture.scenario.potEmergency=100;
global.fetch=async()=>({ok:true,json:async()=>({revision:1,data:fixture})});
require('../app/ui/planner-model.js');require('../app/ui/planner.js');
(async()=>{await global.IHMoneyPlanner.load();const root=selectors['#planner-content'];assert.ok(find(root,x=>x.textContent==='What’s still owed at your target age?'));assert.ok(find(root,x=>x.textContent==='The journey to mortgage-free'));const slider=find(root,x=>x.attributes['aria-label']==='Monthly overpayment slider');assert.ok(slider);slider.value='200';slider.oninput();assert.equal(fixture.scenario.plannedOverpayment,200);assert.ok(find(root,x=>x.textContent==='Mortgage at move'));assert.equal(find(root,x=>x.className==='planner-summary-total').textContent,'£100/mo');const pot=find(root,x=>x.tag==='label'&&find(x,y=>y.textContent==='Emergency'));const input=find(pot,x=>x.tag==='input');input.value='150';input.oninput();assert.equal(find(root,x=>x.className==='planner-summary-total').textContent,'£150/mo');const split=find(root,x=>x.textContent==='Break down this total');split.onclick();assert.equal(fixture.scenario.expense_breakdown.essentials[0].amount,fixture.scenario.essentials);})().catch(e=>{console.error(e);process.exitCode=1;});
