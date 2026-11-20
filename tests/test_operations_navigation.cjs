const assert=require('node:assert/strict');
require('../public/assets/operations-navigation.js');
function element(){
 const classes=new Set(),listeners={},attributes={};
 return {dataset:{},listeners,attributes,children:[],
  classList:{toggle(name,force){const enabled=force??!classes.has(name);enabled?classes.add(name):classes.delete(name);return enabled},remove:name=>classes.delete(name),contains:name=>classes.has(name)},
  setAttribute:(key,value)=>{attributes[key]=value},
  addEventListener(type,handler){(listeners[type]??=[]).push(handler)},
  dispatch(type,event={}){for(const handler of listeners[type]||[])handler(event)},
  prepend(child){this.children.unshift(child)},querySelector(){return this.children[0]||null},
  focus(){this.focused=true}};
}
for(const label of ['القائمة','Menu']){
 const side=element(),nav={id:'navigation',ownerDocument:{createElement:element}};
 const mount=enabled=>KPSOperationsNavigation.mount({side,nav,label,enabled});
 mount(true);
 const toggle=side.children[0];
 assert.equal(toggle.textContent,label);
 assert.equal(toggle.attributes['aria-controls'],'navigation');
 assert.equal(toggle.attributes['aria-expanded'],'false');
 assert(!side.classList.contains('nav-open'));
 toggle.dispatch('click');
 assert(side.classList.contains('nav-open'));
 assert.equal(toggle.attributes['aria-expanded'],'true');
 side.dispatch('keydown',{key:'Escape'});
 assert(!side.classList.contains('nav-open'));
 assert.equal(toggle.attributes['aria-expanded'],'false');
 assert.equal(toggle.focused,true);
 for(let i=0;i<3;i++)mount(true);
 assert.equal(side.children.length,1);
 assert.equal(toggle.listeners.click.length,1);
 assert.equal(side.listeners.keydown.length,1);
 toggle.dispatch('click');
 assert(side.classList.contains('nav-open'),'Repeated mounting must not double-toggle');
 mount(true); // Navigation remounts after a page change.
 assert(!side.classList.contains('nav-open'));
 mount(false);
 assert(!side.classList.contains('compact-navigation'));
}
KPSOperationsNavigation.mount({side:null,nav:null,enabled:true});
console.log('PASS: navigation opens, closes on Escape/page change, returns focus and mounts idempotently in AR/EN.');
