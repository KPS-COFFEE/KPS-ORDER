'use strict';
const get=id=>document.getElementById(id);
const token=location.hash.slice(1);
let candidateName='';
let rev=0,end=0,active=false,busy=false,timer,changes={},submitting=false;
async function request(action,answers={}){
 const response=await fetch('/api/candidate-exam',{method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json','X-Exam-Token':token},body:JSON.stringify({action,answers,name:candidateName,password:get('examPassword').value,revision:rev})});
 const r=await response.json();
 if(!response.ok)throw Error(r.detail?.message||'تعذر الاتصال.');
 return r;
}
function ended(){active=false;get('intro').hidden=true;get('work').hidden=true;get('message').textContent='انتهى الاختبار. تستطيع اللجنة الآن مراجعة الإجابات المحفوظة.'}
function node(tag,value){const n=document.createElement(tag);n.textContent=value;return n}
function show(r){
 get('identify').hidden=r.status!=='identify';
 if(r.status==='identify'){get('fullName').focus();return}
 get('message').textContent='';
 get('candidate').textContent=r.candidate;
 if(r.status==='ended'){ended();return}
 if(r.status==='waiting'){
  const date=new Date(r.opens).toLocaleString('ar-EG',{timeZone:'Africa/Cairo'});
  get('message').textContent='يبدأ الاختبار تلقائيًا '+date+' بتوقيت مصر. اترك الصفحة مفتوحة.';
  setTimeout(()=>request('view').then(show).catch(e=>get('message').textContent=e.message+' حدّث الصفحة.'),Math.max(100,Math.min(30000,r.seconds*1000)));return
 }
 if(r.status==='ready'){get('intro').hidden=false;return}
 get('message').textContent='';active=true;rev=r.revision;end=performance.now()+1000*r.seconds;get('intro').hidden=true;get('work').hidden=false;get('tasks').replaceChildren();
 if(r.step){showStep(r.step);tick();return}
 for(const task of r.paper){
  const card=node('article','');card.append(node('h2',task.title),node('p',task.prompt));
  if(task.columns){const table=node('table','');
  for(const [i,row] of [task.columns,...task.rows].entries()){const tr=node('tr','');for(const cell of row)tr.append(node(i?'td':'th',cell));table.append(tr)}
  card.append(table);}
  for(const [key,label] of task.fields){const l=node('label',label),input=node(task.choices?'select':'input','');if(task.choices){input.append(new Option('اختر الإجابة الأصح',''));task.choices[key].forEach((option,i)=>input.append(new Option(option,String(i+1))))}else{input.inputMode='numeric';input.maxLength=20;}input.value=r.answers[key]||'';
   input.oninput=()=>{changes[key]=input.value;get('message').textContent='جارٍ الحفظ…';clearTimeout(timer);timer=setTimeout(flush,250)};l.append(input);card.append(l)}
  get('tasks').append(card)
 }
 tick()
}
function showStep(step){
 const card=node('article',''), group=node('fieldset',''), next=get('send');
 const number=n=>n.toLocaleString('ar-EG');
 card.append(node('h2','القسم '+number(step.section)+' من '+number(step.sections)+' · '+step.title),
             node('p',step.prompt),node('p','السؤال '+number(step.number)+' من '+number(step.total)));
 group.append(node('legend',step.question));
 let selected='';
 next.disabled=true;
 next.textContent=step.number===step.total?'اعتماد الإجابة وتسليم الاختبار':'اعتماد الإجابة والتالي';
 step.choices.forEach((choice,i)=>{
  const label=node('label',''), radio=document.createElement('input');
  radio.type='radio';radio.name=step.key;radio.style.width='auto';
  radio.onchange=()=>{selected=String(i+1);next.disabled=false};
  label.append(radio,document.createTextNode(' '+choice));group.append(label);
 });
 card.append(group);get('tasks').append(card);
 next.onclick=async()=>{
  if(!selected||next.disabled)return;
  next.disabled=true;group.disabled=true;
  try{show(await request('next',{[step.key]:selected}))}
  catch(error){get('message').textContent=error.message;next.disabled=false;group.disabled=false}
 };
}
async function flush(){
 if(!active||busy)return;
 const batch=changes;changes={};busy=true;
 try{const r=await request(submitting?'submit':'save',batch);if(r.status==='ended'){ended();return}rev=r.revision;get('message').textContent='حُفظت الإجابات.'}
 catch(e){changes={...batch,...changes};submitting=false;get('send').disabled=false;get('tasks').inert=false;get('message').textContent=e.message}
 finally{busy=false;if(active&&(Object.keys(changes).length||submitting)){clearTimeout(timer);timer=setTimeout(flush,800)}}
}
function tick(){if(!active)return;const left=Math.max(0,Math.ceil((end-performance.now())/1000));get('clock').textContent='الوقت المتبقي '+Math.floor(left/60)+':'+String(left%60).padStart(2,'0');if(!left)ended()}
setInterval(tick,250);
get('start').onclick=async()=>{get('start').disabled=true;try{show(await request('start'))}catch(e){get('message').textContent=e.message}finally{get('start').disabled=false}};
get('send').onclick=()=>{if(confirm('تسليم الاختبار الآن؟')){submitting=true;get('send').disabled=true;get('tasks').inert=true;flush()}};
get('identify').onsubmit=async event=>{
 event.preventDefault();
 const submit=event.currentTarget.querySelector('button');
 candidateName=get('fullName').value.trim();
 submit.disabled=true;
 try{show(await request('view'))}
 catch(error){get('message').textContent=error.message}
 finally{submit.disabled=false}
};
request('view').then(show).catch(e=>get('message').textContent=e.message);
