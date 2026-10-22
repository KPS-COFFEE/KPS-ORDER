const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
const helpers=source.slice(source.indexOf('  function persistLocationDraft()'),source.indexOf('  function applyTheme('));
const verify=source.split('\n').find(line=>line.includes('async function verifyOTP(event)'));
function storage(initial={}){return {data:{...initial},getItem(k){return this.data[k]??null;},setItem(k,v){this.data[k]=String(v);},removeItem(k){delete this.data[k];}};}
function setup(lang='ar',saved={},user=null){
  const elements=new Map(),timers=new Map();let nextTimer=0;
  const element=id=>{if(!elements.has(id))elements.set(id,{id,value:'',hidden:false,disabled:false,textContent:'',listeners:{},setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];},after(child){elements.set(child.id,child);},addEventListener(name,cb){this.listeners[name]=cb;}});return elements.get(id);};
  const ctx={console,AbortController,Date,Number,JSON,Math,Promise,requestedBrandId:'kps',locationDraftKey:'draft:kps',
    state:{session:{user,csrf_token:user?'synthetic':null},location:null,branchId:'',service:'delivery',branches:[{id:'b1'}],entryEnabled:true,locationDraftRestored:true},
    mapLink:{revision:0,pending:false,url:'',appliedUrl:'',timer:null,controller:null,promise:null},sessionStorage:storage(saved),localStorage:storage(),
    $:element,node:(tag,cls)=>({tag,className:cls,setAttribute(k,v){this[k]=v;}}),entryText:(a,b)=>lang==='ar'?a:b,
    entryVerified:()=>!!ctx.state.session.user,t:s=>s,text:(id,v)=>{element(id).textContent=v;},toast:()=>{},
    queueQuote:()=>ctx.persistLocationDraft(),queueNearestBranches:()=>{},renderBranches:()=>{},renderService:()=>{},loadBranchCatalog:()=>{},
    setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearTimeout:id=>timers.delete(id),window:{addEventListener(){}},
    renderAccount:()=>{},closeDialog:()=>{},showError:()=>{},busy:async(button,fn)=>fn(),errorMessage:e=>e.message};
  vm.createContext(ctx);vm.runInContext(helpers+'\n'+verify,ctx);return {ctx,element,timers};
}
const link='https://maps.app.goo.gl/SYNTHETIC?g_st=iw',pin={lat:28.383083,lng:46.000244};
async function main(){for(const lang of ['ar','en']){
  const {ctx:c,element:e,timers}=setup(lang);let requests=0;
  c.api=async(path,options)=>{requests++;assert.equal(path,'/api/location/parse-map-url');assert.equal(options.data.url,link);return {location:pin};};
  c.bindLocationLink();e('map-url').value='الموقع: \u200f'+link;e('map-url').listeners.input();
  assert.equal(c.state.location,null);assert.equal(c.mapLink.pending,true);assert.ok(e('map-link-status').textContent);
  [...timers.values()][0]();await c.mapLink.promise;
  assert.equal(requests,1);assert.equal(c.state.location.lat,pin.lat);assert.equal(e('map-url').value,link);
  assert.match(e('map-link-status').textContent,/✓/);assert.equal(e('parse-map-url').disabled,false);
  await c.resolveMapLink();assert.equal(requests,1,'unchanged successful link is not fetched again');
  c.state.branchId='b1';c.persistLocationDraft();
  const draft=c.sessionStorage.data;const reload=setup(lang,draft);reload.ctx.state.locationDraftRestored=false;
  reload.ctx.persistLocationDraft();assert.ok(reload.ctx.sessionStorage.getItem('draft:kps'),'startup must not delete the draft before restoring');
  assert.equal(reload.ctx.restoreLocationDraft(),true);assert.equal(reload.ctx.state.location.lng,pin.lng);assert.equal(reload.ctx.state.branchId,'b1');
  assert.equal(reload.element('map-url').value,link);
  c.api=async()=>({user:{id:'customer-A',role:'customer',brand_id:'kps',phone:'synthetic',display_name:'Test'},csrf_token:'synthetic',customer_verified:true});
  await c.verifyOTP({preventDefault(){}});
  assert.equal(JSON.parse(c.sessionStorage.getItem('draft:kps')).owner,'customer-A','OTP binds the current tab draft to its customer');
  const other=setup(lang,c.sessionStorage.data,{id:'customer-B',role:'customer',brand_id:'kps'});
  assert.equal(other.ctx.restoreLocationDraft(),false);assert.equal(other.ctx.state.location,null);
  e('clear-location').listeners.click();assert.equal(c.sessionStorage.getItem('draft:kps'),null);assert.equal(e('map-url').value,'');
  const bad=setup(lang);bad.element('map-url').value=link;bad.ctx.api=async()=>({location:{lat:999,lng:46}});
  assert.equal(await bad.ctx.resolveMapLink(),false);assert.equal(bad.ctx.state.location,null);assert.equal(bad.ctx.sessionStorage.getItem('draft:kps'),null);
  assert.equal(bad.element('map-link-status').className,'notice error');
  let finish;const stale=setup(lang);stale.element('map-url').value=link;stale.ctx.api=()=>new Promise(resolve=>{finish=resolve;});
  const pending=stale.ctx.resolveMapLink();stale.ctx.saveLocation(28.5,46.1);finish({location:pin});await pending;
  assert.equal(stale.ctx.state.location.lat,28.5,'late link resolution cannot override a newer map or GPS selection');
  let first,second;const race=setup(lang);race.element('map-url').value=link;
  race.ctx.api=(path,options)=>new Promise(resolve=>{if(options.data.url===link)first=resolve;else second=resolve;});
  const old=race.ctx.resolveMapLink();race.element('map-url').value=link+'&new=1';race.ctx.scheduleMapLink();const fresh=race.ctx.resolveMapLink();
  second({location:{lat:28.6,lng:46.2}});await fresh;first({location:pin});await old;assert.equal(race.ctx.state.location.lat,28.6);
  const expired=setup(lang,{'draft:kps':JSON.stringify({location:pin,owner:null,savedAt:Date.now()-9*60*60*1000})});
  assert.equal(expired.ctx.restoreLocationDraft(),false);
}
console.log('PASS: AR/EN automatic paste, shared-text links, deduplication, refresh, OTP ownership, clearing, expiry, invalid coordinates and stale-request protection.');}
main().catch(error=>{console.error(error);process.exitCode=1;});
