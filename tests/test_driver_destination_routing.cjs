const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {create}=require('../public/assets/central-operations.js');
const source=fs.readFileSync('public/ops.js','utf8');
const panel=source.slice(source.indexOf('  async function renderDriverDestinationPanel('),source.indexOf('  async function tasks()'));

class Element {
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.isConnected=true;}
  append(...children){this.children.push(...children);}
  replaceChildren(){this.children=[];}
  setAttribute(){}
  addEventListener(name,handler){this.handlers[name]=handler;}
}
function find(element,tag){return element.tag===tag?element:element.children.map(c=>find(c,tag)).find(Boolean);}

for(const brand of ['kps','enbishan'])for(const shared of [false,true]){
  test(`${brand} ${shared?'shared':'canonical morning'} destination reads and saves in its authorized scope`,async()=>{
    const host=new Element('main'),calls=[],adapter=create();
    const user={id:'driver',role:'driver',brand_id:brand,central_operations:{brands:[
      {id:'kps',branch_ids:['kps-branch']},{id:'enbishan',branch_ids:['enbishan-branch']},
    ]}};
    const raw=async(path,options)=>{calls.push({path,options});return {hafar:true,branches:[{id:brand+'-branch',name:'Branch'}]};};
    const context={state:{user,page:'tasks'},document:{createElement:tag=>new Element(tag)},$:()=>host,tr:(ar)=>ar,
      api:(path,options={})=>adapter.request(path,options,raw,user,'tasks')};
    vm.createContext(context);vm.runInContext(panel,context);
    await context.renderDriverDestinationPanel({status:'open',brand_id:brand,...(shared?{central_workday_id:'day'}:{})});
    const form=find(host,'form');assert.ok(form,'destination options must render instead of an error');
    find(form,'select').value='branch:'+brand+'-branch';
    await form.handlers.submit({preventDefault(){}});
    assert.equal(calls.length,2);
    for(const call of calls){
      assert.equal(call.path,shared?'/api/central-operations/driver-destination':'/api/operations/driver-destination');
      assert.equal(call.options.brand,shared?undefined:brand);
    }
    assert.equal(calls[1].options.method,'PUT');
    assert.equal(calls[1].options.body.branch_id,brand+'-branch');
    assert.equal(user.brand_id,brand);
  });
}

test('closed shifts do not fetch destination or display controls',async()=>{
  const context={state:{user:{id:'driver',role:'driver'}},api:()=>{throw Error('must not fetch');}};
  vm.createContext(context);vm.runInContext(panel,context);
  await context.renderDriverDestinationPanel({status:'closed'});
});
