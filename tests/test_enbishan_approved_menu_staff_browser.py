"""Actual customer and manager renderers; synthetic catalog and no real writes."""
import json,os
from pathlib import Path
import pytest
from test_delivery_workspace_browser import static_customer
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
ROOT=Path(__file__).resolve().parents[1]
SMALL='enbishan-menu-ba943fd11771582e9003ac47'
LARGE='enbishan-menu-eea4f81998c7705a69c69532'

@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_staff_shared_menu_preserves_size_identity_and_small_choices(static_customer,lang,width):
 from playwright.sync_api import sync_playwright,expect
 products=json.loads((ROOT/'tests/fixtures/enbishan-approved-family.json').read_text())
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.goto(static_customer+'/ops.html')
  page.evaluate('''async ({products,lang})=>{
   window.menuQuoteBodies=[];
   await KPSCustomerOrder.open({user:{id:'synthetic-manager',brand_id:'enbishan',role:'delivery_manager'},branches:[{id:'test-branch',name:'Test branch'}],tr:(ar,en)=>lang==='ar'?ar:en,esc:v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),money:String,
    api:async path=>path==='/api/shifts/current'?{shifts:[{user_id:'synthetic-manager',kind:'manager',status:'open',branch_ids:['test-branch']}]}:path.startsWith('/api/catalog')?{products}:{contacts:[]},
    mutation:async(path,body)=>{if(!path.endsWith('/quote'))throw Error('Real writes are forbidden');window.menuQuoteBodies.push(body);return {quote:{subtotal_halalas:6950,delivery_fee_halalas:0,total_halalas:6950}};},closeModal:()=>{},onCreated:()=>{},
    modal:(title,html,submit)=>{document.querySelector('#modal-body').innerHTML=html;document.querySelector('#modal').showModal();const form=document.querySelector('#staff-order-form');form.addEventListener('submit',e=>{e.preventDefault();submit(new FormData(form),form);});}
   });
  }''',{'products':products,'lang':lang})
  page.locator('[data-menu-category="المحاشي"]').click()
  expect(page.locator('#staff-menu .staff-menu-card')).to_have_count(1)
  expect(page.locator('#staff-menu .staff-menu-description')).to_contain_text('٢ ورق عنب' if lang=='ar' else '2 grape leaves')
  small_size=next(p for p in products if p['id']==SMALL)['option_groups'][0]['values'][0]['code']
  page.locator(f'[data-mahashi-product="{SMALL}"]').click()
  expect(page.locator('#staff-product')).to_have_value(SMALL)
  for index,value in [(1,'potato'),(2,'pepper'),(3,'grape')]:
   page.locator(f'#staff-options input[data-group="{index}"][value="{value}"]').check()
   if index==1:expect(page.locator('#staff-options input[data-group="2"][value="potato"]')).to_be_disabled()
  page.locator('#staff-add').click()
  expect(page.locator('#staff-cart > div')).to_have_count(1)
  large=next(p for p in products if p['id']==LARGE)
  large_size=next(v for v in large['option_groups'][0]['values'] if '21' in v['name'])['code']
  page.locator(f'[data-mahashi-size="{large_size}"]').click()
  expect(page.locator('#staff-product')).to_have_value(LARGE)
  expect(page.locator('#staff-selected-product')).to_contain_text('٦ ورق عنب' if lang=='ar' else '6 grape leaves')
  page.locator('#staff-add').click()
  expect(page.locator('#staff-cart > div')).to_have_count(2)
  expect(page.locator(f'[data-product-count="{LARGE}"]')).to_have_text('2')
  page.locator('[data-menu-category="عنبيشن"]').click()
  expect(page.locator('#staff-menu .staff-menu-card')).to_have_count(2)
  expect(page.locator('#staff-cart > div')).to_have_count(2)
  for name,value in {'phone':'0500000000','neighborhood':'Test area','address':'Test address','lat':'28.4','lng':'45.9'}.items():page.locator(f'#staff-order-form [name="{name}"]').fill(value)
  page.locator('#staff-order-form [type=submit]').click()
  page.wait_for_function('window.menuQuoteBodies.length===1')
  body=page.evaluate('window.menuQuoteBodies[0]')
  assert [item['product_id'] for item in body['items']]==[SMALL,LARGE]
  assert body['items'][0]['units'][0]['options']=={'SIZE':[small_size],'CHOICE_1':['potato'],'CHOICE_2':['pepper'],'CHOICE_3':['grape']}
  assert body['items'][1]['units'][0]['options']=={'SIZE':[large_size]}
  assert not errors
  browser.close()

@pytest.mark.parametrize('lang,width',[('ar',320),('ar',390),('en',390),('en',1366)])
def test_customer_single_mahashi_card_and_signature_banner(static_customer,lang,width):
 from playwright.sync_api import sync_playwright,expect
 products=json.loads((ROOT/'tests/fixtures/enbishan-approved-family.json').read_text())
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
  def api(route):
   key=route.request.url.split('/api/',1)[1].split('?',1)[0]
   responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':products},'branches':{'branches':[],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},'session':{'user':None,'csrf_token':None,'customer_verified':False},'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True}}},'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]}}
   route.fulfill(content_type='application/json',body=json.dumps(responses.get(key,{})))
  page.route('**/api/**',api);page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
  page.locator('html').evaluate("el=>el.dataset.storefrontBrand='enbishan'");page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
  expect(page.locator('#banner-content img')).to_have_attribute('src',next(p for p in products if p['name']=='عنبيشن سيجنتشر')['image_url'])
  expect(page.locator(f'[data-product-id="{SMALL}"]')).to_have_count(0)
  expect(page.locator(f'[data-product-id="{LARGE}"]')).to_have_count(1)
  cards=page.locator('.product-card')
  card=page.locator(f'[data-product-id="{LARGE}"]');card.locator('h3 button').click()
  page.locator('[aria-label="حجم محاشي مشكل"],[aria-label="Mixed mahashi size"]').get_by_role('button').first.click()
  expect(page.locator('#product-description')).to_contain_text('٣ أنواع' if lang=='ar' else '3 different')
  expect(page.locator('#product-price')).to_contain_text('٢٠' if lang=='ar' else '20')
  assert not errors
  browser.close()
