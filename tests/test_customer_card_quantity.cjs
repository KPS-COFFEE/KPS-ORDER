'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
const section=source.slice(source.indexOf('  function cardQuantity('),source.indexOf('  function renderCart()'));
function fixture(){
  const plain={id:'p1'},flavour={id:'p2',option_groups:[{required:true}]};
  const calls=[];let sequence=0;
  const ctx={state:{cart:[],products:[plain,flavour]},number:{format:String},groups:p=>p.option_groups||[],
    eligibleForProduct:p=>({ok:p.enabled!==false,reason:'unavailable'}),toast:x=>calls.push(['toast',x]),
    openProduct:p=>calls.push(['options',p.id]),cartChanged:()=>calls.push(['changed']),key:()=>String(++sequence),t:x=>x,
    prefersLessMotion:()=>true,$:id=>({scrollIntoView:()=>calls.push(['scroll',id])}),
    updateQuantity:(id,delta)=>{const row=ctx.state.cart.find(line=>line.rowId===id);row.quantity+=delta;if(!row.quantity)ctx.state.cart=ctx.state.cart.filter(line=>line!==row);}};
  vm.createContext(ctx);vm.runInContext(section,ctx);return {ctx,plain,flavour,calls};
}
{
  const {ctx,plain}=fixture();
  for(let i=0;i<12;i++)ctx.addFromCard(plain);
  assert.equal(ctx.cardQuantity('p1'),12,'Every rapid tap contributes one unit');
  assert.equal(ctx.state.cart.length,1);assert.equal(ctx.state.cart[0].note,'');
  ctx.removeFromCard(plain);assert.equal(ctx.cardQuantity('p1'),11);
}
{
  const {ctx,flavour,calls}=fixture();ctx.addFromCard(flavour);ctx.addFromCard(flavour);
  assert.equal(ctx.state.cart.length,0,'Required choices can never become a plain draft unit');
  assert.equal(calls.filter(row=>row[0]==='options').length,2);
  ctx.state.cart=[{productId:'p2',rowId:'a',quantity:1,options:{FLAVOUR:'red'}},{productId:'p2',rowId:'b',quantity:1,options:{FLAVOUR:'blue'}}];
  ctx.removeFromCard(flavour);assert.equal(ctx.cardQuantity('p2'),2,'Minus cannot silently choose between variants');
  assert.deepEqual(calls.at(-1),['scroll','basket']);
}
{
  const {ctx,plain,calls}=fixture();plain.enabled=false;ctx.addFromCard(plain);assert.equal(ctx.state.cart.length,0);
  assert.deepEqual(calls.at(-1),['toast','unavailable']);plain.enabled=true;
  for(let i=0;i<100;i++)ctx.addFromCard(plain);
  assert.equal(ctx.cardQuantity('p1'),100);assert.ok(ctx.state.cart.every(line=>line.quantity<=30));
  ctx.addFromCard(plain);assert.equal(ctx.cardQuantity('p1'),100);assert.equal(calls.at(-1)[0],'toast');
}
{
  const {ctx,plain}=fixture();ctx.state.cart=[{productId:'p1',rowId:'a',quantity:1,options:{},note:'Existing optional note'}];
  ctx.addFromCard(plain);assert.equal(ctx.state.cart.length,2,'Blank-note photo taps must not overwrite an existing note');
  ctx.state.products=[];ctx.addFromCard(plain);assert.equal(ctx.cardQuantity('p1'),2,'A stale card cannot add a missing catalog item');
}
console.log('PASS: rapid photo taps, explicit variant removal, required options, item/line limits, availability and note preservation.');
