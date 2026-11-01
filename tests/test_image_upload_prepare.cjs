/* Supported phone captures remain within the server image limits. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('public/ops.js','utf8');
const body=source.slice(source.indexOf('  async function prepareUploadImage('),source.indexOf('\n  function photoTransition('));
let dimensions=[1200,900],decodeFails=false,canvasCount=0,revoked=0,encoded=null,posted=null,canvasSize=null;
const context={
  Image:class{set src(value){[this.naturalWidth,this.naturalHeight]=dimensions;queueMicrotask(()=>decodeFails?this.onerror():this.onload());}},
  URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>revoked++},
  document:{createElement(tag){assert.equal(tag,'canvas');canvasCount++;return {getContext:()=>({fillRect(){},drawImage(){}}),toBlob(callback,type,quality){assert.equal(type,'image/jpeg');assert.equal(quality,.85);canvasSize=[this.width,this.height];callback({size:900000,type:'image/jpeg'});}};}},
  FileReader:class{readAsDataURL(file){encoded=file;this.result='data:'+file.type+';base64,TEST';this.onload();}},
  tr:ar=>ar,
  mutation:(path,payload)=>{posted={path,payload};return {id:'saved'};},
};
vm.createContext(context);vm.runInContext(body+'\nglobalThis.prepare=prepareUploadImage;globalThis.upload=imageUpload;',context);
(async()=>{
  const original={size:2000,type:'image/png'};
  assert.equal(await context.prepare(original),original,'ordinary evidence is unchanged');
  assert.equal(canvasCount,0);assert.equal(revoked,1);
  dimensions=[8064,6048];
  await context.upload('/api/conversations/test/attachments',original,{test:'retained'});
  assert.deepEqual(canvasSize,[2400,1800]);assert.equal(encoded.type,'image/jpeg');
  assert.equal(posted.payload.content_type,'image/jpeg');assert.equal(posted.payload.test,'retained');
  assert.equal(posted.payload.data_base64,'TEST');
  dimensions=[3000,2000];
  await context.prepare({size:6*1024*1024,type:'image/jpeg'});
  assert.equal(canvasCount,2,'over-byte-limit photos are normalized');
  assert.deepEqual(canvasSize,[2400,1600]);
  decodeFails=true;
  await assert.rejects(context.prepare(original),/تعذر قراءة/);
  assert.equal(revoked,4,'blob URLs are released on all decode paths');
  await assert.rejects(context.prepare({size:123,type:'text/plain'}));
  await assert.rejects(context.prepare({size:41*1024*1024,type:'image/jpeg'}));
  console.log('image preparation: original preservation, phone dimensions, byte limit, payload type, invalid input and cleanup passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
