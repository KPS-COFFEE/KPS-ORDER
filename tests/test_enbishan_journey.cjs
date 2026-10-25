'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
class Element {
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.listeners={};this.attributes={};this.value='';this.options=[];}
 append(...children){this.children.push(...children);if(this.tag==='select')this.options.push(...children);}
 replaceChildren(){this.children=[];}
 setAttribute(k,v){this.attributes[k]=v;}
 getAttribute(k){return this.attributes[k];}
 addEventListener(k,fn){this.listeners[k]=fn;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll?.(tag)||[]]);}
}
const fields={};let priced=0;
const p={id:'e1'};const group={code:'SIZE',name:'Size',required:true,values:[{code:'six',name:'6 pieces'},{code:'twelve',name:'12 pieces'},{code:'closed',name:'Unavailable',available:false}]};
const ctx={state:{product:p,optionValues:[{}],branchCatalogPending:false,branchId:'b',branchCatalogId:'b'},requestedBrandId:'enbishan',
 $:id=>fields[id]||(fields[id]=new Element('div')),groups:()=>[group],fixedSingleSize:()=>false,isMug:()=>false,isKhaliya:()=>false,
 node:(tag,cls,text)=>new Element(tag,text),Option:function(text,value){const el=new Element('option',text);el.value=value;return el;},
 entryText:(ar,en)=>en,text:()=>{},number:{format:String},t:x=>x,groupLabel:g=>g.name,sourceText:v=>v.name,eligibleForProduct:()=>({ok:true}),showError:()=>{},renderVariantPrice:()=>priced++};
fields['product-qty']=new Element('input');fields['product-qty'].value='1';vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('  function renderOptions()'),source.indexOf('  function addProduct()',source.indexOf('  function renderOptions()'))>0?source.indexOf('  function addProduct()',source.indexOf('  function renderOptions()')):source.indexOf('  async function addProduct()',source.indexOf('  function renderOptions()'))),ctx);
ctx.renderOptions();let buttons=fields['product-options'].querySelectorAll('button');assert.equal(buttons.length,3);assert.equal(buttons[2].disabled,true);
buttons[1].listeners.click();assert.equal(ctx.state.optionValues[0].SIZE,'twelve');assert.equal(buttons[1].attributes['aria-pressed'],'true');assert.equal(buttons[0].attributes['aria-pressed'],'false');assert.ok(priced>=2);
fields['product-qty'].value='2';ctx.renderOptions();buttons=fields['product-options'].querySelectorAll('button');buttons[3].listeners.click();assert.equal(ctx.state.optionValues[0].SIZE,'twelve');assert.equal(ctx.state.optionValues[1].SIZE,'six');
ctx.requestedBrandId='kps';ctx.renderOptions();assert.equal(fields['product-options'].querySelectorAll('button').length,0);
console.log('PASS: direct Enbishan choices preserve disabled options, independent unit selections, pricing refresh and KPS controls.');
