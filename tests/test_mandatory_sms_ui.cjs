// Execute the real UI handlers with a small deterministic DOM surface.
// This supplements the real Chromium cases; it is not browser certification.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('public/ops.js','utf8');
const html=fs.readFileSync('public/ops.html','utf8');
assert.match(html,/class="legacy-login"[^>]*hidden/);
assert.match(source,/staffChannel:'sms', workSmsRequired:true/);
const labels=source.slice(source.indexOf('  function applyStaffChannelLabels()'),source.indexOf('  function staffRequestMessage()'));
const handler=source.split('\n').find(line=>line.includes("$('#login-form').addEventListener('submit'"));
assert.ok(handler);
const sensitive=source.slice(source.indexOf('  async function ownerStepUp('),source.indexOf('  function applyLanguage()'));
(async()=>{
let cases=0;
for(const lang of ['ar','en']){
 const fields=new Map();let submit;const calls=[];
 const label={hidden:false};
 const get=s=>{if(!fields.has(s))fields.set(s,{hidden:false,value:'',textContent:'',closest:()=>label});return fields.get(s)};
 get('#login-form').addEventListener=(name,fn)=>{assert.equal(name,'submit');submit=fn};
 const state={staffChannel:'sms',workSmsRequired:true,staffChallenge:null,user:null};
 const context={state,$:get,tr:(ar,en)=>lang==='ar'?ar:en,FormData:class {constructor(){return {get:k=>({username:'synthetic',password:'synthetic-password'})[k]}}},api:async(...args)=>{calls.push(args);throw Error('Unexpected password HTTP')},showApp:()=>{throw Error('Password must not show app')}};
 vm.createContext(context);vm.runInContext(labels+'\n'+handler,context);
 context.applyStaffChannelLabels();
 assert.equal(get('.legacy-login').hidden,true);
 assert.equal(get('#staff-code-verify-form input[name=code]').maxLength,4);
 assert.match(get('#staff-code-label').textContent,/4/);
 assert.match(get('#staff-code-note').textContent,/SMS/);
 await submit({preventDefault(){},currentTarget:{reset(){}}});
 assert.equal(calls.length,0);
 assert.match(get('#login-error').textContent,/SMS/);
 assert.equal(get('#login-submit').disabled,false);
 cases+=2;
 let dialog;const elements={form:{append(){}},'[data-cancel]':{},'[type=submit]':{},'[name=code]':{value:'0123',focus(){}},'.form-error':{}};
 const requests=[];
 const ownerContext={state:{user:{role:'owner',employee_sms_email_retired:true}},tr:(a,b)=>lang==='ar'?a:b,esc:String,
   mutation:()=>{throw Error('Sensitive password fallback forbidden')},
   api:async(path,opts)=>{requests.push([path,opts]);return path.endsWith('/request')?{challenge_id:'owner_sms_synthetic'}:{verified:true}},
   document:{createElement:tag=>tag==='button'?{}:(dialog={isConnected:false,addEventListener(){},querySelector:s=>elements[s],showModal(){},close(){},remove(){this.isConnected=false;}}),body:{append(node){node.isConnected=true;}}}};
 vm.createContext(ownerContext);vm.runInContext(sensitive,ownerContext);
 const proof=ownerContext.ownerStepUp('must-never-be-sent');
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(dialog.innerHTML,/maxlength="4"/);
 assert.doesNotMatch(dialog.innerHTML,/name="password"/);
 await elements.form.onsubmit({preventDefault(){}});
 assert.equal((await proof).verified,true);
 assert.deepEqual(requests.map(x=>x[0]),['/api/identity/owner/sms/request','/api/identity/owner/sms/verify']);
 assert.equal(requests[1][1].body.code,'0123');
 assert.equal(JSON.stringify(requests).includes('must-never-be-sent'),false);
 cases++;
 ownerContext.state.user.owner_session_approved=true;
 const before=requests.length;
 assert.equal((await ownerContext.ownerStepUp(undefined,true)).verified,true);
 assert.equal(requests.length,before,'Approved owner session sends no extra code');
 cases++;
}
console.log(`PASS ${cases} UI handler scenarios: AR/EN mandatory SMS labels, hidden/forced password form, sensitive SMS-only flow.`);
})().catch(error=>{console.error(error);process.exit(1)});
