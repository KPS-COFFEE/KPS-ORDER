/* A modal-local GPS watch. Snapshot at shutter; no await on the critical path. */
(() => {
  'use strict';
  function create(geo = navigator.geolocation, clock = Date.now) {
    let watch = null, latest = null, generation = 0;
    const stop = () => {generation++;if(watch !== null)geo?.clearWatch(watch);watch=null;};
    const start = () => {
      stop();latest=null;
      if(!geo)return;
      const current=generation;
      try {watch=geo.watchPosition(p=>{
        if(current!==generation)return;
        const c=p.coords,stamp=Number(p.timestamp),now=clock();
        if(!Number.isFinite(stamp)||stamp>now||now-stamp>15000)return;
        if(![c.latitude,c.longitude,c.accuracy].every(Number.isFinite))return;
        if(c.latitude < -90 || c.latitude > 90 || c.longitude < -180 || c.longitude > 180 || c.accuracy<0)return;
        // Never let a delayed old callback replace a more recent fix.
        if(latest && stamp<latest.stamp)return;
        latest={stamp,lat:c.latitude,lng:c.longitude,accuracy_m:c.accuracy};
      },()=>{}, {enableHighAccuracy:true,maximumAge:0,timeout:8000});}catch{}
    };
    const snapshot = (source='browser_camera') => {
      const now=clock(),fix=latest;
      const value={captured_at:new Date(now).toISOString(),source};
      if(source==='browser_camera'&&fix&&now-fix.stamp>=0&&now-fix.stamp<=15000){
        Object.assign(value,{lat:fix.lat,lng:fix.lng,accuracy_m:fix.accuracy_m,location_at:new Date(fix.stamp).toISOString()});
      }
      stop();return value;
    };
    return {start,stop,snapshot};
  }
  window.KPSPhotoLocation={create};
})();
