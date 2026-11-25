'use strict';
const el=id=>document.getElementById(id);
let csrf='', lobby, room, roomId, selected=-1, revision=-1, dirty=false, timer;
const options=(select,items)=>select.replaceChildren(...items.map(([value,label])=>new Option(label,value)));
async function api(path,body){
 const res=await fetch(path,{cache:'no-store',method:body?'POST':'GET',headers:{'X-KPS-Context':'work',...(body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{})},body:body?JSON.stringify(body):undefined});
 const value=await res.json(); if(!res.ok)throw Error(value.detail?.message||'تعذر إتمام الطلب.');return value;
}
function action(id,run){el(id).onclick=async()=>{el(id).disabled=true;try{await run()}catch(e){el('status').textContent=e.message}finally{el(id).disabled=false}}}
function msg(t){el('status').textContent=t}
const base='/api/interviews';
async function loadLobby(){
 lobby=await api(base);el('setup').hidden=!lobby.staff;el('lobby').hidden=!!lobby.staff;
 if(lobby.staff){for(const id of ['director','ahmed'])options(el(id),[['','اختر الحساب'],...lobby.staff.map(u=>[u.id,u.display_name+' · '+u.role])]);return}
 el('close').hidden=!lobby.owner||lobby.closed;el('create').hidden=lobby.closed;
 el('supervise').hidden=lobby.closed;el('register').hidden=lobby.closed;renderGroupRooms();
 options(el('rooms'),lobby.rooms.map(r=>[r.id,r.candidate+' · كلمة المرور '+r.exam_code]));options(el('bank'),questions().map((q,n)=>[n,(n+1)+' · '+q[0]]))
}
function questions(){return room?.track==='hr'?lobby.hr_questions:lobby.questions}
function linkActions(input,candidate){
 const bar=document.createElement('div'), copy=document.createElement('button');
 copy.textContent='نسخ الرابط';
 copy.onclick=async()=>{
  try{await navigator.clipboard.writeText(input.value);msg('تم نسخ رابط '+candidate)}
  catch{input.focus();input.select();msg('حددنا الرابط. اختر نسخ من جهازك.')}
 };
 bar.append(copy);
 if(navigator.share){
  const share=document.createElement('button');share.textContent='مشاركة الرابط';
  share.onclick=async()=>{
   try{await navigator.share({title:'اختبار '+candidate,text:'رابط اختبار '+candidate,url:input.value})}
   catch(error){if(error.name!=='AbortError')msg('تعذرت المشاركة. استخدم نسخ الرابط.')}
  };
  bar.append(share)
 }
 return bar
}
function renderGroupRooms(){
 const select=el('groupLane'),previous=select.value;
 options(select,[1,2,3,4].map(n=>[n,'لجنة '+n]));select.value=previous||'1';
 const schedule=el('bookings');schedule.replaceChildren();
 for(const booking of lobby.bookings){
  const line=document.createElement('p');
  line.textContent='لجنة '+booking.exam_lane+' · '+new Date(booking.opens).toLocaleString('ar-EG',{timeZone:'Africa/Cairo'})+' · ٢٥ دقيقة';
  schedule.append(line)
 }
 const box=el('groupRooms');box.querySelectorAll('label').forEach(label=>label.remove());
 for(const r of lobby.rooms.filter(r=>!r.has_exam)){
  const label=document.createElement('label'),input=document.createElement('input');
  input.type='checkbox';input.value=r.id;label.append(input,' '+r.candidate);box.append(label)
 }
}
el('delay').onchange=()=>{el('scheduledLabel').hidden=el('delay').value!=='scheduled'};
action('refreshGroup',loadLobby);
action('register',async()=>{
 const registered=await api(base+'/rooms',{name:el('name').value,track:el('track').value});
 el('name').value='';await loadLobby();msg('تم تسجيل المرشح · كلمة المرور '+registered.exam_code)
});
action('groupStart',async()=>{
 const rooms=Array.from(el('groupRooms').querySelectorAll(':checked'),input=>Number(input.value));
 if(rooms.length<2){msg('اختر مرشحين اثنين على الأقل.');return}
 const scheduled=el('delay').value==='scheduled';
 if(scheduled&&!el('cairoStart').value){msg('حدد التاريخ والساعة بتوقيت مصر.');return}
 const when=scheduled?{cairo_start:el('cairoStart').value}:{delay:Number(el('delay').value)};
 const group=await api(base+'/groups',{rooms,lane:Number(el('groupLane').value),...when});
 const links=el('groupLinks'),heading=document.createElement('h3');
 heading.textContent='لجنة '+group.lane+' · بدء الاختبار '+new Date(group.opens).toLocaleTimeString('ar-EG',{timeZone:'Africa/Cairo'})+' بتوقيت مصر';
 links.prepend(heading);
 for(const item of group.links){
  const label=document.createElement('label'),input=document.createElement('input');
  label.textContent=item.candidate+' · كلمة المرور '+lobby.rooms.find(r=>r.candidate===item.candidate && r.id===item.room_id)?.exam_code;input.value=new URL(item.url,location.origin).href;input.readOnly=true;
  input.onclick=()=>input.select();label.append(input);const card=document.createElement('div');card.append(label,linkActions(input,item.candidate));heading.after(card)
 }
 await loadLobby()
});
function renderCard(n){
 selected=n;dirty=false;el('asked').value=n;el('card').hidden=n<0;if(n<0)return;
 const q=questions()[n],v=room.mine?.answers[n]||{},opts=q[2].split('؛');
 el('question').textContent=q[1];el('key').textContent=opts[Number(q[3])-1]+' — '+q[4];
 options(el('choice'),[['','اختر'],...[...opts,'إجابة أخرى','سُئل ولم يُجب'].map(x=>[x,x])]);
 for(const id of ['choice','outcome','answer','note','extra','score'])el(id).value=v[id]??'';
 renderFollowup();
}
function renderFollowup(){
 const q=questions()[selected], choice=el('choice').value;
 el('branch').hidden=!choice;
 if(!choice)return;
 let branch=q[6]?.[choice];
 if(!Array.isArray(branch)){
  const prompt=choice==='سُئل ولم يُجب' ? 'خذ لحظة. ما أول خطوة تبدأ بها في هذه الحالة؟' : 'اشرح المقصود بمثال من هذه الحالة. '+q[5];
  branch=[prompt,q[4]];
 }
 el('follow').textContent=branch[0];
 el('expected').textContent=branch[1];
}
el('choice').onchange=()=>{dirty=true;el('outcome').value='';renderFollowup()};
async function poll(first=false){
 const id=roomId;if(!id)return;
 const updated=await api(base+'/rooms/'+id);if(id!==roomId||(!first&&updated.version<revision))return;
 room=updated;if(first)options(el('bank'),questions().map((q,n)=>[n,(n+1)+' · '+q[0]]));const turns=room.turns,last=turns.at(-1),locked=room.closed||room.mine?.submitted;
 el('room').hidden=false;el('candidate').textContent=room.candidate;
 el('live').textContent=room.closed?'الدورة مغلقة':room.reviewing?'انتهت الأسئلة · مراجعة التقييمات':last?last.speaker+' يطرح الآن: '+questions()[last.q][1]:'بانتظار السؤال الأول';
 el('examIssue').hidden=room.closed||!room.reviewing;
 for(const id of ['ask','finish'])el(id).hidden=room.reviewing||room.closed;
 for(const id of ['save','draft','submit'])el(id).hidden=locked||(id==='submit'&&!room.reviewing);
 if(first||revision!==room.version){
  const asked=[...new Set(turns.map(t=>t.q))];options(el('asked'),asked.map(n=>[n,questions()[n][0]]));
  if(!first&&dirty){el('asked').value=selected}else{renderCard(last?.q??-1)}
  revision=room.version;
 }
 if(first){const f=room.mine?.answers[15]||{};for(const [id,key] of [['bio','bio'],['traits','traits'],['final','score'],['decision','decision'],['reason','note']])el(id).value=f[key]||''}
 el('results').replaceChildren();for(const r of room.results){const f=r.answers['15']||{},d=document.createElement('details'),s=document.createElement('summary');s.textContent=r.display_name+' · '+f.score+'/١٠ · '+f.decision;d.append(s);for(const [key,v] of Object.entries(r.answers)){const p=document.createElement('p');p.textContent=(key==='15'?'الخلاصة':questions()[key][0])+' · '+Object.values(v).filter(Boolean).join(' · ');d.append(p)}el('results').append(d)}
}
async function enter(id){
 clearTimeout(timer);el('examUrl').value='';el('examActions').replaceChildren();el('examReport').replaceChildren();roomId=id;revision=-1;dirty=false;await poll(true);
 async function tick(){if(id!==roomId)return;try{await poll()}catch(e){msg(e.message)}if(id===roomId)timer=setTimeout(tick,1800)}
 timer=setTimeout(tick,1800)
}
async function mutate(type,n=0,values={}){await api(base+'/rooms/'+roomId,{action:type,question:n,version:revision,values});await poll()}
async function save(){if(!dirty||selected<0)return;const n=selected,v={};for(const id of ['choice','outcome','answer','note','extra','score'])v[id]=el(id).value;await mutate('save',n,v);dirty=false;msg('حُفظ تقييم السؤال')}
async function finalize(type){await save();await mutate(type,15,{bio:el('bio').value,traits:el('traits').value,score:el('final').value,decision:el('decision').value,note:el('reason').value});msg('تم الحفظ')}
action('open',async()=>{await api(base+'/panel',{members:[el('director').value,el('ahmed').value]});await loadLobby()});
action('create',async()=>{await save();const r=await api(base+'/rooms',{name:el('name').value,track:el('track').value});await loadLobby();el('rooms').value=r.id;await enter(r.id)});
action('enter',async()=>{await save();if(el('rooms').value)await enter(el('rooms').value)});
action('ask',async()=>{await save();await mutate('ask',+el('bank').value)});
action('finish',async()=>{if(confirm('إنهاء طرح الأسئلة وفتح المراجعة؟')){await save();await mutate('review')}});
action('close',async()=>{if(confirm('إقفال الدورة وإيقاف التعديل لكل اللجنة؟')){await api(base+'/close',{});await loadLobby();await poll()}});
action('save',save);action('draft',()=>finalize('save'));action('submit',()=>finalize('submit'));
el('asked').onchange=async e=>{let n=+e.target.value;try{await save();renderCard(n)}catch(err){msg(err.message)}};
el('card').oninput=()=>dirty=true;
for(const id of ['score','final'])options(el(id),[['','لم يُقيّم'],...Array.from({length:11},(_,n)=>[n,n])]);
const traits=['حضور مؤثر','هادئ وواثق','متحدث واضح','قليل الكلام ودقيق','حازم ويستند إلى دليل','تردد عند الاعتراض','انفعل عند الاختلاف','استوضح قبل الحكم','تسرع ثم صحّح'];options(el('trait'),traits.map(x=>[x,x]));
action('append',()=>{el('traits').value+=[el('traits').value?'، ':'',el('trait').value].join('')});
api('/api/session').then(s=>{csrf=s.csrf_token;return loadLobby()}).catch(e=>msg(e.message));

