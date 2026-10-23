/* Central staff screens over canonical, revocable branch-scoped APIs. */
(() => {
  'use strict';
  const collections = new Map([
    ['/api/driver-work-hours/current',[]], ['/api/reports',['reports']], ['/api/report-notifications',['notifications']],
    ['/api/custody',['transfers']], ['/api/printing/devices',['devices']],
    ['/api/printing/operational-devices',['devices']], ['/api/printing/jobs',['jobs']],
    ['/api/printing/migration-status',['branches']], ['/api/support-tickets',['tickets']],
    ['/api/vehicle-maintenance',['reports']], ['/api/dispatch/offers',['offers']],
    ['/api/fuel-exceptions',['requests']], ['/api/driver-breaks',['breaks']],
    ['/api/driver-closure',['requests']], ['/api/orders',['orders']], ['/api/tasks',['tasks']],
    ['/api/conversations',['conversations']], ['/api/conversations/alerts',['awaiting_reply']],
    ['/api/shifts',['shifts','available_manager_shifts']],
    ['/api/shifts/current',['shifts','available_manager_shifts']],
    ['/api/operations/people',['users','available_manager_shifts','closure_manager_shifts','authorized_receivers']],
    ['/api/operations/drivers',['drivers']], ['/api/operations/driver-locations',['drivers']],
    ['/api/operations/alerts',['unassigned','problems','printing','closure_exceptions']],
    ['/api/orders/customer-search',['contacts']], ['/api/task-recovery',['tasks']],
  ]);
  const kinds={orders:'order',tasks:'task',conversations:'conversation',shifts:'shift',branches:'branch',devices:'printer',transfers:'custody',users:'staff',reports:'report'};
  function create(){
    const records=new Map(),workdays=new Map(),workdayLegs=new Map();let signature='';
    const key=(kind,id)=>kind+':'+id;
    function remember(data,brand){
      if(!data||typeof data!=='object')return data;
      for(const [name,value] of Object.entries(data)){
        for(const row of Array.isArray(value)?value:[value])if(row?.central_workday_id&&row.id){const anchor=row.central_anchor_id||row.id;workdays.set(row.id,anchor);if(!workdayLegs.has(anchor))workdayLegs.set(anchor,new Map());workdayLegs.get(anchor).set(row.brand_id||brand,row.id);}
        const kind=kinds[name]||kinds[name+'s'];
        if(kind)for(const row of Array.isArray(value)?value:[value])if(row?.id){
          records.set(key(kind,row.id),row.brand_id||brand);
          if(row.order_id)records.set(key('order',row.order_id),row.brand_id||brand);
          if(row.branch_id)records.set(key('branch',row.branch_id),row.brand_id||brand);
        }
      }
      return data;
    }
    function merge(results,names,url){
      const output={...results[0].data};
      for(const name of names){
        const seen=new Map();
        for(const {data,brand} of results)for(const row of data[name]||[]){
          const identity=row.central_workday_id||row.id||[row.branch_id,row.phone,row.name].join(':');
          if(row.central_workday_id&&seen.has(identity)){
            const previous=seen.get(identity),anchor=row.id===row.central_anchor_id?{...row,brand_id:row.brand_id||brand.id}:previous;
            const order_counts={};for(const item of [previous.order_counts||{},row.order_counts||{}])for(const [k,v]of Object.entries(item))order_counts[k]=(order_counts[k]||0)+v;
            seen.set(identity,{...anchor,order_counts,opening_halalas:Number(previous.opening_halalas||0)+Number(row.opening_halalas||0),permanent_float:{amount:Number(previous.permanent_float?.amount||0)+Number(row.permanent_float?.amount||0)},branch_ids:[...new Set([...(previous.branch_ids||[]),...(row.branch_ids||[])])],
              central_legs:[...(previous.central_legs||[]),{id:row.id,brand_id:brand.id,branch_ids:row.branch_ids||[]}],
              operational_brands:[...new Set([...(previous.operational_brands||[]),brand.id])]});
            continue;
          }
          if(['users','drivers','authorized_receivers'].includes(name)&&seen.has(identity)){
            const current=seen.get(identity);
            current.branch_ids=[...new Set([...(current.branch_ids||[]),...(row.branch_ids||[])])];
            current.operational_brands=[...new Set([...(current.operational_brands||[]),brand.id])];
          }else seen.set(identity,{...row,brand_id:row.brand_id||brand.id,brand_name:brand.name,operational_brands:[brand.id],...(row.central_workday_id?{central_legs:[{id:row.id,brand_id:brand.id,branch_ids:row.branch_ids||[]}]}:{})});
        }
        output[name]=[...seen.values()];
      }
      if(url.pathname==='/api/driver-work-hours/current')output.trip=results.map(r=>r.data.trip).find(Boolean)||null;
      if(output.orders){
        const newest=url.searchParams.get('sort')==='newest';
        output.orders.sort((a,b)=>(newest?-1:1)*(String(a.created_at||'').localeCompare(String(b.created_at||'')))||String(a.id).localeCompare(String(b.id)));
      }
      if(output.conversations)output.conversations.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))||String(a.id).localeCompare(String(b.id)));
      // The offset advances each constituent stream by the same canonical page
      // size. Do not globally truncate: that would silently drop other rows.
      if(results.some(r=>'next_offset' in r.data))output.next_offset=results.reduce((next,r)=>r.data.next_offset==null?next:Math.max(next??0,r.data.next_offset),null);
      for(const name of ['counts','workspace_counts'])if(results.some(r=>r.data[name])){
        output[name]={};
        for(const {data} of results)for(const [k,v] of Object.entries(data[name]||{})){
          if(typeof v==='number')output[name][k]=(output[name][k]||0)+v;
        }
      }
      for(const name of ['unread_count','migrated_branches','remaining_branches'])if(results.some(r=>name in r.data))output[name]=results.reduce((n,r)=>n+Number(r.data[name]||0),0);
      if(results.some(r=>'total' in r.data))output.total=results.reduce((n,r)=>n+Number(r.data.total||0),0);
      output.has_more=results.some(r=>r.data.has_more);
      return output;
    }
    async function request(path,options,raw,user,page){
      const context=user?.central_operations;
      if(!context||options.brand||!path.startsWith('/api/')||user.delivery_context)return raw(path,options);
      const current=user.id+':'+JSON.stringify(context);
      if(current!==signature){records.clear();workdays.clear();workdayLegs.clear();signature=current;}
      const url=new URL(path,'https://central.invalid'),method=options.method||'GET';
      if(url.pathname==='/api/identity/staff'&&method==='GET')return remember(await raw('/api/central-operations/staff',options),user.brand_id);
      const staffEdit=url.pathname.match(/^\/api\/identity\/staff\/([^/]+)$/);
      if(url.pathname==='/api/identity/staff/invitations'&&method==='POST')return raw('/api/central-operations/staff/invitations',options);
      if(staffEdit&&method==='PATCH')return raw('/api/central-operations/staff/'+staffEdit[1],options);
      if(url.pathname==='/api/shifts'&&method==='POST'&&options.body?.delivery_period!=='morning'){
        return remember(await raw('/api/central-operations/workdays',options),user.brand_id);
      }
      const shiftAction=url.pathname.match(/^\/api\/(shifts|driver-closure)\/([^/]+)(?:\/([^/]+))?$/);
      if(shiftAction){
        const id=decodeURIComponent(shiftAction[2]),anchor=workdays.get(id),action=shiftAction[3];
        if(anchor&&(!action||['closing','approve','return','driver-close','submit','request','decision','schedule','withdraw'].includes(action))){
          return remember(await raw('/api/central-operations/workdays/'+encodeURIComponent(anchor)+(action?'/'+action:''),options),user.brand_id);
        }
      }
      let resource=null;
      const custom=[['/api/report-notifications/','notification'],['/api/fuel-exceptions/','fuel'],['/api/driver-breaks/','break'],['/api/dispatch/offers/','offer'],['/api/vehicle-maintenance/','maintenance'],['/api/printing/jobs/','print_job'],['/api/support-tickets/','ticket'],['/api/driver-work-hours/','trip']].find(([prefix])=>url.pathname.startsWith(prefix));
      const reservedResources=new Set(['current','report','options','policy','photos','daily','shifts','depart','next-destination']);
      if(custom){const identifier=url.pathname.slice(custom[0].length).split('/')[0];if(identifier&&!reservedResources.has(identifier))resource={kind:custom[1],id:decodeURIComponent(identifier)};}
      const extra=url.pathname.match(/^\/api\/(?:printing\/devices|reports|custody|identity\/staff)\/([^/]+)/);
      if(extra)resource={kind:url.pathname.startsWith('/api/printing/')?'printer':url.pathname.startsWith('/api/reports/')?'report':url.pathname.startsWith('/api/custody/')?'custody':'staff',id:decodeURIComponent(extra[1])};
      const match=url.pathname.match(/\/(orders|tasks|shifts|conversations)\/([^/]+)/);
      const reserved=new Set(['current','alerts','customer-search','customer-lookup','delivery-center','complimentary']);
      if(shiftAction?.[1]==='driver-closure')resource={kind:'shift',id:decodeURIComponent(shiftAction[2])};
      if(match&&!reserved.has(match[2]))resource={kind:kinds[match[1]],id:decodeURIComponent(match[2])};
      for(const [parameter,kind] of [['order_id','order'],['task_id','task'],['branch_id','branch']]){
        const id=url.searchParams.get(parameter)||options.body?.[parameter];
        if(!resource&&id)resource={kind,id};
      }
      if(resource){
        let brand=records.get(key(resource.kind,resource.id));
        if(!brand){const resolved=await raw('/api/central-operations/resource-context?'+new URLSearchParams({kind:resource.kind,resource_id:resource.id}),{});brand=resolved.brand_id;}
        let body=options.body;
        if(resource.kind==='branch'&&body?.shift_id){const anchor=workdays.get(body.shift_id),leg=workdayLegs.get(anchor)?.get(brand);if(leg)body={...body,shift_id:leg};}
        return remember(await raw(path,{...options,body,brand}),brand);
      }
      const names=collections.get(url.pathname);
      if(method==='GET'&&names){
        // A missing constituent is an error, never a healthy partial inbox.
        const results=await Promise.all(context.brands.filter(b=>b.branch_ids.length||user.role==='owner').map(async brand=>{
          const data=await raw(path,{...options,brand:brand.id});remember(data,brand.id);return {brand,data};
        }));
        return results.length?merge(results,names,url):Object.fromEntries(names.map(n=>[n,[]]));
      }
      // Settings and accounting select their own brand locally. Operating
      // screens never change the signed-in principal or a global brand filter.
      return raw(path,options);
    }
    return {request,remember};
  }
  const exported={create};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;
  else window.KPSCentralOperations=exported;
})();
