const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const source=fs.readFileSync('public/assets/accounting.js','utf8');
 const start=source.indexOf('  function countWorkspaceBag('),end=source.indexOf('  async function load(){',start);
 let body='',submit,loads=0,closed=0,writes=[];
 const context={tr:(ar,en)=>en,text:(ar,en)=>en,esc:v=>String(v??'').replaceAll('<','&lt;'),field:(name,label,type,value,extra)=>`<input name="${name}" value="${value}" ${extra}>`,modal:(title,html,fn)=>{body=html;submit=fn;},closeModal:()=>closed++,cents:v=>Math.round(Number(v)*100),load:async()=>loads++,mutation:async(path,data)=>writes.push({path,data})};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 context.countWorkspaceBag({id:'bag /1',driver_name:'Driver <1>',reference:'REF',ready_to_count:false});assert.equal(submit,undefined);
 context.countWorkspaceBag({id:'bag /1',driver_name:'Driver <1>',reference:'REF',ready_to_count:true,count_id:'existing'});assert.equal(submit,undefined);
 context.countWorkspaceBag({id:'bag /1',driver_name:'Driver <1>',reference:'REF',declared_cash_halalas:700,ready_to_count:true});
 assert(body.includes('Driver &lt;1>'));assert(body.includes('name="cash" value=""'));assert(body.includes('name="card" value=""'));
 const values=new Map([['cash','6.50'],['card','9.00'],['reference','Actual collection evidence']]);
 await assert.rejects(()=>submit(values),/actual collection/);assert.equal(writes.length,0);
 values.set('confirmed','on');await submit(values);
 assert.deepEqual(JSON.parse(JSON.stringify(writes)),[{path:'/api/cash-bags/bag%20%2F1/count',data:{received_cash_halalas:650,card_halalas:900,reference:'Actual collection evidence',note:'',confirmed:true}}]);
 assert.equal(closed,1);assert.equal(loads,1);assert(!writes.some(w=>/approve|settle|allocation/.test(w.path)));
 console.log('PASS: accounting bag count requires witnessed collection, starts amounts empty, preserves actual partial cash, refreshes queue and never posts settlement');
})().catch(error=>{console.error(error);process.exitCode=1;});
