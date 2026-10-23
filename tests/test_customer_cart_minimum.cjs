'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8').split('\n').find(line=>line.includes('async function addProduct()'));
async function scenario(code,neighborhood='Test district'){
  const product={id:'p1'},fields={'product-qty':{value:'1'},'product-notes':{value:''},'customer-name':{value:'Test'},neighborhood:{value:neighborhood}};
  const calls=[];
  const ctx={state:{product,cart:[{productId:'p1',quantity:1,options:{},note:'',rowId:'old'}],branchId:'b1',service:'delivery',location:{lat:28,lng:45},session:{user:null}},
    eligibleForProduct:()=>({ok:true}),$:id=>fields[id]||(fields[id]={value:''}),groups:()=>[],key:()=>String(Math.random()),
    api:async path=>{calls.push(path);if(path.startsWith('/api/products/'))return {available:true};throw Object.assign(new Error(code),{code});},
    catalogReady:()=>true,orderBody:proposed=>({items:proposed}),showError:(_id,error)=>calls.push(['error',error]),
    cartChanged:()=>calls.push('changed'),closeDialog:()=>calls.push('closed'),toast:()=>{},sourceText:()=>'',t:x=>x,errorMessage:error=>error.message};
  vm.createContext(ctx);vm.runInContext(source,ctx);await ctx.addProduct();return {ctx,calls};
}
(async()=>{
  let result=await scenario('MINIMUM_ORDER_NOT_MET');assert.equal(result.ctx.state.cart[0].quantity,2);assert.ok(result.calls.includes('changed'));
  result=await scenario('OUTSIDE_COVERAGE');assert.equal(result.ctx.state.cart[0].quantity,1);assert.ok(!result.calls.includes('changed'));
  result=await scenario('PRODUCT_UNAVAILABLE');assert.equal(result.ctx.state.cart[0].quantity,1);
  result=await scenario('unused','');assert.equal(result.ctx.state.cart[0].quantity,2);assert.ok(!result.calls.includes('/api/quote'),'Missing checkout district must not block building a draft basket');
  console.log('PASS: adding items below the minimum stays possible; final quote is unchanged and other failures remain blocked.');
})().catch(error=>{console.error(error);process.exitCode=1;});
