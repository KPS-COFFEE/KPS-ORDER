'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/customer.js','utf8');
const sending=source.slice(source.indexOf('  async function sendMessage(event)'),source.indexOf('  async function loadBranchCatalog()'));
const polling=source.split('\n').find(line=>line.startsWith('  async function pollChat()'));
const busy=source.split('\n').find(line=>line.startsWith('  async function busy('));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 const requests=[],errors=[],removed=[],released=[],cleared=[],scheduled=[];
 const fields=new Map(),storage=new Map([['draft:A','Original message']]);
 const field=id=>{if(!fields.has(id))fields.set(id,{id,value:id==='chat-text'?'Original message':'',open:true,disabled:false,dataset:{},textContent:'Send',setAttribute(){},removeAttribute(){}});return fields.get(id);};
 let readResolve;
 const image={name:'evidence.png',type:'image/png',size:42,lastModified:1};
 const context={state:{conversation:{id:'A'},session:{user:{id:'customer'}},chatSequence:0,chatTimer:1},chatSelectedImage:image,
  $:field,t:key=>key,localizedBindings:new Map(),setText(){},renderQuote(){},encodeURIComponent,
  chatDraftKey:()=>`draft:${context.state.conversation?.id}`,
  sessionStorage:{getItem:key=>storage.get(key)??null,removeItem:key=>{removed.push(key);storage.delete(key);}},
  readFile:()=>new Promise(resolve=>readResolve=resolve),
  stableKey:(scope,payload)=>({value:scope+JSON.stringify(payload)}),releaseKey:key=>released.push(key),
  api:(path,options)=>new Promise((resolve,reject)=>requests.push({path,options,resolve,reject})),
  clearChatImage:()=>{context.chatSelectedImage=null;cleared.push('image');},
  showError:(id,text)=>errors.push({id,text}),errorMessage:error=>error.message,
  clearTimeout:id=>cleared.push(id),setTimeout:(fn,ms)=>{scheduled.push({fn,ms});return 9;}
 };
 vm.createContext(context);vm.runInContext(busy+'\n'+sending+'\n'+polling+'\nthis.send=sendMessage;this.poll=pollChat;',context);
 // Sending only needs polling as a notification; polling itself is tested separately below.
 context.pollChat=async()=>scheduled.push('poll');
 return {context,requests,errors,removed,released,cleared,scheduled,storage,field,image,
  read:()=>readResolve('base64'),send:()=>context.send({preventDefault(){}})};
}
test('file reading and upload stay on original order after conversation switch',async()=>{
 const h=harness(),pending=h.send();h.context.state.conversation={id:'B'};h.field('chat-text').value='New order draft';h.storage.set('draft:B','New order draft');h.context.chatSelectedImage={name:'new.png'};h.read();await tick();
 assert.equal(h.requests[0].path,'/api/conversations/A/attachments');h.requests[0].resolve({attachment:{id:'evidence-A'}});await tick();
 assert.equal(h.requests[1].path,'/api/conversations/A/messages');assert.deepEqual(JSON.parse(JSON.stringify(h.requests[1].options.data)),{text:'Original message',attachment_id:'evidence-A'});
 h.requests[1].resolve({});await pending;
 assert.equal(h.field('chat-text').value,'New order draft');assert.equal(h.context.chatSelectedImage.name,'new.png');assert.equal(h.storage.get('draft:B'),'New order draft');assert.deepEqual(h.removed,['draft:A']);assert.deepEqual(h.errors,[]);assert.deepEqual(h.scheduled,[]);
});
test('new text and image selected during same-conversation send are preserved',async()=>{
 const h=harness(),pending=h.send();h.read();await tick();h.requests[0].resolve({id:'old-image'});await tick();
 h.field('chat-text').value='Follow-up';h.storage.set('draft:A','Follow-up');const next={name:'next.png'};h.context.chatSelectedImage=next;
 h.requests[1].resolve({});await pending;assert.equal(h.field('chat-text').value,'Follow-up');assert.equal(h.storage.get('draft:A'),'Follow-up');assert.equal(h.context.chatSelectedImage,next);assert.deepEqual(h.removed,[]);
});
test('confirmed original send clears only its submitted draft and image',async()=>{
 const h=harness(),pending=h.send();h.read();await tick();h.requests[0].resolve({id:'evidence'});await tick();h.requests[1].resolve({});await pending;
 assert.equal(h.field('chat-text').value,'');assert.equal(h.context.chatSelectedImage,null);assert.equal(h.storage.has('draft:A'),false);assert.deepEqual(h.scheduled,['poll']);assert.equal(h.field('send-message').disabled,false);
});
test('repeated press while original request is pending produces one message',async()=>{
 const h=harness();h.context.chatSelectedImage=null;const first=h.send(),second=h.send();assert.equal(h.requests.length,1);h.requests[0].resolve({});await Promise.all([first,second]);assert.equal(h.requests.length,1);
});
test('failed upload for old order does not overwrite newly opened chat error state',async()=>{
 const h=harness(),pending=h.send();h.read();await tick();h.context.state.conversation={id:'B'};h.requests[0].reject(new Error('Upload failed'));await pending;assert.deepEqual(h.errors,[]);assert.equal(h.storage.get('draft:A'),'Original message');assert.equal(h.requests.length,1);
});
test('same conversation upload failure retains draft and shows recoverable error',async()=>{
 const h=harness(),pending=h.send();h.read();await tick();h.requests[0].reject(new Error('Upload failed'));await pending;assert.equal(h.errors.at(-1).text,'Upload failed');assert.equal(h.field('chat-text').value,'Original message');assert.equal(h.context.chatSelectedImage,h.image);assert.equal(h.field('send-message').disabled,false);
});
for(const fail of [false,true])test(`stale poll ${fail?'failure':'success'} cannot cancel current conversation timer`,async()=>{
 const h=harness(),pending=h.context.poll();h.context.state.conversation={id:'B'};h.context.state.chatTimer=77;
 if(fail)h.requests[0].reject(new Error('Old request failed'));else h.requests[0].resolve({messages:[]});await pending;
 assert.deepEqual(h.cleared,[]);assert.deepEqual(h.scheduled,[]);assert.deepEqual(h.errors,[]);assert.equal(h.context.state.chatTimer,77);
});
