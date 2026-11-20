'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('public/assets/dispatch-map.js','utf8');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.listeners={};this.isConnected=true;this.options=[];this.classList={add(){},remove(){}};}
  append(...nodes){this.children.push(...nodes);if(this.tag==='select')this.options.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];if(this.tag==='select')this.options=[...nodes];}
  setAttribute(){} addEventListener(name,cb){this.listeners[name]=cb;} closest(){return this;} getClientRects(){return [1];}
  remove(){} get text(){return (this.textContent||'')+this.children.map(n=>n.text||'').join(' ');}
}
async function setup({mode='ok',apiFailure=false}={}){
  const host=new Element('div'),maps=[];let calls=0,constructs=0,supported=mode!=='webgl';
  class MapView{constructor(){constructs++;if(mode==='constructor')throw new Error('https://example.invalid/?access_token=secret');this.events={};maps.push(this);}on(name,cb){this.events[name]=cb;}addControl(){}remove(){this.removed=true;}resize(){} }
  const window={mapboxgl:{supported:()=>supported,Map:MapView,NavigationControl:class{}}};
  if(mode==='sdk')delete window.mapboxgl;
  const document={createElement:tag=>new Element(tag),createTextNode:text=>({text}),head:new Element('head'),hidden:false};
  if(mode==='sdk')document.head.append=node=>{if(node.tag==='script')queueMicrotask(()=>node.onerror());};
  const context={window,document,Map,Date,Number,Promise,Error,URL,MutationObserver:class{observe(){}disconnect(){}},setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout};
  vm.createContext(context);vm.runInContext(source,context);
  const controller=await window.KPSDispatchMap.mount(host,{orders:[],drivers:[],branches:[],api:async()=>{calls++;if(apiFailure)throw new Error('secret config URL');return {enabled:true,provider:'mapbox',public_token:'pk.synthetic-secret',style:'synthetic'};}});
  return {host,maps,controller,constructs:()=>constructs,calls:()=>calls,enableWebGL:()=>{supported=true;},state:()=>host.children[0],async retry(){host.children[0].children.find(x=>x.tag==='button').listeners.click();await new Promise(resolve=>setImmediate(resolve));}};
}
for(const [mode,code] of [['webgl','MAP_WEBGL'],['constructor','MAP_INIT'],['config','MAP_CONFIG'],['sdk','MAP_SDK']])test(`reports fixed ${code} code and never raw error/token`,async()=>{
  const t=await setup({mode,apiFailure:mode==='config'});
  assert.match(t.state().text,new RegExp('\\['+code+'\\]'));assert.doesNotMatch(t.state().text,/secret|access_token|example/);
  assert.equal(t.host.children[1].hidden,true);assert.equal(t.state().children.filter(x=>x.tag==='button').length,1);
  t.controller.destroy();
});
test('explicit WebGL retry rechecks support and never auto-retries',async()=>{
  const t=await setup({mode:'webgl'});assert.equal(t.calls(),1);assert.equal(t.constructs(),0);
  t.enableWebGL();await t.retry();assert.equal(t.calls(),2);assert.equal(t.constructs(),1);
  assert.equal(t.state().text.includes('MAP_WEBGL'),false);t.controller.destroy();
});
test('provider errors expose only auth/resource codes and ignore old-map events after retry',async()=>{
  const t=await setup(),old=t.maps[0];
  old.events.error({error:{status:403,message:'secret URL https://x/?access_token=secret'}});
  assert.match(t.state().text,/\[MAP_AUTH\]/);assert.doesNotMatch(t.state().text,/secret|access_token/);
  await t.retry();assert.equal(old.removed,true);assert.equal(t.maps.length,2);
  old.events.error({error:{status:401}});assert.doesNotMatch(t.state().text,/MAP_AUTH/);
  t.maps[1].events.error({error:{status:500,message:'secret'}});assert.match(t.state().text,/\[MAP_RESOURCE\]/);
  t.controller.destroy();
});

test('loaded-map resource failure retains map, has no remount retry and clears on recovery',async()=>{
  const t=await setup(),map=t.maps[0];map.events.load();
  map.events.error({error:{status:503,message:'secret'}});
  assert.match(t.state().text,/\[MAP_RESOURCE\]/);
  assert.equal(t.state().children.some(x=>x.tag==='button'),false);
  map.isStyleLoaded=()=>false;map.events.idle();assert.equal(t.state().hidden,false);
  map.isStyleLoaded=()=>true;map.events.idle();assert.equal(t.state().hidden,true);
  assert.equal(t.maps.length,1);assert.equal(t.calls(),1);t.controller.destroy();
});
