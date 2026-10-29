'use strict';
// Exercise the actual access monitor; real HTTP/Chromium cases remain separate.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/assets/customer-report.js','utf8');
const monitor=source.slice(source.indexOf('  function maskAccess()'),source.indexOf('  async function load(pageOffset=0, existing=null)'));
assert(monitor.includes('async function checkAccess()'));
function setup(fetch){
 const attrs=new Map(),nodes=new Map(),calls=[];
 const el=id=>{if(!nodes.has(id))nodes.set(id,{children:['PRIVATE PHONE'],hidden:false,disabled:false,textContent:'PRIVATE PHONE',href:'',className:'',replaceChildren(){this.children=[]}});return nodes.get(id)};
 const context={stopped:false,accessAllowed:false,accessController:null,accessPromise:null,accessGeneration:0,actor:null,accessFingerprint:null,reportPending:true,data:{private:true},applied:{q:'private'},offset:50,requestId:0,controller:null,headers:{'X-KPS-Context':'work'},brand:'kps',accessTimer:1,AbortController,
  document:{hidden:false,documentElement:{setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k)}},navigator:{onLine:true},form:{hidden:false,reset(){calls.push('form-reset')}},$:el,tr:(a,b)=>b,encodeURIComponent,clearInterval:()=>calls.push('timer-stopped'),setTimeout:()=>1,clearTimeout(){},load:async()=>calls.push('report-load'),
  fetch:async(...args)=>{calls.push(args);return fetch?fetch(...args):{ok:true,status:200,json:async()=>({user:{id:'owner-a',role:'owner',brand_id:'kps',branch_ids:[]},csrf_token:'valid-csrf'})}}};
 vm.createContext(context);vm.runInContext(monitor,context);return {context,attrs,nodes,calls};
}
(async()=>{
 let cases=0;
 for(const session of [{user:null},{user:{id:'owner-a',role:'driver',brand_id:'kps'},csrf_token:'x'},{user:{id:'owner-a',role:'owner',brand_id:'kps'},reauthentication_required:true}]){
  const t=setup(async()=>({ok:true,status:200,json:async()=>session}));await t.context.checkAccess();assert(t.context.stopped);assert.equal(t.context.data,null);assert.equal(t.context.applied,null);assert(t.context.form.hidden);assert.equal(t.nodes.get('ranking').children.length,0);assert.equal(t.nodes.get('report-signin').hidden,false);cases++;
 }
 const good=setup();good.context.maskAccess();assert.equal(await good.context.checkAccess(),true);assert.equal(good.attrs.size,0);assert.equal(good.context.headers['X-Operations-Brand'],'kps');assert(good.nodes.get('back-link').href.includes('operations_brand=kps'));cases++;
 const hidden=setup();hidden.context.document.hidden=true;await hidden.context.checkAccess();hidden.context.document.hidden=false;hidden.context.navigator.onLine=false;await hidden.context.checkAccess();assert.equal(hidden.calls.length,0);cases++;
 let release;const stale=setup(()=>new Promise(resolve=>release=resolve));const waiting=stale.context.checkAccess();stale.context.pauseAccess();release({ok:true,status:200,json:async()=>({user:{id:'owner-a',role:'owner',brand_id:'kps'},csrf_token:'x'})});assert.equal(await waiting,false);assert.equal(stale.context.actor,null);assert(stale.attrs.has('data-customer-access-pending'));cases++;
 let resolve;const overlap=setup(()=>new Promise(r=>resolve=r));const a=overlap.context.checkAccess(),b=overlap.context.checkAccess();assert.equal(overlap.calls.length,1);resolve({ok:true,status:200,json:async()=>({user:{id:'owner-a',role:'owner',brand_id:'kps'},csrf_token:'x'})});assert(await a);assert(await b);cases++;
 const changed=setup();await changed.context.checkAccess();changed.context.accessFingerprint='previous-branch-scope';await changed.context.checkAccess();assert.equal(changed.context.data,null);assert(changed.calls.includes('report-load'));cases++;
 for(const altered of ['actor','brand']){const t=setup();t.context[altered]=altered==='actor'?'different-owner':'enbishan';assert.equal(await t.context.checkAccess(),false);assert(t.context.stopped);assert.equal(t.context.data,null);cases++;}
 console.log('PASS '+cases+' actual customer-report monitor cases: role/revocation/reauth clearing, scope links, offline/hidden, stale response, coalescing and changed identity/scope.');
})().catch(e=>{console.error(e);process.exit(1)});
