"""Execute report close wiring independently of the full authenticated app."""
import json
from pathlib import Path
import subprocess
import pytest

OPS=(Path(__file__).resolve().parents[1]/'public'/'ops.js').read_text()

@pytest.mark.parametrize('period',['morning','evening'])
def test_manager_end_report_keeps_historical_operational_context(period):
    source=OPS[OPS.index('  async function managerEndDutyReport'):OPS.index('  async function sharedManagerClosure')]
    shift={'id':'historic','kind':'manager','user_id':'me','delivery_period':period,'delivery_operational_date':'2026-10-25'}
    script='''const state={user:{id:'me'}};const api=async()=>{throw Error('unexpected API')};const tr=(a,e)=>e;const $=()=>({});const modal=(...a)=>{};const window={KPSOperatingReports:{mount:async(host,options)=>console.log(JSON.stringify(options))}};'''+source+'\nmanagerEndDutyReport('+json.dumps(shift)+');'
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
    options=json.loads(result.stdout)
    assert options['initialDay']=='2026-10-25'
    assert options['managerShiftId']=='historic'
    assert options['morning']==(period=='morning')
    assert options['allowDay']==(period!='morning')
    assert options['lockContext'] is True


def test_all_manager_close_paths_show_report():
    for endpoint in ('/vault-close','/manager-physical-submit','/approve`'):
        begin=OPS.index(endpoint)
        assert 'managerEndDutyReport(s)' in OPS[begin:begin+500]
    assert "classList.remove('cash-bags-dialog','operating-report-dialog')" in OPS


def test_cash_bag_reference_is_isolated_and_escaped_once():
    source=OPS[OPS.index('  function cashBagCard'):OPS.index('  async function cashBagInbox')]
    script='''const tr=(a,e)=>e;const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const money=String;const datetime=String;'''+source+'''console.log(cashBagCard({id:'1',driver_name:'Driver',reference:'A<B',branch_name:'Branch',destination:'branch',shift_date:'2026-10-25',deposited_at:'2026-10-26',declared_cash_halalas:123,declared_card_halalas:456}));'''
    rendered=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True).stdout
    assert '<bdi>A&lt;B</bdi>' in rendered
    assert '&lt;bdi&gt;' not in rendered
    assert rendered.count('<dt>')==7


@pytest.mark.parametrize('has_receipt',[False,True])
def test_remote_legacy_closure_requires_real_receipt_before_final_approval(has_receipt):
    source=OPS[OPS.index('  async function sharedDriverClosure'):OPS.index('  async function managerEndDutyReport')]
    data={'shift':{'id':'shift','user_id':'driver','status':'submitted'},'declaration':{'id':'dec','cash_halalas':100,'card_halalas':200},'cash_bag':None,'receipt':{'id':'receipt','received_halalas':100} if has_receipt else None,'manager_approval_required':True,'manager_approval':None,'can_approve':True}
    script='''const state={user:{id:'manager',role:'delivery_manager'}};const tr=(a,e)=>e;const esc=String;const money=String;const driverOrderReviewControl=()=>'';const dataItem=(l,v)=>l+': '+v;const notice=String;const amountField=(l,n)=>`<input name="${n}">${l}`;const field=amountField;const check=amountField;const formEnd=l=>`<button>${l}</button>`;const bindSharedClosureDirty=()=>{};let html,submit,saved;const modal=(title,body,callback)=>{html=body;submit=callback;};const closeModal=()=>{};const loadPage=async()=>{};const mutation=async(path,body)=>saved={path,body};const api=async()=>('''+json.dumps(data)+');\n'+source+'''\n(async()=>{await sharedDriverClosure('shift');await submit(new Map([['confirmed',true],['received','1.00'],['reference','actual']]));console.log(JSON.stringify({html,saved}));})();'''
    # The physical receipt path parses amounts; approval posts no amount.
    script="const cents=x=>Math.round(Number(x)*100);"+script
    result=json.loads(subprocess.run(['node','-e',script],capture_output=True,text=True,check=True).stdout)
    if has_receipt:
        assert 'Actual cash receipt is recorded' in result['html']
        assert 'name="received"' not in result['html']
        assert result['saved']=={'path':'/api/central-operations/workdays/shift/physical-approval','body':{'declaration_id':'dec','confirmed':True}}
    else:
        assert 'name="received"' in result['html']
        assert result['saved']['path'].endswith('/physical-receipt')
        assert result['saved']['body']['received_halalas']==100