action('examIssue',async()=>{const id=roomId;const r=await api(base+'/rooms/'+id+'/exam',{});if(roomId===id){el('examUrl').value=new URL(r.url,location.origin).href;el('examActions').replaceChildren(linkActions(el('examUrl'),room.candidate))}});
action('examResult',async()=>{const id=roomId,r=await api(base+'/rooms/'+id+'/exam');if(roomId!==id)return;const target=el('examReport');target.textContent=r.status;if(r.result){target.append(' · '+r.result.points+' / '+r.result.maximum);for(const section of r.result.sections){const line=document.createElement('p');line.textContent=section.title+' · '+section.points+' / '+section.maximum;target.append(line)}for(const d of r.result.details){const line=document.createElement('p');line.textContent=d.label+' — الجواب: '+(d.answer||'فارغ')+'، الصحيح: '+d.expected+'، الدرجة: '+d.earned+'/'+d.maximum+(d.explanation?' · '+d.explanation:'');target.append(line)}}});

async function showBatch(){
 const r=await api(base+'/batch/results'),target=el('batchRows');
 target.textContent=r.opens?'موعد الدفعة: '+new Date(r.opens).toLocaleString('ar-EG',{timeZone:'Africa/Cairo'})+' بتوقيت مصر':'نتائج الاختبارات الفردية';
 for(const x of r.results){const row=document.createElement('p');row.textContent=[x.rank?'المركز '+x.rank:'',x.candidate,x.status,x.score==null?'':x.score+' / '+x.maximum].filter(Boolean).join(' · ');target.append(row)}
}
action('batchShow',showBatch);
if(location.hash==='#results')window.addEventListener('load',()=>{el('batch').open=true;showBatch().catch(e=>msg(e.message))});
