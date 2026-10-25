/* A visible composer must intercept submit while read-receipt I/O is pending. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2]||'public/ops.js','utf8');
const fn=source.split('\n').find(line=>line.startsWith('  async function conversation(id,after=0)'));
assert.ok(fn,'conversation entry point exists');
(async()=>{
  let releaseRead,readStarted=false,submitHandler,prevented=false,sent=0;
  const readPending=new Promise(resolve=>{releaseRead=resolve;});
  const root={dataset:{},closest:()=>({classList:{remove(){}}}),innerHTML:''};
  const box={scrollTop:0,scrollHeight:10};
  const button={disabled:false,isConnected:true};
  const error={textContent:''};
  const form={isConnected:true,reset(){},addEventListener(event,handler){if(event==='submit')submitHandler=handler;}};
  const context={conversationVersion:0,state:{page:'chat',conversations:[],chatHistory:new Map(),user:{id:'staff'}},
    document:{querySelectorAll:()=>[]},chatSurfaceActive:()=>true,tr:ar=>ar,esc:String,
    conversationLabel:()=> 'Synthetic conversation',messageMarkup:m=>m.text,formEnd:()=>'<button type="submit">Send</button>',
    $:selector=>selector==='#chat-detail'?root:selector==='#messages'?box:selector==='#message-form'?form:selector==='button[type=submit]'?button:error,
    api:async path=>{if(path.endsWith('/read')){readStarted=true;return readPending;}return {conversation:{route:'privacy'},messages:[{id:'message-1',seq:1,text:'Initial message'}]};},
    FormData:class {get(key){return key==='text'?'Reply during pending read':null;}},
    mutation:async(path,body)=>{assert.ok(path.endsWith('/messages'));assert.equal(body.text,'Reply during pending read');sent++;},
    pollConversation:async()=>{},refreshOperationalAlerts:async()=>{}};
  vm.createContext(context);vm.runInContext(fn+'\nthis.openConversation=conversation;',context);
  const opening=context.openConversation('conversation-1');
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(root.innerHTML.includes('id="message-form"'),'composer rendered');
  assert.ok(readStarted,'read receipt is in flight');
  assert.equal(typeof submitHandler,'function','visible composer must already prevent native form navigation');
  await submitHandler({preventDefault(){prevented=true;},currentTarget:form});
  assert.ok(prevented);assert.equal(sent,1);assert.equal(button.disabled,false);
  releaseRead({through_seq:1});await opening;
  console.log('PASS: pending read receipt cannot leave a visible composer without submit handling.');
})().catch(error=>{console.error(error);process.exit(1);});
