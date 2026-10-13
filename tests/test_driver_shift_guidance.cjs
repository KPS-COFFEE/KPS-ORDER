const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const helpers=source.slice(source.indexOf('  function ownUnclosedDriverShift('),source.indexOf('  async function tasks()'));
const open=source.split('\n').find(x=>x.startsWith('  async function openShift()'));
(async()=>{
for(const lang of ['ar','en'])for(const status of ['open','closing','returned','submitted']){
 const shift={id:'my-shift',kind:'driver',user_id:'me',status};let modalCalls=0,peopleCalls=0,loaded=0;
 const context={state:{user:{id:'me',role:'driver'}},tr:(ar,en)=>lang==='ar'?ar:en,esc:String,$:()=>({open:false}),loadBranches:async()=>{},api:async()=>({shifts:[{...shift,id:'other',user_id:'other'},shift]}),people:async()=>{peopleCalls++;throw Error('must not load a new shift form')},modal:()=>{modalCalls++},loadPage:async()=>{loaded++},setStatus:()=>{}};
 vm.createContext(context);vm.runInContext(helpers+'\n'+open+'\nthis.run=openShift;this.guidance=driverShiftGuidance;this.pick=ownUnclosedDriverShift;',context);
 await context.run();assert.equal(modalCalls,0);assert.equal(peopleCalls,0);assert.equal(loaded,1);assert.equal(context.state.page,status==='open'?'tasks':'shifts');
 assert.equal(context.guidance(shift).action,status==='open'?undefined:'shift-detail');
 assert.equal(context.guidance(null).action,'open-shift');
 assert.equal(context.pick([{...shift,status:'closed'}]),undefined);
 assert.equal(context.pick([{...shift,user_id:'other'}]),undefined);
}
console.log('PASS: 8 AR/EN active-shift scenarios; no duplicate form; correct next page; own-user and closed-shift filtering.');
})().catch(e=>{console.error(e);process.exit(1)});
