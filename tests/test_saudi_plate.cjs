const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync('public/assets/saudi-plate.js','utf8'),context);
const plate=context.window.KPSSaudiPlate;
const pairs={ا:'A',ب:'B',ح:'J',د:'D',ر:'R',س:'S',ص:'X',ط:'T',ع:'E',ق:'G',ك:'K',ل:'L',م:'Z',ن:'N',ه:'H',و:'U',ى:'V'};

for(const [ar,en] of Object.entries(pairs))test(`Saudi plate letter ${ar} maps to ${en}`,()=>{
  assert.equal(plate.parse(ar.repeat(3)+' 1234').latin.join(''),en.repeat(3));
  assert.equal(plate.parse('1234 '+en.repeat(3)).arabic.join(''),ar.repeat(3));
});

test('all 4913 letter triplets round-trip without changing physical columns',()=>{
  for(const a of Object.keys(pairs))for(const b of Object.keys(pairs))for(const c of Object.keys(pairs)){
    const letters=a+b+c,parsed=plate.parse(letters+' ١٢٣٤');
    assert.equal(parsed.latin.join(''),pairs[c]+pairs[b]+pairs[a]);
    const back=plate.parse('1234 '+parsed.latin.join(''));
    assert.equal(back.arabic.join(''),letters);
    assert.equal(back.key,parsed.key);
  }
});

test('Arabic/Latin ordering, lowercase, Persian/Arabic digits and safe separators',()=>{
  const key='1234:DZJ';
  for(const value of ['ح م د ١٢٣٤','١٢٣٤ ح م د','حمد1234','1234 dzj','D Z J / ۱۲۳۴','\u200fح م د\u200e 1234'])assert.equal(plate.parse(value).key,key);
  assert.equal(plate.parse('أ ي هـ ٠٠٢٣').key,'0023:HVA');
  assert.equal(plate.parse('ى و ب 7').key,'7:BUV');
  assert.equal(plate.parse('7 BUV').arabic.join(''),'ىوب');
});

test('one to four digits retain their exact value, order and leading zeros',()=>{
  for(const number of ['1','12','123','1234','0007']){
    const parsed=plate.parse(number+' ABC'.replace('C','D'));
    assert.equal(parsed.digits,number);
    assert.equal(plate.parse(parsed.arabic.join('')+parsed.arabicDigits).digits,number);
  }
});

test('unsupported, mixed-language, malformed and malicious values are not guessed',()=>{
  for(const value of ['',null,1234,'TEST FLEET <img>','ABC1234','AB1234','ABCD1234','ج ب د 1234','حBد1234','DZJ12345','12DZJ34','1234 DZJ<script>','1234 DZJ!', 'حمد ١٢٣٤ ٥'])assert.equal(plate.parse(value),null,String(value));
  assert(plate.render('TEST <img src=x onerror=alert(1)>').includes('&lt;img'));
  assert(!plate.render('TEST <img src=x onerror=alert(1)>').includes('<img'));
  assert(plate.field('vehicle_plate','\" autofocus onfocus=alert(1)').includes('&quot;'));
});

test('private white plate output is bilingual and gives each letter its own column',()=>{
  const html=plate.render('ح م د ١٢٣٤','en');
  assert(html.includes('Saudi private vehicle plate: 1234 · D Z J'));
  assert(html.includes('١٢٣٤'));
  assert(html.includes('<span>د</span><span>م</span><span>ح</span>'));
  assert(html.includes('<span>D</span><span>Z</span><span>J</span>'));
  assert(html.includes('خصوصي'));
  assert(plate.render('1234 DZJ','ar',true).includes('saudi-plate--compact'));
  assert(!html.includes('<img'));
});

test('language-only edits keep the original persisted plate and permanent identity',()=>{
  const original=' ح م د ١٢٣٤ ';
  for(const value of ['1234 DZJ','حمد 1234','۱۲۳۴ dzj'])assert.equal(plate.preserve(value,original),original);
  assert.equal(plate.preserve('1235 DZJ',original),'1235 DZJ');
  assert.equal(plate.preserve('1234 JZD',original),'1234 JZD');
  assert.equal(plate.preserve(' TEST LEGACY ','TEST LEGACY'),'TEST LEGACY');
  assert.equal(plate.preserve('','ح م د 1234'),'');
});

test('operations load shared assets before fleet and reuse the same formatter',()=>{
  const html=fs.readFileSync('public/ops.html','utf8'),ops=fs.readFileSync('public/ops.js','utf8'),fleet=fs.readFileSync('public/assets/fleet.js','utf8');
  assert(html.indexOf('/assets/saudi-plate.js')<html.indexOf('/assets/fleet.js'));
  assert(html.includes('/assets/saudi-plate.css'));
  assert(ops.includes('window.KPSSaudiPlate.bindInputs'));
  assert(ops.includes('plate:window.KPSSaudiPlate.preserve'));
  assert(ops.includes('window.KPSSaudiPlate.render(v.plate'));
  assert(fleet.includes("c.key==='plate'?plate(r.plate,true)"));
});
