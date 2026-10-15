"""Public bilingual presentation; no identity or operational writes."""
from pathlib import Path
from html.parser import HTMLParser
import hashlib
import tarfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import customer_legal
from app.customer_policy_content import CONTROLLER, CR, VAT, SECTIONS

ROOT = Path(__file__).resolve().parents[1]
URLS = ['https://wa.me/966550472662','https://instagram.com/kps.coffe','https://www.snapchat.com/add/Kps.cofee','https://www.tiktok.com/@Kps.cofee']

class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__();self.tags=[];self.text=[];self.feed(text)
    def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))
    def handle_data(self,data): self.text.append(data)

@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('kind',['privacy','terms','refunds'])
def test_policies_public_controller_only_in_privacy_and_no_db(monkeypatch,lang,kind):
    def forbidden(*args,**kwargs): raise AssertionError('Public presentation must not read/write operational records')
    monkeypatch.setattr(customer_legal.db,'tx',forbidden)
    app=FastAPI();app.include_router(customer_legal.router)
    r=TestClient(app).get(f'/{kind}?lang={lang}')
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    m=Markup(r.text)
    assert f'lang="{lang}"' in r.text and f'dir="{"rtl" if lang=="ar" else "ltr"}"' in r.text
    assert r.text.count(CONTROLLER)==(1 if kind=='privacy' else 0)
    if kind=='privacy':
        assert CONTROLLER in r.text.split('id="data-controller"')[1].split('</section>')[0]
        assert CR in r.text and '15-10-2026' in r.text
        assert all(word not in r.text for word in ('Render','Frankfurt','Germany','فرانكفورت','ألمانيا'))
        assert ('outside Saudi Arabia' if lang=='en' else 'خارج المملكة العربية السعودية') in r.text
        assert ('Technology service providers may be used as needed' if lang=='en' else 'تتم الاستعانة بمزودي خدمات تقنية عند الحاجة') in r.text
    assert 'KPS COFFEE' in r.text and 'كبز قهوة مختصة' in r.text
    assert '2026-10-15' in r.text and 'mailto:' not in r.text
    assert not any(tag in ('script','input') for tag,_ in m.tags)
    assert len([1 for tag,_ in m.tags if tag=='section'])==len(SECTIONS[kind])+(kind=='privacy')

def test_exact_social_destinations_labels_and_no_visible_handles():
    s=(ROOT/'public/index.html').read_text();m=Markup(s)
    assert CONTROLLER not in s
    for url in URLS:
        links=[a for tag,a in m.tags if tag=='a' and a.get('href')==url]
        assert len(links)==2
        for a in links:
            assert a['target']=='_blank' and set(a['rel'].split())=={'noopener','noreferrer'}
            assert a['aria-label'] and a['title'] and a['data-i18n-aria-label'] and a['data-i18n-title']
    visible=' '.join(m.text)
    assert '@kps.coffe' not in visible and '@Kps.cofee' not in visible
    assert 'mailto:' not in s
    for name in ('whatsapp','instagram','snapchat','tiktok'):
        asset=(ROOT/f'public/assets/social/{name}.svg').read_text()
        assert '<svg' in asset and '<script' not in asset and 'onload=' not in asset

def test_exact_baseline_and_business_values_preserved():
    archive=ROOT/'docs/legal-presentation/baseline-c37d2d8.tar'
    with tarfile.open(archive) as tf:
        names=set(tf.getnames())
        for file in ('app/customer_legal.py','public/index.html','public/customer.js','public/customer.css','public/style.css'):
            assert file in names and tf.extractfile(file).read()
    from app.invoice_profiles import defaults
    assert defaults("kps")["entities"]["reem"]["vat_number"]==VAT
    assert VAT=='311887526600003' and CR=='7037181620'

@pytest.mark.postgres
@pytest.mark.parametrize('lang',['ar','en'])
def test_full_app_public_pages_do_not_change_records_or_settings(client,lang):
    from app import db
    def snapshot():
        with db.tx() as c:
            tables=[r['table_name'] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchall()]
            from psycopg import sql
            return {name:c.execute(sql.SQL('SELECT count(*) AS n, md5(coalesce(string_agg(row_to_json(t)::text,\'\' ORDER BY row_to_json(t)::text),\'\')) AS digest FROM {} t').format(sql.Identifier(name))).fetchone() for name in tables}
    before=snapshot()
    for kind in ('privacy','terms','refunds'):
        assert client.get(f'/{kind}?lang={lang}').status_code==200
    assert client.get('/').status_code==200
    assert client.get('/api/customer/profile').status_code==401
    assert client.put('/api/customer/legal',json={'controller_name':'invalid'}).status_code==401
    assert before==snapshot()
