const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/assets/customer-google-map.js','utf8');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.disabled=false;this.hidden=false;this.open=false;this.textContent='';this.attributes={};}
  append(...children){for(const el of children){el.parent=this;this.children.push(el);}}
  prepend(el){el.parent=this;this.children.unshift(el);}
  replaceChildren(...children){this.children=[];this.append(...children);}
  setAttribute(k,v){this.attributes[k]=v;}
  addEventListener(k,fn){this.events[k]=fn;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(e=>e!==this);}
  showModal(){this.open=true;}
  close(){this.open=false;this.events.close?.();}
  focus(){}
}
function setup(lang='ar',preload=true){
  const timers=new Map(),committed=[],constructed=[],external=[],observers=[];let timerId=0,saved=null,allowed=true;
  class GoogleMap{
    constructor(container,options){this.events={};this.center={...options.center};this.zoom=options.zoom;constructed.push(this);this.options=options;}
    addListener(name,fn){this.events[name]=fn;return {remove:()=>delete this.events[name]};}
    getCenter(){return {toJSON:()=>({...this.center})};}
    setCenter(p){this.center={...p};this.events.center_changed?.();}
    setZoom(z){this.zoom=z;}
    getZoom(){return this.zoom;}
  }
  const sdk={Map:GoogleMap,RenderingType:{RASTER:'RASTER'},event:{clearInstanceListeners(map){map.events={};},trigger(){}}};
  const body=new Element('body'),head=new Element('head'),host=new Element('host');
  const ctx={console,URLSearchParams,Number,Math,Promise,Set,Error,
    document:{createElement:tag=>new Element(tag),body,head,documentElement:{lang}},
    navigator:{geolocation:{getCurrentPosition(success,error,options){ctx.gps={success,error,options};}}},
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    MutationObserver:class{constructor(fn){observers.push(fn);}observe(){}}};
  ctx.window=ctx;if(preload)ctx.google={maps:sdk};vm.createContext(ctx);vm.runInContext(source,ctx);
  const config={enabled:true,provider:'google',browser_key:'AIza'+'a'.repeat(35)};
  return {ctx,body,head,host,sdk,timers,committed,constructed,external,observers,config,
    async mount(){await ctx.KPSCustomerGoogleMap.mount({config,brandId:'kps',host,getLocation:()=>saved,onConfirm:(lat,lng)=>{saved={lat,lng};committed.push(saved);},tr:(ar,en)=>ctx.document.documentElement.lang==='en'?en:ar,canOpen:()=>allowed});},
    get allowed(){return allowed;},set allowed(value){allowed=value;},get saved(){return saved;}};
}
const ui=s=>{const dialog=s.body.children[0],toolbar=dialog.children[0],frame=dialog.children[1],actions=dialog.children[3];return {dialog,button:s.host.children[0],close:toolbar.children[1],surface:frame.children[0],pin:frame.children[1],status:dialog.children[2],locate:actions.children[0],confirm:actions.children[1]};};
async function main(){
 for(const lang of ['ar','en']){
  const s=setup(lang);await s.mount();const u=ui(s);assert.equal(s.head.children.length,0,'SDK is lazy');
  await u.button.events.click();const m=s.constructed[0];assert.equal(m.options.renderingType,'RASTER');assert.equal(m.options.mapId,undefined);assert.equal(m.options.streetViewControl,false);
  assert.equal(u.confirm.disabled,true);m.events.tilesloaded();assert.equal(u.confirm.disabled,true,'Default city center is not a customer selection');assert.equal(u.locate.disabled,false);assert.equal(u.pin.hidden,false);
  m.events.click({latLng:{toJSON:()=>({lat:28.45,lng:45.97})}});assert.equal(u.confirm.disabled,false);assert.equal(s.committed.length,0);
  u.close.events.click();assert.equal(s.committed.length,0,'Cancel never commits a location');
  await u.button.events.click();assert.equal(s.constructed.length,1,'One reusable Google map');assert.equal(u.confirm.disabled,true,'Cancel discards the previous draft');
  m.events.dragstart();m.center={lat:28.46,lng:45.98};m.events.center_changed();assert.equal(u.confirm.disabled,true,'Never confirm while the map is moving');m.events.dragend();u.confirm.events.click();assert.deepEqual(s.saved,{lat:28.46,lng:45.98});
  await u.button.events.click();u.locate.events.click();assert.deepEqual(JSON.parse(JSON.stringify(s.ctx.gps.options)),{enableHighAccuracy:true,timeout:15000,maximumAge:0});
  s.ctx.gps.success({coords:{latitude:28.47,longitude:45.99,accuracy:7.2}});assert.match(u.status.textContent,/8/);assert.equal(s.committed.length,1,'GPS needs explicit confirmation');
  u.confirm.events.click();assert.deepEqual(s.saved,{lat:28.47,lng:45.99});
  await u.button.events.click();u.locate.events.click();s.ctx.gps.success({coords:{latitude:28.4,longitude:45.9,accuracy:300}});assert.match(u.status.textContent,/300/);assert.match(u.status.textContent,lang==='ar'?/الدقة منخفضة/:/Low accuracy/);
  u.locate.events.click();const stale=s.ctx.gps;u.surface.events.pointerdown();m.events.click({latLng:{toJSON:()=>({lat:28.48,lng:46.01})}});stale.success({coords:{latitude:28.1,longitude:45.1,accuracy:1}});u.confirm.events.click();assert.deepEqual(s.saved,{lat:28.48,lng:46.01},'Late GPS cannot replace a deliberate pin');
  await u.button.events.click();u.locate.events.click();const afterClose=s.ctx.gps;u.close.events.click();afterClose.success({coords:{latitude:28.1,longitude:45.1,accuracy:1}});assert.equal(s.committed.length,3);
  await u.button.events.click();s.allowed=false;u.confirm.events.click();assert.equal(s.committed.length,3,'Confirmation rechecks current authorization to open');s.allowed=true;
  u.locate.events.click();s.ctx.gps.error({code:1});assert.equal(u.locate.disabled,false);assert.match(u.status.textContent,lang==='ar'?/حرّك الخريطة/:/move the map/);
  s.ctx.document.documentElement.lang=lang==='ar'?'en':'ar';s.observers[0]();assert.match(u.status.textContent,lang==='ar'?/move the map/:/حرّك الخريطة/);
  const lateTiles=m.events.tilesloaded;s.ctx.gm_authFailure();assert.equal(u.confirm.disabled,true);assert.equal(u.pin.hidden,true);lateTiles();assert.equal(u.confirm.disabled,true,'Late tiles never resurrect an authentication failure');assert.match(u.status.textContent,/Google/);
  assert.equal(s.head.children.length,0,'Preloaded synthetic API never makes an external request');
 }
 // Real loader contract is exercised by manually completing a synthetic script callback.
 const loading=setup('en',false);await loading.mount();const u=ui(loading);const opening=u.button.events.click();assert.equal(loading.head.children.length,1);
 const script=loading.head.children[0],url=new URL(script.src);assert.equal(url.origin,'https://maps.googleapis.com');assert.equal(url.searchParams.get('v'),'quarterly');assert.equal(url.searchParams.get('language'),'en');
 loading.ctx.google={maps:loading.sdk};loading.ctx.__kpsCustomerGoogleReady();await opening;loading.constructed[0].events.tilesloaded();assert.equal(u.locate.disabled,false);
 const broken=setup('ar',false);await broken.mount();const b=ui(broken);const badOpen=b.button.events.click();broken.head.children[0].onerror();await badOpen;assert.equal(b.confirm.disabled,true);assert.match(b.status.textContent,/Google/);assert.equal(broken.head.children.length,0);
 const timeout=setup('ar',false);await timeout.mount();const t=ui(timeout);const slow=t.button.events.click();[...timeout.timers.values()].find(v=>v.ms===20000).fn();await slow;assert.equal(t.confirm.disabled,true);assert.match(t.status.textContent,/Google/);
 console.log('PASS: Google raster lazy SDK, explicit pin/GPS confirmation, cancel/reuse, precision/races, bilingual states, access/auth/network/timeout failures; no Google requests.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
