/* Keep the current workspace visible before the full operations menu. */
(function(root){
  'use strict';
  function mount({side,nav,label,enabled}){
    if(!side||!nav)return;
    side.classList.toggle('compact-navigation',enabled);
    if(!enabled)return;
    let toggle=side.querySelector('.dispatch-nav-toggle');
    if(!toggle){toggle=nav.ownerDocument.createElement('button');toggle.type='button';toggle.className='quiet dispatch-nav-toggle';side.prepend(toggle);}
    toggle.textContent=label;toggle.setAttribute('aria-controls',nav.id);
    const close=()=>{side.classList.remove('nav-open');toggle.setAttribute('aria-expanded','false');};
    // Reuse the delivery-room control when it already owns its click handler.
    if(!toggle.dataset.operationsNavigation){
      toggle.dataset.operationsNavigation='true';
      toggle.addEventListener('click',()=>{const open=side.classList.toggle('nav-open');toggle.setAttribute('aria-expanded',String(open));});
      side.addEventListener('keydown',event=>{if(event.key==='Escape'){close();toggle.focus();}});
    }
    close();
  }
  root.KPSOperationsNavigation={mount};
})(typeof window==='undefined'?globalThis:window);
