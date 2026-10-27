'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('public/customer.js','utf8');
const ctx={groups:p=>p.option_groups||[],safeImage:u=>u?.startsWith('/assets/')?u:null,sourceText:v=>v?.name,entryText:(ar,en)=>ar,requestedBrandId:'enbishan'};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('  function menuSection('),source.indexOf('  function renderCategories()')),ctx);
const products=JSON.parse(fs.readFileSync('tests/fixtures/enbishan-catalog-presentation.json','utf8'));
const grape=products.find(p=>p.name==='ورق عنب');const before=JSON.stringify(grape);
assert.equal(ctx.menuCover(grape).image_url,grape.variant_images.find(v=>v.options.SIZE==='v-4d099242d361cb106794a77c').image_url);
assert.match(ctx.menuCover(grape).label,/عائلي/);assert.equal(JSON.stringify(grape),before,'Cover never preselects or mutates an order');
for(const p of products){const cover=ctx.menuCover(p);assert.ok(cover.image_url);if(cover.label)assert.ok(p.variant_images.some(v=>v.image_url===cover.image_url));assert.notEqual(ctx.menuSection(p),'أصناف أخرى');}
assert.equal(ctx.menuSection({brand_id:'kps',name:'بوكس',category:'Coffee'}),'Coffee');
assert.equal(ctx.menuCover({brand_id:'kps',image_url:'/assets/kps.jpg'}).image_url,'/assets/kps.jpg');
console.log('PASS: large covers, actual size photo mappings, stable sections and KPS isolation.');
const overview=[...products].sort((a,b)=>ctx.menuRank(a,true)-ctx.menuRank(b,true));
assert.equal(overview[0].name,'عنبيشن سيجنتشر');
assert.ok(overview.slice(0,6).every(p=>ctx.menuCover(p).label),'The first six overview cards show named box sizes');
