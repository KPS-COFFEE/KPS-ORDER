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
const ctx={mahashiFamily:()=>[],renderMahashiSizes:()=>{},state:{product:p,optionValues:[{}],branchCatalogPending:false,branchId:'b',branchCatalogId:'b'},requestedBrandId:'enbishan',
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

// The four visible choices are independent flavours; one or two selections
// map to an approved server variant without requiring a second flavour.
vm.runInContext(source.slice(source.indexOf('  const grapeFlavourDrafts='),source.indexOf('  function renderOptions()')),ctx);
ctx.requestedBrandId='enbishan';
const flavour={code:'FLAVOUR',values:[
 {code:'pair-ps',name:'دبس رمان + سبايسي',name_en:'Pomegranate molasses + Spicy'},
 {code:'pair-pl',name:'دبس رمان + ليمون',name_en:'Pomegranate molasses + Lemon'},
 {code:'pair-sl',name:'سبايسي + ليمون',name_en:'Spicy + Lemon'},
 {code:'single-pomegranate',name:'دبس رمان',name_en:'Pomegranate molasses'},
 {code:'single-spicy',name:'سبايسي',name_en:'Spicy'},
 {code:'single-lemon',name:'ليمون',name_en:'Lemon'},
 {code:'mixed',name:'مشكل',name_en:'Mixed'}]};
ctx.state.optionValues=[{},{}];
let picker=ctx.renderGrapeFlavours(p,flavour,0),flavours=picker.querySelectorAll('button');
assert.deepEqual(flavours.map(b=>b.textContent),['Pomegranate molasses','Spicy','Lemon','Mixed']);
flavours[0].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'single-pomegranate');
flavours[1].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'pair-ps');assert.equal(flavours[2].disabled,true);
flavours[3].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'mixed');assert.deepEqual(flavours.map(b=>b.attributes['aria-pressed']),['false','false','false','true']);
flavours[3].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,undefined);assert.equal(flavours[3].attributes['aria-pressed'],'false');
flavours[3].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'mixed');
flavours[2].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'single-lemon');assert.equal(flavours[3].attributes['aria-pressed'],'false');
flavours[0].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'pair-pl');
flavours[0].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'single-lemon');
flavours[1].listeners.click();assert.equal(ctx.state.optionValues[0].FLAVOUR,'pair-sl');
assert.equal(ctx.state.optionValues[1].FLAVOUR,undefined);
picker=ctx.renderGrapeFlavours(p,flavour,0);assert.equal(picker.querySelectorAll('button')[2].attributes['aria-pressed'],'true');
ctx.state.optionValues[1]={...ctx.state.optionValues[0]};picker=ctx.renderGrapeFlavours(p,flavour,1);assert.equal(picker.querySelectorAll('button')[1].attributes['aria-pressed'],'true');
ctx.requestedBrandId='kps';assert.equal(ctx.renderGrapeFlavours(p,flavour,0),null);
assert.equal(picker.querySelectorAll('p').length,0,'No explanatory flavour note');
console.log('PASS: single flavours, pairs, two-pick limit, mixed exclusivity and independent units.');

// Exercise the manager's actual picker and item serializer with DOM input doubles.
const staffSource=fs.readFileSync('public/assets/customer-order.js','utf8');
const product=JSON.parse(fs.readFileSync('tests/fixtures/enbishan-grape-flavours-public.json','utf8'))[0];
const staffGroup=product.option_groups.find(g=>g.code==='FLAVOUR');
const inputs=['single-pomegranate','single-spicy','single-lemon','v-2606b799a8a89a992bffd12e'].map(value=>({value,checked:false,disabled:false,dataset:{grapePick:'0'},matches:selector=>selector==='input[data-grape-pick]'}));
const hidden=staffGroup.values.map(v=>({value:v.code,checked:false}));
let onChange;
const staffCtx={enbishan:{},esc:String,tr:(ar,en)=>en,options:{addEventListener:(event,fn)=>{onChange=fn;},querySelectorAll:selector=>selector.startsWith('[data-grape-pick')?inputs:selector.endsWith(':checked')?hidden.filter(x=>x.checked):hidden,focus:()=>{}},
 products:[product],select:{value:product.id},productGroups:()=>[staffGroup],selectedDescription:()=>{},invalidate:()=>{},form:{elements:{item_note:{value:''}}},structuredClone};
vm.createContext(staffCtx);
vm.runInContext(staffSource.slice(staffSource.indexOf('    function grapeOptions('),staffSource.indexOf('    function drawOptions(){')),staffCtx);
vm.runInContext(staffSource.slice(staffSource.indexOf('    function selectedItem('),staffSource.indexOf('    function sameVariant(')),staffCtx);
function pick(index,checked=true){assert.equal(inputs[index].disabled,false);inputs[index].checked=checked;onChange({target:inputs[index]});}
function encoded(){return hidden.filter(x=>x.checked).map(x=>x.value);}
pick(0);assert.deepEqual(encoded(),['single-pomegranate']);
let item=staffCtx.selectedItem(1);assert.equal(item.units[0].options.FLAVOUR[0],'single-pomegranate');assert.equal(item.note,'');
pick(1);assert.deepEqual(encoded(),['pair-783331fde37a9bb0a6e0']);assert.equal(inputs[2].disabled,true);
pick(3);assert.deepEqual(encoded(),['v-2606b799a8a89a992bffd12e']);assert.equal(inputs.filter(x=>x.checked).length,1);
pick(2);assert.deepEqual(encoded(),['single-lemon']);assert.equal(inputs[3].checked,false);
pick(2,false);assert.deepEqual(encoded(),[]);
assert.throws(()=>staffCtx.selectedItem(1),/Check selections/);
console.log('PASS: manager single/pair/mixed choices serialize as structured options with no item note.');
