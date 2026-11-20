(function(root){
  'use strict';
  function selected(product, options){
    if(product?.brand_id!=='enbishan'||!Array.isArray(product.variant_price_matrix))return null;
    const keys=(product.option_groups||[]).map(g=>g.code);
    if(Object.keys(options||{}).length!==keys.length||keys.some(k=>!options[k]))return null;
    return product.variant_price_matrix.find(row=>keys.every(k=>row.options[k]===options[k]))||null;
  }
  function starting(product){
    if(product?.brand_id!=='enbishan'||!Array.isArray(product.variant_price_matrix))return null;
    return product.variant_price_matrix.find(row=>row.price_halalas===product.price_halalas)||null;
  }
  const api={selected,starting};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.KPSMenuVariants=api;
})(typeof window!=='undefined'?window:globalThis);
