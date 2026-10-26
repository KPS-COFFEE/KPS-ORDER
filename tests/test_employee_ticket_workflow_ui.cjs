const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  for(const lang of ['ar','en']){
    let rendered='',submit,mutations=[],actions=[];
    const host={isConnected:true,querySelector:()=>null};
    const context={window:{},document:{querySelector:s=>s==='#content'?host:null,querySelectorAll:()=>actions}};vm.createContext(context);vm.runInContext(fs.readFileSync('public/assets/employee-tickets.js','utf8'),context);
    const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const workflow={version:3,status:'responded',assignment_recorded:true,responsible_user_id:'owner',responsible_department:'owner',dispatcher_id:'owner',assignee_id:'manager',department:'delivery',next_recipient_id:'owner',question:'Attach <receipt>',evidence_required:'Actual source record',due_at:'2026-10-26T12:00:00Z',overdue:true,names:{owner:'Owner <one>',manager:'Manager'},fallback_users:[{id:'fallback',name:'Authorized fallback'}],history:[{actor_name:'Manager',created_at:'2026-10-26T10:00:00Z',details:{action:'respond',note:'Actual <evidence>',evidence:['R001']}}],actions:['assign','return','complete']};
    const api=async path=>path.endsWith('/options')?{types:[],factual_workflow_enabled:true}:path.endsWith('/followup')?{workflow,assignees:[{id:'manager',name:'Manager <two>',department:'delivery'}]}:{tickets:[]};
    actions=[{dataset:{etFollowupAction:'assign'}},{dataset:{etFollowupAction:'return'}}];
    await context.window.KPSEmployeeTickets.open({api,mutation:async(path,body)=>mutations.push({path,body:JSON.parse(JSON.stringify(body))}),tr,esc,heading(){},modal:(_title,html,fn)=>{rendered=html;submit=fn;},closeModal(){},user:{id:'owner',role:'owner'},lang,active:()=>true,linkedOnly:true,initialTicket:'ticket1'});
    assert(rendered.includes('Owner &lt;one&gt;')&&rendered.includes('Attach &lt;receipt&gt;')&&!rendered.includes('Attach <receipt>'));
    assert(rendered.includes('Authorized fallback')&&rendered.includes(lang==='ar'?'متأخر':'Overdue'));
    assert(rendered.includes(lang==='ar'?'لا تمنح صلاحية':'grants no permission'));
    actions[0].onclick();assert(rendered.includes('Manager &lt;two&gt;'));
    await submit(new Map([['assignee_id','manager'],['due_hours','12'],['note','Please attach the actual receipt'],['evidence_required','Recorded cash custody reference']]));
    assert.deepEqual(mutations,[{path:'/api/employee-tickets/ticket1/followup',body:{expected_version:3,action:'assign',note:'Please attach the actual receipt',assignee_id:'manager',department:'delivery',due_hours:12,evidence_required:'Recorded cash custody reference'}}]);
    actions[1].onclick();await submit(new Map([['due_hours','2'],['note','Correct the receipt timestamp'],['evidence_required','Original receipt timestamp']]));
    assert.equal(mutations[1].body.action,'return');assert.equal(mutations[1].body.expected_version,3);
    await assert.rejects(()=>context.window.KPSEmployeeTickets.open({api:async()=>({types:[],factual_workflow_enabled:false}),mutation(){throw new Error('must not mutate');},tr,esc,heading(){},modal(){throw new Error('must not open handoff UI');},closeModal(){},user:{id:'owner',role:'owner'},lang,active:()=>true,linkedOnly:true,initialTicket:'ticket1'}));
  }
  console.log('PASS: AR/EN actual responsibility, deadline, scoped reassignment, exact return correction, history and source independence');
})().catch(e=>{console.error(e);process.exitCode=1;});
