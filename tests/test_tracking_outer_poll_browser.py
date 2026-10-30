"""Exercise shipped operations poller with the real tracking mount and wall timers."""
from test_tracking_wall_eta_browser import wall, ROOT, eta_count, pytestmark


def test_outer_operations_poll_preserves_wall_mount_and_minute_eta_budget(wall):
    source = (ROOT / 'public/ops.js').read_text()
    read_scope = next(line for line in source.splitlines() if line.startswith('  function pageReadApi('))
    tracking = source[source.index('  let trackingController=null;'):source.index('  async function dashboard()', source.index('  let trackingController=null;'))]
    polling = next(line for line in source.splitlines() if line.startswith('  let polling=false;setInterval('))
    wall.evaluate('''() => {controller.destroy();window.timers=[];window.requests=[];window.mountCount=0;
      const original=KPSOrderTracking.mount;KPSOrderTracking.mount=async(...args)=>{mountCount++;return original(...args);};
    }''')
    wall.evaluate('''async({tracking,polling,readScope})=>{
      const state={user:{id:'manager',role:'delivery_manager'},page:'tracking',lang:'en',branches:[{id:'b1',city:'Hafar'}],busy:new Set(),orders:[]};
      const trialMode=false,$=selector=>selector==='#content'?document.querySelector('#host'):selector==='#modal'?{open:false}:null;
      const api=window.api,tr=(ar,en)=>en,loadBranches=async()=>{},heading=()=>{};
      let loadCount=0;
      const loadPage=async()=>{loadCount++;await window.outerTrackingMount();};
      eval(readScope+';'+tracking+';globalThis.outerTrackingMount=tracking;');
      // Shipped interval closes over the same state and routes into the actual
      // operations tracking function, so accidental remounts reset real caches.
      const mount=window.outerTrackingMount;
      eval(polling);
      await mount();
      window.outerMountCount=()=>mountCount;
      window.outerState=state;
    }''', {'tracking': tracking, 'polling': polling, 'readScope': read_scope})
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    assert wall.evaluate('mountCount') == 1
    first = eta_count(wall)
    for _ in range(3):
        wall.evaluate('tick(15000)')
        assert wall.evaluate('mountCount') == 1
        assert eta_count(wall) == first
    wall.evaluate('tick(16000)')
    wall.wait_for_function(f"requests.filter(r=>r.path.includes('/route-eta')).length==={first + 1}")
    assert wall.evaluate('mountCount') == 1
