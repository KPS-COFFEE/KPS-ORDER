const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
const between=(a,b)=>{const start=source.indexOf(a),end=source.indexOf(b,start);assert.ok(start>=0&&end>start);return source.slice(start,end);};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const basics=()=>({URLSearchParams,state:{user:{id:'delegate',role:'admin',brand_id:'enbishan',effective_permissions:['orders_view','chat_view']},page:'orders',filters:{period:'all',sort:'newest'},branches:[],drivers:[]},tr:(_ar,en)=>en,esc,num:String,datetime:String,statusName:String,branchLabel:()=>'',empty:String,field:(label,name,value)=>`<label>${label}<input name="${name}" value="${esc(value)}"></label>`,selectOptions:()=>'',serviceName:String});
test('supervisor navigation preserves existing configuration and opens orders plus conversations',()=>{
 const nav={innerHTML:'',insertAdjacentHTML(_where,html){this.innerHTML+=html;}},ctx={...basics(),document:{activeElement:null},$:()=>nav,isAdmin:()=>true,isManager:()=>false};
 ctx.can=p=>ctx.state.user.effective_permissions.includes(p);ctx.canConfigure=()=>ctx.can('products_manage');ctx.state.user.effective_permissions.push('products_manage');vm.createContext(ctx);
 vm.runInContext(between('  const pagePermissions=','  const actionPermissions=')+'\n'+source.split('\n').find(l=>l.startsWith('  function navigation()')),ctx);
 ctx.navigation();assert.match(nav.innerHTML,/data-page="orders"/);assert.match(nav.innerHTML,/Orders &amp; invoices/);assert.match(nav.innerHTML,/data-page="chat"/);assert.match(nav.innerHTML,/data-page="admin"/);assert.doesNotMatch(nav.innerHTML,/data-page="dashboard"/);
});
test('invoice search explicitly supports all history and clears contradictory date bounds',()=>{
 const ctx={...basics(),FormData:class{constructor(form){return new Map(Object.entries(form));}}};vm.createContext(ctx);
 vm.runInContext(between('  function filterForm()','  async function recovery(')+between('  function orderFilters(','  function duration(')+between('  function validatedFilters(','  document.addEventListener(\'submit\',async e=>{if(e.target.id!==\'filters-form\')'),ctx);
 const html=ctx.filterForm();assert.match(html,/value="all" selected/);assert.match(html,/Order or invoice number/);assert.match(html,/All history/);
 const filters=ctx.validatedFilters({period:'all',start:'2020-01-01',end:'2020-01-02',search:'INV-OLD'});assert.equal(filters.start,'');assert.equal(filters.end,'');assert.equal(filters.search,'INV-OLD');assert.equal(filters.period,'all');
});
test('opening order conversations selects a scoped read-only history filter without creating a conversation',async()=>{
 const reads=[],ctx={...basics(),closeModal(){},loadPage:async()=>{},api:async path=>{reads.push(path);return {order:{brand_id:'enbishan'}};},mutation:async()=>assert.fail('Viewing history must not POST')};vm.createContext(ctx);vm.runInContext(between('  function chatQuery(','  async function chat()'),ctx);
 await ctx.openOrderConversations('historical/order','enbishan');assert.equal(reads.length,0);assert.equal(ctx.state.page,'chat');assert.equal(ctx.state.chatFilters.brand,'enbishan');assert.equal(ctx.state.chatFilters.order_id,'historical/order');assert.match(ctx.chatQuery(),/order_id=historical%2Forder/);assert.doesNotMatch(ctx.chatQuery(),/period|today/);
 ctx.state.chatFilters.offset=50;ctx.state.chatNextOffset=100;assert.match(ctx.chatPaging(),/data-offset="0"/);assert.match(ctx.chatPaging(),/data-offset="100"/);
});
test('read-only admin displays an archived receipt through GET without triggering fiscal issuance',async()=>{
 const reads=[],rendered=[],win={opener:{},document:{write:html=>rendered.push(html),close(){}},close(){assert.fail('Unexpected receipt failure');}};
 const ctx={...basics(),window:{open:()=>win},can:p=>p==='orders_view',api:async path=>{reads.push(path);return {order:{id:'old',issued_receipt:{id:'old',fiscal_document:{number:'OLD-INVOICE'}}}};},mutation:async()=>assert.fail('Read must never issue a new fiscal document'),normalizeOrder:o=>o,receiptHTML:o=>JSON.stringify(o),bindReceiptWindow(){},complimentaryName:()=>''};vm.createContext(ctx);vm.runInContext(between('  async function printOrder(','  async function reportView('),ctx);
 await ctx.printOrder('old');assert.deepEqual(reads,['/api/orders/old']);assert.match(rendered[0],/OLD-INVOICE/);assert.equal(win.opener,null);
});
test('read-only conversation has messages and transfers but no reply, claim or status mutations',async()=>{
 const root={innerHTML:'',dataset:{},closest:()=>({classList:{remove(){}}})},messages={scrollTop:0,scrollHeight:10};
 const ctx={...basics(),conversationVersion:0,api:async()=>({conversation:{id:'c',order_no:219,status:'open',route:'privacy',can_reply:false,can_manage:false},messages:[],transfers:[{to_route:'delivery',reason:'Original routing note',created_at:'2026-10-30'}]}),$:selector=>selector==='#chat-detail'?root:selector==='#messages'?messages:null,document:{querySelectorAll:()=>[]},can:()=>true,conversationLabel:c=>c.order_no,messageMarkup:()=>'',table:(_head,rows)=>rows.flat().join('|'),formEnd:()=>'',safeHref:()=>false,mutation:async()=>assert.fail('Viewing must not send'),refreshOperationalAlerts:async()=>{}};
 ctx.state.page='chat';ctx.state.chatHistory=new Map();ctx.state.conversations=[];vm.createContext(ctx);vm.runInContext(between('  function chatSurfaceActive(','  async function editDriverTerminal('),ctx);
 await ctx.conversation('c');assert.match(root.innerHTML,/Original routing note/);assert.match(root.innerHTML,/assigned response team/);assert.doesNotMatch(root.innerHTML,/id="message-form"|claim-chat|resolve-chat|transfer-chat/);
});
test('history groups Riyadh dates and retains original reason, correction, actor and responsible manager',()=>{
 const ctx={...basics(),money:String};ctx.state.lang='en';vm.createContext(ctx);vm.runInContext(between('  function interventionReasonLabel(','  function orderBrandBadge('),ctx);
 const html=ctx.orderHistoryTimeline([{created_at:'2026-10-29T21:30:00Z',actor_name:'Supervisor <script>',event_type:'intervention.returned',details:{original_reason_code:'late_complaint',reason_code:'no_answer',target_manager_name:'Manager A',note:'Correct the recorded contact result'}},{created_at:'2026-10-30T09:00:00Z',actor_name:'Manager A',event_type:'intervention.resubmitted',details:{note:'Evidence completed'}}]);
 assert.equal((html.match(/class="order-history-day"/g)||[]).length,1);assert.match(html,/Original reason: Delay complaint/);assert.match(html,/Current reason: Customer not answering/);assert.match(html,/Completion assigned to: Manager A/);assert.match(html,/Supervisor &lt;script&gt;/);assert.match(html,/Evidence completed/);
});
test('preparation navigation can use the actual target while source invoice identity stays fixed',()=>{
 const ctx={};vm.createContext(ctx);vm.runInContext(source.split('\n').find(l=>l.startsWith('  function normalizeOrder(')),ctx);
 const order={id:'o',branch_id:'source',branch_name:'Source current name',snapshot:{branch_name:'Source invoice name',branch_location:{lat:1,lng:2}},preparation_branch:{id:'target',name:'Preparation target',location:{lat:3,lng:4},routed:true}};const normalized=ctx.normalizeOrder(order);assert.equal(normalized.branch_id,'source');assert.equal(normalized.branch_name,'Source invoice name');assert.equal(normalized.branch_location.lat,3);assert.equal(normalized.snapshot.branch_location.lat,1);
});
test('delivery manager can reach assigned historical processing work without after-print authority',()=>{
 const nav={innerHTML:'',insertAdjacentHTML(_where,html){this.innerHTML+=html;}},ctx={...basics(),document:{activeElement:null},$:()=>nav,isAdmin:()=>false,isManager:()=>true,canConfigure:()=>false};ctx.state.user.role='delivery_manager';ctx.state.user.effective_permissions=['orders_view'];ctx.can=p=>ctx.state.user.effective_permissions.includes(p);vm.createContext(ctx);vm.runInContext(between('  const pagePermissions=','  const actionPermissions=')+'\nthis.pageAllowed=canPage;\n'+source.split('\n').find(l=>l.startsWith('  function navigation()')),ctx);
 assert.equal(ctx.pageAllowed('interventionInbox'),true);ctx.navigation();assert.match(nav.innerHTML,/data-page="interventionInbox"/);assert.match(nav.innerHTML,/Processing requests/);ctx.state.user.effective_permissions=[];assert.equal(ctx.pageAllowed('interventionInbox'),false);
});
