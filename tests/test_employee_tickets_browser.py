"""Employee request UI journeys with synthetic records, independent of production."""
import os
from pathlib import Path
import pytest

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width,lang',[(390,'ar'),(1440,'ar'),(390,'en'),(1440,'en')])
def test_staff_requests_other_details_and_owner_review_fit(width,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        launch={'headless':True,'args':['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><body><h1 id="heading"></h1><main id="content"></main><dialog id="modal"><h2></h2><div id="modal-body"></div></dialog></body></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{font:15px Arial;background:#faf8f5;margin:0;padding:16px}button,input,select,textarea{font:inherit;padding:9px;border:1px solid #ddd;border-radius:7px}button{background:#a64116;color:#fff;cursor:pointer}main{max-width:1100px;margin:auto}dialog{border:1px solid #ddd;border-radius:12px;width:min(680px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;padding:16px}dialog h2{font-size:18px}button.quiet{background:#fff;color:#a64116}')
        page.add_style_tag(path=str(ROOT/'public/assets/employee-tickets.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/employee-tickets.js'))
        page.evaluate('''async lang=>{
          const o=(code,ar,en,extra={})=>({code,ar,en,...extra});
          const reasons=[o('not_working','الخدمة لا تعمل','Service not working'),o('other','سبب آخر','Other reason')];
          const issues=[o('location_issue','مشكلة في الموقع والخرائط','Location and maps issue',{reasons}),o('other','مشكلة أخرى','Other issue',{reasons})];
          const types=[o('technical','مشكلة تقنية','Technical issue',{issues}),o('permission_addition','إضافة صلاحية','Permission addition',{issues:[o('missing_permission','صلاحية غير متاحة','Permission unavailable',{reasons})]}),o('other','أخرى','Other',{issues:[o('other','مشكلة أخرى','Other issue',{reasons})]})];
          window.etRows=[];window.etMutations=[];window.etUser={id:'employee',role:'driver'};
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          window.etOpen=async()=>KPSEmployeeTickets.open({lang,user:window.etUser,active:()=>true,tr:(ar,en)=>lang==='ar'?ar:en,esc,heading:t=>document.querySelector('#heading').textContent=t,
            modal:(title,html,callback)=>{const d=document.querySelector('#modal');d.querySelector('h2').textContent=title;document.querySelector('#modal-body').innerHTML=html;const form=d.querySelector('form');if(form)form.onsubmit=async e=>{e.preventDefault();try{await callback(new FormData(form));}catch(error){form.querySelector('.form-error').textContent=error.message;}};d.showModal();},closeModal:()=>document.querySelector('#modal').close(),
            api:async url=>url.endsWith('/options')?{types,role_profile:o('driver','السائق','Driver')}:url==='/api/employee-tickets'?{tickets:window.etRows}:{ticket:window.etRows.find(t=>url.endsWith('/'+t.id)),history:[{status:'open',note:'TEST recorded request',actor_name:'TEST DRIVER',created_at:'2026-10-23T15:30:00Z',version:1}]},
            mutation:async(url,body)=>{window.etMutations.push({url,body});if(url.endsWith('/review')){Object.assign(window.etRows[0],{status:body.status,version:2});return window.etRows[0];}
              const type=types.find(t=>t.code===body.type),issue=type.issues.find(i=>i.code===body.issue),reason=issue.reasons.find(r=>r.code===body.reason);
              const t={id:'ticket-1',reference:'ET-TEST-001',...body,type_label:type,issue_label:issue,reason_label:reason,requested_by:'employee',requester_name:'TEST DRIVER',created_at:'2026-10-23T15:30:00Z',status:'open',version:1};window.etRows.push(t);return t;}
          });await window.etOpen();
        }''',lang)
        page.locator('[data-et-new]').click()
        form=page.locator('#modal-body form')
        expect(form.locator('[name=issue]')).to_be_disabled()
        form.locator('[name=type]').select_option('technical')
        expect(form.locator('[name=issue] option')).to_have_count(3)
        expect(form.locator('[name=issue]')).not_to_contain_text('missing_permission')
        form.locator('[name=issue]').select_option('location_issue')
        form.locator('[name=reason]').select_option('other')
        assert form.locator('[name=description]').get_attribute('required') is not None
        form.locator('[name=description]').fill('TEST location marker does not follow my position')
        form.locator('button[type=submit]').click()
        expect(page.locator('[data-et-ticket=ticket-1]')).to_contain_text('ET-TEST-001')
        assert page.evaluate('window.etMutations.length')==1
        page.locator('[data-et-detail=ticket-1]').click()
        expect(page.locator('.et-detail')).to_contain_text('مشكلة في الموقع والخرائط' if lang=='ar' else 'Location and maps issue')
        expect(page.locator('.et-detail form')).to_have_count(0)
        page.keyboard.press('Escape')
        page.evaluate("async()=>{window.etUser={id:'owner',role:'owner'};await window.etOpen();}")
        page.locator('[data-et-detail=ticket-1]').click()
        review=page.locator('.et-detail form')
        review.locator('[name=status]').select_option('in_progress')
        review.locator('[name=note]').fill('TEST assigned for technical review')
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'employee-tickets-review-{lang}-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('#modal').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        review.locator('button[type=submit]').click()
        expect(page.locator('[data-et-ticket=ticket-1] .et-status')).to_have_attribute('data-state','in_progress')
        calls=page.evaluate('window.etMutations')
        assert calls[1]['url']=='/api/employee-tickets/ticket-1/review'
        assert calls[1]['body']=={'expected_version':1,'status':'in_progress','note':'TEST assigned for technical review'}
        page.locator('[data-et-filter]').select_option('resolved')
        expect(page.locator('[data-et-ticket]')).to_have_count(0)
        assert not errors
        browser.close()
