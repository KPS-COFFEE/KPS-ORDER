'use strict';
// Offline tests execute the shipped helpers. No customer traffic or DB writes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'public/customer.js'),'utf8');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
const prefix=source.slice(0,source.indexOf('  const brandParameters='));
const line=starts=>source.split('\n').find(value=>value.startsWith(starts));
function load(saved=null,blocked=false){
  class Element {
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.isConnected=true;this.value='';this.classList={add(){},toggle(){}};}
    set textContent(value){this.copy=String(value);this.children=[];}
    get textContent(){return (this.copy||'')+this.children.map(child=>child.textContent||'').join('');}
    setAttribute(name,value){this[name]=String(value);}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.copy='';this.children=children;}
    addEventListener(){}
    querySelectorAll(selector){
      assert.match(selector,/^\.[a-z-]+$/,'fixture only supports the shipped class selectors');
      const name=selector.slice(1),matches=[];
      for(const child of this.children){
        if(String(child.className||'').split(/\s+/).includes(name))matches.push(child);
        matches.push(...child.querySelectorAll(selector));
      }
      return matches;
    }
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    get childElementCount(){return this.children.length;}
  }
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,new Element());
    return elements.get(id);
  };
  const storage={getItem(){if(blocked)throw Error('Storage blocked');return saved;}};
  const state={products:structuredClone(catalog.products),category:'',brand:{id:'kps'},cart:[]};
  const context={Intl,URL,state,requestedBrandId:'kps',location:{origin:'https://fixture.invalid'},
    eligibleForProduct:()=>({ok:true}),branch:()=>null,city:value=>value,
    localStorage:storage,queueMicrotask:fn=>fn(),
    document:{documentElement:{},getElementById:element,querySelectorAll:()=>[],createElement:tag=>new Element(tag)}};
  vm.createContext(context);
  const extra=[line('  const groups ='),line('  const isKhaliya ='),line('  const cartLineName ='),
    line('  const locale='),line('  const currency ='),line('  const money ='),line('  const number ='),
    line('  const node ='),line('  const text ='),line('  const safeImage ='),
    line('  const restricted ='),line('  const isMug ='),
    source.slice(source.indexOf('  function renderCategories()'),source.indexOf('  function renderCart()'))].join('\n');
  vm.runInContext(prefix+extra+`
    globalThis.probe={sourceText,productSearchText,groupLabel,cartLineName,uiStrings,t,money,
      applyStaticLanguage,renderProducts,renderCategories,current:()=>language,lang:next=>language=next};
  })();`,context);
  return {p:context.probe,document:context.document,element,state};
}
const arabic=/[\u0600-\u06ff]/;
test('Arabic default, explicit English preference and unavailable storage',()=>{
  for(const [saved,blocked,expected] of [[null,false,'ar'],['invalid',false,'ar'],['en',false,'en'],['en',true,'ar']]){
    const {p,document,element}=load(saved,blocked);p.applyStaticLanguage();
    assert.equal(p.current(),expected);
    assert.equal(document.documentElement.lang,expected);
    assert.equal(document.documentElement.dir,expected==='ar'?'rtl':'ltr');
    assert.equal(element('customer-language').textContent,expected==='ar'?'English':'العربية');
  }
});
test('every original product, category and choice has English display without source edits',()=>{
  const {p}=load('en');const before=JSON.stringify(catalog);
  for(const product of catalog.products){
    for(const field of ['name','category']){
      const label=p.sourceText(product,field);assert.ok(label,product.id+':'+field);
      assert.equal(arabic.test(label),false,product.id+':'+label);
    }
    for(const group of product.option_groups){
      assert.equal(arabic.test(String(p.groupLabel(group))),false);
      for(const value of group.values)assert.equal(arabic.test(p.sourceText(value)),false);
    }
  }
  p.lang('ar');
  for(const product of catalog.products){
    assert.equal(p.sourceText(product),product.name);
    assert.equal(p.sourceText(product,'category'),product.category);
  }
  assert.equal(JSON.stringify(catalog),before);
});
test('explicit translations win; unknown and renamed products are not fabricated',()=>{
  const {p}=load('en');const item=catalog.products[0];
  assert.equal(p.sourceText({...item,name_en:'Owner English name'}),'Owner English name');
  assert.equal(p.sourceText({...item,translations:{en:{name:'New name'}}}),'New name');
  assert.equal(p.sourceText({...item,name:'اسم معدل'}),'اسم معدل');
  assert.equal(p.sourceText({...item,brand_id:'another-brand'}),item.name);
  assert.equal(p.sourceText({...item,id:'new-product',source_id:'new-product'}),item.name);
  assert.equal(p.sourceText({...item,description:'وصف محفوظ'},'description'),'وصف محفوظ');
  assert.equal(p.sourceText(null),'');
  p.lang('ar');assert.equal(p.sourceText({...item,name_ar:'اسم عربي'}),'اسم عربي');
});
test('unified Khaliya and its selected basket type are localized, never rewritten',()=>{
  const {p}=load('en');const product={id:'kps-33',brand_id:'kps',name:'الخلية',unified_khaliya:true,
    option_groups:[{code:'TYPE',name:'اختر النوع',values:[{code:'classic',name:'كلاسيك'},{code:'pecan',name:'بيكان'}]}]};
  const before=JSON.stringify(product);
  assert.equal(p.cartLineName(product,{TYPE:'classic'}),'Khaliya — Classic');
  assert.equal(p.cartLineName(product,{TYPE:'pecan'}),'Khaliya — Pecan');
  p.lang('ar');assert.equal(p.cartLineName(product,{TYPE:'pecan'}),'الخلية — بيكان');
  assert.equal(JSON.stringify(product),before);
});
test('search recognizes both languages and category/option translations in either UI',()=>{
  const {p}=load();const mojito=catalog.products.find(item=>item.id==='kps-11');
  for(const lang of ['ar','en']){
    p.lang(lang);const text=p.productSearchText(mojito);
    for(const query of ['موهيتو','mojito','refreshers','المنعشات','strawberry','فراولة'])assert.ok(text.includes(query),lang+':'+query);
  }
});
test('static labels, direction and currency change together and round-trip',()=>{
  const {p,document}=load();
  for(const lang of ['ar','en','ar']){
    p.lang(lang);p.applyStaticLanguage();
    assert.equal(document.documentElement.dir,lang==='ar'?'rtl':'ltr');
    for(const [key,pair] of Object.entries(p.uiStrings))assert.ok(pair[lang],key);
    assert.equal(String(p.t('full_menu')),lang==='ar'?'كل المنيو':'Full menu');
    assert.match(String(p.money(950)),lang==='ar'?/ر\.س$/:/SAR$/);
    assert.equal(p.sourceText(catalog.products[0]),lang==='ar'?'كولد برو':'Cold Brew');
  }
});
test('shipped menu rendering keeps source identities and images while translating cards and tabs',()=>{
  const {p,element,state}=load();const before=JSON.stringify(state.products);
  function render(lang){p.lang(lang);p.renderCategories();p.renderProducts();return element('products').children.map(card=>({
    id:card.dataset.productId,title:card.children[1].children[0].textContent,
    image:card.children[0].children[0].src,alt:card.children[0].children[0].alt,
    zoom:card.children[1].children.at(-1).textContent,
  }));}
  const ar=render('ar'),en=render('en');
  for(const card of element('products').children){
    assert.equal(card.querySelector('.product-card-quantity').textContent,'0');
    assert.equal(card.querySelector('.product-minus-button').disabled,true);
  }
  assert.equal(en.length,catalog.products.length);
  assert.deepEqual(en.map(({id,image})=>({id,image})),ar.map(({id,image})=>({id,image})));
  assert.equal(en[0].title,'Cold Brew');
  for(const card of en){assert.equal(arabic.test(card.title),false);assert.equal(card.alt,card.title);assert.equal(card.zoom,'Enlarge photo');}
  assert.ok(element('categories').textContent.includes('Best Sellers'));
  element('search').value='strawberry';p.renderProducts();
  assert.equal(element('products').children[0].dataset.productId,'kps-11');
  p.lang('ar');p.renderProducts();assert.equal(element('products').children[0].dataset.productId,'kps-11');
  element('search').value='';assert.deepEqual(render('ar'),ar);
  state.cart=[{productId:catalog.products[0].id,quantity:3,options:{},note:''}];
  for(const lang of ['ar','en']){
    render(lang);
    const card=element('products').children[0];
    assert.equal(card.querySelector('.product-card-quantity').textContent,
      new Intl.NumberFormat(lang==='ar'?'ar-SA':'en-SA').format(3));
    assert.equal(card.querySelector('.product-minus-button').disabled,false);
  }
  assert.equal(JSON.stringify(state.products),before);
});
