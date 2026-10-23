'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {webcrypto,createHash}=require('node:crypto');
const source=fs.readFileSync('public/ops.js','utf8');
const api=source.slice(source.indexOf('  async function api(path,'),source.indexOf('  async function mutation('));
const user='SYNTHETIC-shared-manager',path='/api/shifts/SYNTHETIC/cash-event';
const body={amount_halalas:1000,reference:'SYNTHETIC',reason:'Synthetic counted cash',confirmed:true};
// This is the production 4e4d781 signature and SHA-256 storage format, before
// the multi-brand release. Store no raw body or phone in the browser key.
const legacySignature=`${user}:POST:${path}:${JSON.stringify(body)}`;
const legacyStorage=`kps_ops_pending_v1:${user}:`+createHash('sha256').update(legacySignature).digest('hex');
const saved='SYNTHETIC-committed-key';
async function scenario(brand,fail=false){
  const storage=new Map([[legacyStorage,saved]]),requests=[];
  const ctx={state:{user:{id:user,brand_id:brand},csrf:'synthetic-csrf',requestKeys:new Map()},morningMode:false,
    operationsBrand:brand,TextEncoder,crypto:{subtle:webcrypto.subtle,randomUUID:()=>brand+'-new-synthetic-key'},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    tr:(ar,en)=>en,apiError:()=>'',location:{},
    fetch:async(url,options)=>{requests.push(options);if(fail)throw new Error('Synthetic lost response');return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({saved:true})};}};
  vm.createContext(ctx);vm.runInContext(api,ctx);
  if(fail)await assert.rejects(()=>ctx.api(path,{method:'POST',body}),/not confirmed/);
  else await ctx.api(path,{method:'POST',body});
  return {storage,requests};
}
(async()=>{
  let result=await scenario('kps');assert.equal(result.requests[0].headers['Idempotency-Key'],saved);assert.equal(result.storage.size,0);
  result=await scenario('kps',true);assert.equal(result.requests[0].headers['Idempotency-Key'],saved);assert.equal(result.storage.get(legacyStorage),saved);
  result=await scenario('enbishan');assert.notEqual(result.requests[0].headers['Idempotency-Key'],saved);assert.equal(result.storage.get(legacyStorage),saved,'ENBISHAN cannot consume or remove a KPS pending replay');
  assert.equal(result.requests[0].headers['X-Operations-Brand'],'enbishan');
  console.log('PASS: actual staff API reuses production KPS retry keys after upgrade; failed responses retain them and ENBISHAN remains separate.');
})().catch(error=>{console.error(error);process.exitCode=1;});
