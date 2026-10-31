"""Synthetic DOM acceptance: production thread rendering, polling, images and close.

HTTP responses are fixtures; real authorization is covered by PostgreSQL tests.
"""
import base64
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'public/ops.js').read_text()
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit synthetic browser acceptance')


def between(start,end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end,SOURCE.index(start))]


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('origin',['chat','ownerDashboard'])
def test_thread_window_images_live_draft_and_close(width,origin,tmp_path):
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
    with sync_playwright() as pw:
        options={'headless':True,'args':['--no-sandbox']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):options['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=pw.chromium.launch(**options)
        page=browser.new_page(viewport={'width':width,'height':950})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def serve(route):
            if urlsplit(route.request.url).path.startswith('/api/chat-attachments/'):
                return route.fulfill(body=png,content_type='image/png')
            return route.fulfill(body='<!doctype html><html dir="rtl"><body><div id="content"><div class="chat-layout inbox-only"><div class="conversation-list"></div><section id="chat-detail"></section></div></div><dialog id="modal"><h2 id="modal-title"></h2><button id="modal-close">Close</button><div id="modal-body"></div></dialog></body></html>',content_type='text/html')
        page.route('**/*',serve);page.goto('https://qa.invalid/followup')
        page.add_style_tag(content=(ROOT/'public/ops.css').read_text()+(ROOT/'public/assets/followup-cards.css').read_text())
        page.add_script_tag(content='''
            const $=(s,root=document)=>root.querySelector(s);
            const state={page:'chat',user:{id:'staff'},chatHistory:new Map(),conversations:[]};
            let conversationVersion=0,printerRefresh=null;const trialMode=false;
            const tr=(ar,en)=>en,esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            const safeHref=s=>esc(s),datetime=s=>s||'',can=()=>true;
            const hideUnavailableActions=()=>{},bindFuelSubmission=()=>{},selectDispatchPanel=()=>{};
            const refreshOperationalAlerts=async()=>{},table=()=>'',setStatus=()=>{};
            window.KPSSaudiPlate={bindInputs:()=>{}};
            window.reads=[];window.fixtureMessages=[{id:'1',seq:1,text:'Saved image',sender_id:'customer',attachment_url:'/api/chat-attachments/image?context=work'}];
            async function api(path,options={}){if(path.endsWith('/read')){reads.push(options.body);return {}};const after=Number(new URL(path,location.href).searchParams.get('after')||0);return {conversation:{id:'c1',order_reference:'ORDER-1',can_reply:true,can_manage:true,route:'delivery'},messages:fixtureMessages.filter(m=>m.seq>after)};}
            async function mutation(path,body){fixtureMessages.push({id:String(fixtureMessages.length+1),seq:fixtureMessages.length+1,sender_id:'staff',text:body.text});}
        '''+between('  let conversationHome=null;','  function formEnd(')+between('  function formEnd(','  const incentiveStateName=')+between('  function conversationLabel(','  async function customerMessageRouting')+between('  function messageMarkup(','  async function editDriverTerminal')+between('  let conversationPolling=false;','  async function imageUpload(')+"\n$('#modal-close').addEventListener('click',closeModal);$('#modal').addEventListener('close',()=>{if(!$('#modal').open)restoreConversationWindow();});")
        page.evaluate('(origin)=>{state.page=origin;if(origin!=="chat")document.querySelector(".chat-layout").remove();}',origin)
        page.evaluate('conversation("c1")')
        expect(page.locator('#modal')).to_be_visible()
        expect(page.locator('#modal #messages')).to_contain_text('Saved image')
        expect(page.locator('#messages img')).to_have_js_property('naturalWidth',1)
        page.locator('#message-form textarea').fill('Unsent draft')
        page.evaluate('window.originalComposer=document.querySelector("#message-form");fixtureMessages.push({id:"2",seq:2,text:"Live reply",sender_id:"customer"});pollConversation()')
        expect(page.locator('#messages')).to_contain_text('Live reply')
        expect(page.locator('#message-form textarea')).to_have_value('Unsent draft')
        assert page.evaluate('document.querySelector("#message-form")===originalComposer')
        assert page.evaluate('document.querySelector("#modal").scrollWidth<=document.querySelector("#modal").clientWidth')
        page.screenshot(path=str(tmp_path/f'conversation-{origin}-{width}.png'))
        page.locator('#modal-close').click();expect(page.locator('#modal')).not_to_be_visible()
        assert page.evaluate('state.page')==origin
        if origin=='chat':expect(page.locator('.chat-layout')).to_have_class('chat-layout inbox-only')
        else:assert page.locator('#chat-detail').count()==0
        assert not errors,errors
        browser.close()
