const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function fixture(geo){
  const context={window:{},navigator:{},Date,Number};vm.createContext(context);
  vm.runInContext(fs.readFileSync('public/assets/photo-location.js','utf8'),context);
  return context.window.KPSPhotoLocation.create(geo,()=>100000);
}
test('missing or denied GPS never blocks the shutter',()=>{
  const a=fixture(null);a.start();assert.equal(a.snapshot().source,'browser_camera');
  const b=fixture({watchPosition(){throw Error('denied')}});b.start();assert.equal(b.snapshot().lat,undefined);
});
test('snapshot freezes the contemporaneous fix and stops collection',()=>{
  let callback,cleared=0;
  const gps=fixture({watchPosition(ok){callback=ok;return 3},clearWatch(){cleared++}});
  gps.start();callback({timestamp:99000,coords:{latitude:28,longitude:45,accuracy:200}});
  const data=gps.snapshot();assert.equal(data.lat,28);assert.equal(cleared,1);
  callback({timestamp:100000,coords:{latitude:29,longitude:46,accuracy:10}});
  assert.equal(data.lat,28);
});
test('stale positions and native camera return cannot get shutter GPS',()=>{
  let callback;const gps=fixture({watchPosition(ok){callback=ok;return 1},clearWatch(){}});
  gps.start();callback({timestamp:80000,coords:{latitude:28,longitude:45,accuracy:10}});
  assert.equal(gps.snapshot().lat,undefined);
  gps.start();callback({timestamp:99000,coords:{latitude:28,longitude:45,accuracy:10}});
  assert.equal(gps.snapshot('device_camera_return').lat,undefined);
});
