const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,mutations=[],buttons=[],add=null;
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const data={order_version:3,can_request:false,assignees:[],clarifications:[{id:'q1',version:2,question:'<script>missing evidence</script>',status:'accepted',due_at:'2026-10-25T15:00:00Z',overdue:true,responsible_user_id:'manager',responsible_role:'delivery_manager',next_action:'manager_response',required_correction:'Attach <receipt>',authorized_fallback_role:'owner',requested_by:'accountant',requester_name:'Actual accountant',assignee_id:'manager',assignee_name:'Actual manager',actions:['respond'],history:[{action:'accepted',actor_name:'Actual manager',created_at:'2026-10-25T10:00:00Z',note:'Accepted actual handoff',evidence:[]}]}]};
    const context={window:{},document:{documentElement:{lang},querySelectorAll:()=>buttons,querySelector:()=>add}};vm.createContext(context);
    vm.runInContext(fs.readFileSync('public/assets/financial-clarifications.js','utf8'),context);
    const ctx={oid:'order',api:async()=>data,mutation:async(path,body)=>{mutations.push({path,body:JSON.parse(JSON.stringify(body))});data.clarifications[0].status='responded';data.clarifications[0].actions=[];return {};},tr:(ar,en)=>lang==='en'?en:ar,esc,modal:(title,html,fn)=>{rendered=html;submit=fn;},closeModal:()=>{}};
    buttons=[{dataset:{id:'q1',fcqAction:'respond'}}];
    await context.window.KPSFinancialClarifications.open(ctx);
    assert(rendered.includes('Actual manager')&&rendered.includes('Actual accountant'));
    assert(rendered.includes('&lt;script&gt;')&&!rendered.includes('<script>'));
    assert(rendered.includes('data-fcq-action="respond"'));
    assert(rendered.includes('Attach &lt;receipt&gt;')&&!rendered.includes('Attach <receipt>'));
    assert(rendered.includes(lang==='ar'?'متأخر':'Overdue'));
    assert(rendered.includes(lang==='ar'?'أرسل الرد والإثبات':'send the response and evidence'));
    assert(rendered.includes(lang==='ar'?'عند غياب المسؤول':'When the responsible employee is unavailable'));
    assert(!rendered.includes('data-fcq-action="resolve"'));
    buttons[0].onclick();
    await submit(new Map([['note','These are the actual facts'],['evidence','Receipt 001\n\nManager statement 002']]));
    assert.deepEqual(mutations,[{path:'/api/financial-clarifications/q1/decision',body:{expected_version:2,action:'respond',note:'These are the actual facts',evidence:['Receipt 001','Manager statement 002']}}]);
    assert(rendered.includes(lang==='ar'?'بانتظار مراجعة المحاسب':'awaits the requesting accountant'));
    assert(!rendered.includes('data-fcq-action="resolve"'));
  }
  console.log('PASS: AR/EN recorded assignees, scoped server actions, versioned evidence response, accounting review pending and escaped context');
})().catch(error=>{console.error(error);process.exitCode=1;});
