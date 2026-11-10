'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('public/assets/dispatch-map.js','utf8');
const loader=source.slice(source.indexOf('  const SDK='),source.indexOf('  function point'));
function setup(){
  const nodes=[],timers=new Map();let id=0;
  const context={window:{},Promise,Error,
    document:{createElement:type=>({type,removed:false,remove(){this.removed=true;}}),head:{append:n=>nodes.push(n)}},
    setTimeout:cb=>{timers.set(++id,cb);return id;},clearTimeout:key=>timers.delete(key)};
  vm.createContext(context);vm.runInContext(loader+';window.loadSDK=sdk;',context);
  return {context,nodes,timers,load:()=>context.window.loadSDK()};
}
test('concurrent maps share one SDK request and successful load clears deadline',async()=>{
  const t=setup(),a=t.load(),b=t.load();assert.equal(a,b);assert.equal(t.nodes.length,2);
  t.context.window.mapboxgl={version:'synthetic'};t.nodes[1].onload();
  assert.equal((await a).version,'synthetic');assert.equal(t.timers.size,0);
});
for(const mode of ['error','missing-global','timeout'])test(`SDK ${mode} permits a clean retry`,async()=>{
  const t=setup(),first=t.load(),rejected=assert.rejects(first,/sdk/);
  if(mode==='error')t.nodes[1].onerror();
  else if(mode==='missing-global')t.nodes[1].onload();
  else [...t.timers.values()][0]();
  await rejected;
  assert.equal(t.nodes[0].removed,true);assert.equal(t.nodes[1].removed,true);
  const second=t.load();assert.notEqual(first,second);assert.equal(t.nodes.length,4);
  t.context.window.mapboxgl={version:'retry'};t.nodes[3].onload();assert.equal((await second).version,'retry');
});
test('late events after timeout do not settle or reset a newer request',async()=>{
  const t=setup(),first=t.load(),rejected=assert.rejects(first,/sdk/);[...t.timers.values()][0]();await rejected;
  const second=t.load();t.nodes[1].onerror();assert.equal(t.load(),second);
  t.context.window.mapboxgl={};t.nodes[3].onload();await second;
});
