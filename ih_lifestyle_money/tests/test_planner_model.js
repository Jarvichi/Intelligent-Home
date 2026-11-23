'use strict';
const assert=require('node:assert/strict'), {model}=require('../app/ui/planner-model.js');
const start=2026*12, data={anchors:{START_MONTH:start,START_BALANCE:100000,CURRENT_PAYMENT:1000,FIXED_RATE:0,FIXED_ENDS:start+120,ERC_CAP:0,BIRTH_TEST:(1970*12),LISA_CURRENT:0,LISA_TARGET:0,VW_ENDS:start,KITCHEN_ENDS:start,BMW_ENDS:start,LEASE_VW:0,LEASE_KITCHEN:0,LEASE_BMW:0,LEASE_PHONES:0},scenario:{anchor:'test',moveMonth:start+12,growth:0,essentials:0,postMove:0,family:0,groceries:0,misc:0,pensionNetCost:0,plannedOverpayment:0,shareValue:0,useShares:false,salePrice:200000,buyPrice:150000,newRate:0,newTerm:20,targetAge:60,polestar:false,polestarCost:0,svrRate:0,sellCostPct:0,buyCosts:0,replaceBMW:false,lisaOn:false,porting:true,payHistory:[{month:start,income:3000}]}};
const result=model(data);
assert.equal(result.atMove,88000);assert.equal(result.equity,112000);assert.equal(result.tax,500);assert.equal(result.mortgage,38500);assert.ok(Math.abs(result.payment-38500/240)<1e-8);
data.scenario.plannedOverpayment=100;const faster=model(data);assert.ok(faster.atMove<result.atMove);assert.ok(faster.fastMonth<=faster.finalMonth);assert.ok(faster.monthlyRelief>=0);
data.scenario.moveMonth=start+1000;assert.throws(()=>model(data));
console.log('Planner model checks passed');
