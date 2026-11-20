/* Central staff screens over canonical, revocable branch-scoped APIs. */
(() => {
  'use strict';
  const collections = new Map([
    ['/api/preparation-routing/tasks',['preparation_tasks']],
    ['/api/driver-work-hours/current',[]], ['/api/reports',['reports']], ['/api/report-notifications',['notifications']],
    ['/api/custody',['transfers']], ['/api/printing/devices',['devices']],
    ['/api/printing/operational-devices',['devices']], ['/api/printing/jobs',['jobs']],
    ['/api/printing/migration-status',['branches']], ['/api/support-tickets',['tickets']],
    ['/api/vehicle-maintenance/reports',['reports']], ['/api/dispatch/offers',['offers']],
    ['/api/fuel-exceptions',['requests']], ['/api/driver-breaks',['breaks']],
    ['/api/driver-closure',['requests']], ['/api/orders',['orders']], ['/api/order-amendments/inbox',['orders','requests']], ['/api/tasks',['tasks']],
    ['/api/conversations',['conversations']], ['/api/conversations/alerts',['awaiting_reply']],
    ['/api/shifts',['shifts','available_manager_shifts']],
    ['/api/shifts/current',['shifts','available_manager_shifts']],
    ['/api/operations/people',['users','available_manager_shifts','closure_manager_shifts','authorized_receivers']],
    ['/api/operations/drivers',['drivers']], ['/api/operations/driver-locations',['drivers']],
    ['/api/operations/alerts',['unassigned','problems','printing','closure_exceptions']],
    ['/api/orders/customer-search',['contacts']], ['/api/task-recovery',['tasks']],
  ]);
  const kinds={orders:'order',tasks:'task',conversations:'conversation',shifts:'shift',branches:'branch',devices:'printer',transfers:'custody',users:'staff',reports:'report',vehicles:'vehicle'};
  function create(){
    const records=new Map(),workdays=new Map(),workdayLegs=new Map(),staffScopes=new Map(),managerShifts=new Map();let signature='';
    const key=(kind,id)=>kind+':'+id;
    function remember(data,brand){
      if(!data||typeof data!=='object')return data;
      for(const [name,value] of Object.entries(data)){
        if(['available_manager_shifts','closure_manager_shifts','shifts'].includes(name)){
          for(const row of Array.isArray(value)?value:[value])if(row?.id&&(name!=='shifts'||row.kind==='manager')){
            managerShifts.set(row.id,{...row,brand_id:row.brand_id||brand});
          }
        }
        for(const row of Array.isArray(value)?value:[value])if(row?.central_workday_id&&row.id){const anchor=row.central_anchor_id||row.id;workdays.set(row.id,anchor);if(!workdayLegs.has(anchor))workdayLegs.set(anchor,new Map());workdayLegs.get(anchor).set(row.brand_id||brand,row.id);}
        if(name==='users')for(const row of Array.isArray(value)?value:[value])if(row?.id&&row.branch_grants)staffScopes.set(row.id,[...row.branch_ids]);
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
            // Only sum fields the server actually disclosed. Blind driver
            // payloads must not acquire invented zero-value financial fields.
            const financial={};
            for(const k of new Set([...Object.keys(previous),...Object.keys(row)]))if(k.endsWith('_halalas')&&(typeof previous[k]==='number'||typeof row[k]==='number'))financial[k]=Number(previous[k]||0)+Number(row[k]||0);
            for(const name of ['submission','closure'])if(previous[name]||row[name]){
              financial[name]={...(anchor[name]||{})};
              for(const k of new Set([...Object.keys(previous[name]||{}),...Object.keys(row[name]||{})]))if(k.endsWith('_halalas')&&(typeof previous[name]?.[k]==='number'||typeof row[name]?.[k]==='number'))financial[name][k]=Number(previous[name]?.[k]||0)+Number(row[name]?.[k]||0);
            }
            if(typeof previous.permanent_float?.amount==='number'||typeof row.permanent_float?.amount==='number')financial.permanent_float={amount:Number(previous.permanent_float?.amount||0)+Number(row.permanent_float?.amount||0)};
            seen.set(identity,{...anchor,order_counts,...financial,branch_ids:[...new Set([...(previous.branch_ids||[]),...(row.branch_ids||[])])],
              central_legs:[...(previous.central_legs||[]),{id:row.id,brand_id:brand.id,branch_ids:row.branch_ids||[]}],
              operational_brands:[...new Set([...(previous.operational_brands||[]),brand.id])]});
            continue;
          }
          if(['users','drivers','authorized_receivers'].includes(name)&&seen.has(identity)){
            const current=seen.get(identity);
            if(name==='drivers'&&url.pathname==='/api/operations/drivers'){
              // One physical driver can be unavailable in either brand ledger.
              // Prefer on-duty unavailability, then ready duty, over no duty.
              const rank={no_shift:0,available:1,busy:2};
              if((rank[row.live_status]??-1)>(rank[current.live_status]??-1))current.live_status=row.live_status;
            }
            current.available=current.available!==false&&row.available!==false;
            current.branch_ids=[...new Set([...(current.branch_ids||[]),...(row.branch_ids||[])])];
            current.operational_brands=[...new Set([...(current.operational_brands||[]),brand.id])];
          }else seen.set(identity,{...row,brand_id:row.brand_id||brand.id,brand_name:brand.name,operational_brands:[brand.id],...(row.central_workday_id?{central_legs:[{id:row.id,brand_id:brand.id,branch_ids:row.branch_ids||[]}]}:{})});
        }
        output[name]=[...seen.values()];
      }
      if(url.pathname==='/api/operations/drivers'&&output.drivers?.every(row=>['available','busy','no_shift'].includes(row.live_status))){
        output.driver_counts={total:output.drivers.length,available:0,busy:0,no_shift:0};
        for(const row of output.drivers)output.driver_counts[row.live_status]++;
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
      // Dispatch counts cover the full authorized cohort, not just the
      // paginated orders. Preserve both scopes and their nested source totals.
      function sumCounts(target,source){
        for(const [k,v] of Object.entries(source||{})){
          if(typeof v==='number')target[k]=(target[k]||0)+v;
          else if(v&&typeof v==='object'&&!Array.isArray(v)){
            if(!target[k])target[k]={};
            sumCounts(target[k],v);
          }
        }
      }
      for(const name of ['counts','workspace_counts'])if(results.some(r=>r.data[name])){
        output[name]={};
        for(const {data} of results)sumCounts(output[name],data[name]);
      }
      for(const name of ['unread_count','migrated_branches','remaining_branches'])if(results.some(r=>name in r.data))output[name]=results.reduce((n,r)=>n+Number(r.data[name]||0),0);
      if(results.some(r=>'total' in r.data))output.total=results.reduce((n,r)=>n+Number(r.data.total||0),0);
      output.has_more=results.some(r=>r.data.has_more);
      return output;
    }
    async function request(path,options,raw,user,page){
      const context=user?.central_operations;
      if(options.brand||!path.startsWith('/api/')||user?.delivery_context)return raw(path,options);
      const current=user?.id+':'+JSON.stringify([context,user?.central_workday_drain]);
      if(current!==signature){records.clear();workdays.clear();workdayLegs.clear();staffScopes.clear();managerShifts.clear();signature=current;}
      const url=new URL(path,'https://central.invalid'),method=options.method||'GET';
      for(const day of user?.central_workday_drain||[]){
        const anchor=day.anchor_shift_id;
        if(!anchor)continue;
        workdays.set(anchor,anchor);
        if(!workdayLegs.has(anchor))workdayLegs.set(anchor,new Map());
        for(const leg of day.legs||[]){workdays.set(leg.shift_id,anchor);workdayLegs.get(anchor).set(leg.brand_id,leg.shift_id);records.set(key('shift',leg.shift_id),leg.brand_id);}
      }
      if(!context){
        // Turning off the rollout stops new unified workdays, while canonical
        // authorized shift reads still discover existing groups for closure.
        const action=url.pathname.match(/^\/api\/(?:shifts|driver-closure)\/([^/]+)(?:\/([^/]+))?$/),anchor=action&&workdays.get(decodeURIComponent(action[1]));
        if(anchor&&(!action[2]||['closing','approve','return','driver-close','submit','request','decision','schedule','withdraw'].includes(action[2])))return remember(await raw('/api/central-operations/workdays/'+encodeURIComponent(anchor)+(action[2]?'/'+action[2]:''),options),user.brand_id);
        const data=await raw(path,options);
        if(method==='GET'&&/^\/api\/shifts(?:\/[^/]+)?$/.test(url.pathname)){
          remember(data,user?.brand_id);
          if(data?.shift?.central_workday_id)return remember(await raw('/api/central-operations/workdays/'+encodeURIComponent(data.shift.central_anchor_id||data.shift.id),options),user.brand_id);
        }
        return data;
      }
      if(url.pathname==='/api/manager-handovers'||/^\/api\/manager-handovers\/[^/]+\/accept$/.test(url.pathname))return raw(url.pathname.replace('/api/manager-handovers','/api/central-operations/manager-handovers')+url.search,options);
      if(url.pathname==='/api/operations/driver-destination')return raw('/api/central-operations/driver-destination'+url.search,options);
      if(url.pathname==='/api/driver-incentives'&&method==='GET')return raw('/api/central-operations/driver-incentives'+url.search,options);
      if(['/api/shifts','/api/shifts/current'].includes(url.pathname)&&method==='GET'&&url.searchParams.has('owner_view'))return remember(await raw('/api/central-operations/shifts'+url.search,options),user.brand_id);
      if(['/api/fleet/options','/api/fleet/report','/api/fleet/export'].includes(url.pathname)&&method==='GET')return raw(url.pathname.replace('/api/fleet/','/api/central-operations/fleet/')+url.search,options);
      if(url.pathname==='/api/fleet/vehicles'&&method==='GET')return remember(await raw('/api/central-operations/fleet/vehicles'+url.search,options),user.brand_id);
      if(url.pathname==='/api/dashboard'&&method==='GET')return raw('/api/central-operations/dashboard'+url.search,options);
      if(url.pathname==='/api/operations/driver-locations'&&method==='GET')return raw('/api/central-operations/driver-locations',options);
      if(url.pathname==='/api/operations/driver-route-context'&&['GET','POST'].includes(method))return raw('/api/central-operations/driver-route-context'+url.search,options);
      if(['/api/operations/route-eta','/api/operations/driver-route-plan','/api/operations/map-services/trip-stops/current'].includes(url.pathname))return raw(path,options);
      if(url.pathname==='/api/identity/staff'&&method==='GET')return remember(await raw('/api/central-operations/staff',options),user.brand_id);
      const staffPhoneSwap=url.pathname.match(/^\/api\/identity\/staff\/([^/]+)\/swap-phone$/);
      if(staffPhoneSwap&&method==='POST')return raw('/api/central-operations/staff/'+staffPhoneSwap[1]+'/swap-phone',options);
      const staffEdit=url.pathname.match(/^\/api\/identity\/staff\/([^/]+)$/);
      if(url.pathname==='/api/identity/staff/invitations'&&method==='POST')return raw('/api/central-operations/staff/invitations',options);
      if(staffEdit&&method==='PATCH'){const expected=staffScopes.get(decodeURIComponent(staffEdit[1]));return raw('/api/central-operations/staff/'+staffEdit[1],{...options,body:{...options.body,...(options.body?.branch_ids&&expected?{expected_branch_ids:expected}:{})}});}
      if(url.pathname==='/api/shifts'&&method==='POST'){
        const manager=managerShifts.get(options.body?.manager_shift_id);
        // Morning managers have a canonical single-brand shift, not a shared
        // workday. Drivers inherit that shift's period on the server. Preserve
        // the submitted scope and inspection; canonical checks still apply.
        if(options.body?.kind==='driver'&&manager?.status==='open'&&!manager.central_workday_id
            &&manager.delivery_window_snapshot?.period==='morning'){
          return remember(await raw(path,{...options,brand:manager.brand_id}),manager.brand_id);
        }
        if(options.body?.delivery_period!=='morning')return remember(await raw('/api/central-operations/workdays',options),user.brand_id);
      }
      const shiftAction=url.pathname.match(/^\/api\/(shifts|driver-closure)\/([^/]+)(?:\/([^/]+))?$/);
      if(shiftAction){
        const id=decodeURIComponent(shiftAction[2]),anchor=workdays.get(id),action=shiftAction[3];
        if(anchor&&(!action||['closing','approve','return','driver-close','submit','request','decision','schedule','withdraw','handover-manager'].includes(action))){
          return remember(await raw('/api/central-operations/workdays/'+encodeURIComponent(anchor)+(action?'/'+action:''),options),user.brand_id);
        }
      }
      let resource=null;
      const custom=[['/api/fleet/vehicles/','vehicle'],['/api/report-notifications/','notification'],['/api/fuel-exceptions/','fuel'],['/api/driver-breaks/','break'],['/api/dispatch/offers/','offer'],['/api/vehicle-maintenance/reports/','maintenance'],['/api/printing/jobs/','print_job'],['/api/support-tickets/','ticket'],['/api/driver-work-hours/','trip']].find(([prefix])=>url.pathname.startsWith(prefix));
      const reservedResources=new Set(['current','report','options','policy','photos','daily','shifts','depart','next-destination']);
      if(custom){const identifier=url.pathname.slice(custom[0].length).split('/')[0];if(identifier&&!reservedResources.has(identifier))resource={kind:custom[1],id:decodeURIComponent(identifier)};}
      const extra=url.pathname.match(/^\/api\/(?:printing\/devices|reports|custody|identity\/staff)\/([^/]+)/);
      if(extra&&!reservedResources.has(extra[1])&&extra[1]!=='promotions')resource={kind:url.pathname.startsWith('/api/printing/')?'printer':url.pathname.startsWith('/api/reports/')?'report':url.pathname.startsWith('/api/custody/')?'custody':'staff',id:decodeURIComponent(extra[1])};
      const match=url.pathname.match(/\/(orders|tasks|shifts|conversations)\/([^/]+)/);
      const reserved=new Set(['current','alerts','customer-search','customer-lookup','delivery-center','complimentary','promotions']);
      if(shiftAction?.[1]==='driver-closure')resource={kind:'shift',id:decodeURIComponent(shiftAction[2])};
      if(match&&!reserved.has(match[2]))resource={kind:kinds[match[1]],id:decodeURIComponent(match[2])};
      // Cancellation delegates can review a scoped intervention without a
      // general order grant. Resolve/cache that authority separately; the
      // canonical read or decision still checks current permission and scope.
      const intervention=url.pathname.match(/^\/api\/order-interventions\/orders\/([^/]+)(?:\/|$)/);
      if(intervention)resource={kind:'order_intervention',id:decodeURIComponent(intervention[1])};
      for(const [parameter,kind] of [['order_id','order'],['task_id','task'],['branch_id','branch'],['return_branch_id','branch'],['shift_id','shift']]){
        const id=url.searchParams.get(parameter)||options.body?.[parameter];
        if(!resource&&id)resource={kind,id};
      }
      if(resource){
        let brand=records.get(key(resource.kind,resource.id));
        if(!brand){
          const resolved=await raw('/api/central-operations/resource-context?'+new URLSearchParams({kind:resource.kind,resource_id:resource.id}),{});brand=resolved.brand_id;
          records.set(key(resource.kind,resource.id),brand);
          if(resource.kind==='shift'&&resolved.central_anchor_id)workdays.set(resource.id,resolved.central_anchor_id);
        }
        const anchor=resource.kind==='shift'?workdays.get(resource.id):null;
        const action=shiftAction?.[3];
        if(anchor&&shiftAction&&(!action||['closing','approve','return','driver-close','submit','request','decision','schedule','withdraw','handover-manager'].includes(action)))return remember(await raw('/api/central-operations/workdays/'+encodeURIComponent(anchor)+(action?'/'+action:''),options),user.brand_id);
        // Physical vehicle state belongs to the shared anchor, even when
        // opened through a side-brand task or a cold resource deep link.
        if(anchor&&anchor!==resource.id&&(url.pathname==='/api/driver-breaks'||['refuel','availability'].includes(action)||url.pathname.startsWith('/api/driver-evidence/shifts/'))){
          const nextPath=options.body?.shift_id?path:path.replace('/shifts/'+encodeURIComponent(resource.id),'/shifts/'+encodeURIComponent(anchor));
          return request(nextPath,{...options,...(options.body?.shift_id?{body:{...options.body,shift_id:anchor}}:{})},raw,user,page);
        }
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
        const output=results.length?merge(results,names,url):Object.fromEntries(names.map(n=>[n,[]]));
        if(['/api/shifts','/api/shifts/current'].includes(url.pathname)&&!url.search&&['delivery_manager','driver'].includes(user.role)){
          const previous=(output.shifts||[]).filter(s=>s.user_id===user.id&&s.status==='open'&&!s.staffing_ended_at&&!s.central_workday_id&&(s.delivery_window_snapshot?.period||'evening')!=='morning');
          if(previous.length===1){await raw('/api/central-operations/workdays/'+encodeURIComponent(previous[0].id)+'/adopt',{method:'POST',body:{}});return request(path,options,raw,user,page);}
        }
        return output;
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
