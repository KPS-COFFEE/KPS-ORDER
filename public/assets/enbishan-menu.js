/* Approved Enbishan presentation; never changes catalog records or price matrices. */
(()=>{'use strict';window.KPSEnbishanMenu={create(tr){
const entryText=tr||((ar,en)=>document.documentElement.lang==='en'?en:ar);
const requestedBrandId='enbishan';
const groups=p=>p?.option_groups||[];
const sourceText=(p,field='name')=>entryText(p?.[field]||'',p?.[field+'_en']||p?.[field]||'');
const safeImage=value=>typeof value==='string'&&(/^(\/assets\/|\/api\/product-assets\/|https:\/\/)/.test(value))?value:null;
  function menuSection(product) {
    if(product.brand_id!=='enbishan')return product.category||'';
    const name=product.name||'';
    if(/موهيتو|فتة/.test(name))return 'الفتة والموهيتو';
    if(/سيجنتشر|عنبيشن كبير/.test(name))return 'عنبيشن';
    if(/بوكس/.test(name))return 'البوكسات';
    if(/ورق عنب|ملفوف/.test(name))return 'ورق العنب والملفوف';
    if(/كبة|سمبوسة/.test(name))return 'المقليات';
    if(/مسخن/.test(name))return 'المسخن';
    if(/نودلز/.test(name))return 'النودلز';
    return product.category||'';
  }
  const enbishanSections=['ورق العنب والملفوف','المحاشي','المقليات','عنبيشن','البوكسات','المسخن','الفتة والموهيتو','النودلز','المخبوزات','المشروبات'];
  function menuSectionLabel(section) {
    const english={'عنبيشن':'Enbishan','ورق العنب والملفوف':'Grape leaves & cabbage','البوكسات':'Boxes','المحاشي':'Stuffed vegetables','المقليات':'Fried bites','النودلز':'Noodles','المسخن':'Musakhan','الفتة والموهيتو':'Fattah & Mojito','المخبوزات':'Bakery','المشروبات':'Drinks'};
    return requestedBrandId==='enbishan'?entryText(section,english[section]||section):sourceText(state.products.find(p=>p.category===section),'category');
  }
  function menuRank(product, overview=false) {
    // Keep each section together in the full menu as well as category tabs.
    const section=enbishanSections.indexOf(menuSection(product));
    const grapeOrder=['enbishan-menu-894fad254b67890eafc2c47e','enbishan-menu-c434c397f9072844ba9c93b4','enbishan-menu-6c7a9f521d5f21500c2e6d9a','enbishan-menu-cfe9ad0bccc96e0d0537be28','enbishan-menu-2abc3c6b5efd0180c521eef0'];
    const index=grapeOrder.indexOf(product.id);
    return (section<0?99:section)*100+(index<0?50:index);
  }
  function menuCover(product) {
    if(product.brand_id!=='enbishan')return {image_url:product.image_url};
    const sizes=groups(product).find(g=>g.code==='SIZE')?.values||[];
    const preferred=['عائلي','كبير','وسط'];
    for(const label of preferred){
      const size=sizes.find(v=>String(v.name||'').includes(label));
      const image=size&&(product.variant_images||[]).find(v=>v.options?.SIZE===(size.code||size.id)&&safeImage(v.image_url));
      if(image)return {image_url:image.image_url,label:sourceText(size)};
    }
    return {image_url:product.image_url};
  }
  // Customer-facing copy only; catalog names, options and prices stay authoritative.
  function menuDescription(product,selectedSize=null) {
    if(product.brand_id!=='enbishan')return sourceText(product,'description')||'';
    const sizes=groups(product).find(g=>g.code==='SIZE')?.values||[];
    const pictured=menuCover(product).label;
    const size=selectedSize||sizes.find(v=>sourceText(v)===pictured)||sizes[0];
    const contents=menuSizeContents(product,size);
    if(contents)return contents;
    const name=sourceText(product);
    if(product.name?.startsWith('بوكس رمضان'))return name.split(' · ').slice(1).join(' · ');
    if(product.id==='enbishan-menu-ba943fd11771582e9003ac47')return entryText('اختر ٣ أنواع مختلفة من المحاشي؛ عدد الحبات موضح مع كل اختيار.','Choose 3 different stuffed vegetables; piece counts are shown with each choice.');
    const label=sourceText(size);
    const combined=label.match(/[（(](.*)[)）]/);
    if(combined)return combined[1];
    const count=String(size?.name||'').match(/[0-9٠-٩]+/);
    if(count)return entryText(count[0]+' قطعة '+name,count[0]+' pieces · '+name);
    return name;
  }
  // Piece distributions transcribed from the approved workbook's contents column.
  function menuSizeContents(product,size) {
    if(product.brand_id!=='enbishan')return '';
    const sizes={
      'enbishan-menu-eea4f81998c7705a69c69532':{
        'v-8bc5ff94ef1484666fa0cfde':['٢ ورق عنب + ١ بطاطس + ١ فليفلة + ٢ بصل + ٢ كوسا + ٢ باذنجان','2 grape leaves + 1 potato + 1 pepper + 2 onions + 2 zucchini + 2 eggplants'],
        'v-7ec6c327c235a99f12ee1173':['٦ ورق عنب + ١ بطاطس + ١ فليفلة + ٤ بصل + ٤ كوسا + ٥ باذنجان','6 grape leaves + 1 potato + 1 pepper + 4 onions + 4 zucchini + 5 eggplants']
      },
      'enbishan-menu-b641eb7a3572b133717e64ef':{
        'v-3301de500e7478b4e03b3a73':['٥ ورق عنب + ١ بطاطس + ٢ فليفلة + ٢ بصل + ٢ كوسا + ١ باذنجان + ٣ ملفوف + ٢ كبة','5 grape leaves + 1 potato + 2 peppers + 2 onions + 2 zucchini + 1 eggplant + 3 cabbage rolls + 2 kibbeh']
      },
      'enbishan-menu-7b0752b4daa269674e08576f':{
        'v-b77b6cf14198f496f49c1d3c':['١٢ ورق عنب + ٢ بطاطس + ٢ فليفلة + ٤ بصل + ٥ كوسا + ٤ باذنجان + ٨ ملفوف + ٧ كبة','12 grape leaves + 2 potatoes + 2 peppers + 4 onions + 5 zucchini + 4 eggplants + 8 cabbage rolls + 7 kibbeh']
      }
    };
    const copy=sizes[product.id]?.[size?.code||size?.id];
    return copy?entryText(...copy):'';
  }
  function menuSizeLabel(product,size) {
    return [sourceText(size),menuSizeContents(product,size)].filter(Boolean).join(' — ');
  }

return {section:menuSection,sections:enbishanSections,describe:menuDescription,contents:menuSizeContents,cover:menuCover,text:sourceText,mahashiIds:['enbishan-menu-eea4f81998c7705a69c69532','enbishan-menu-ba943fd11771582e9003ac47']};}};})();
