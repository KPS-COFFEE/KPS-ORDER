"""The central branch projection must preserve scoped, verified map pins."""
import json
from pathlib import Path
import subprocess
import pytest
from app import central_operations as central


def row(identifier='visible', brand='kps', **settings):
    return {'id':identifier,'brand_id':brand,'branch_no':1,'name':'Branch','city':'حفر الباطن',
            'settings':{'location':{'lat':28.4,'lng':45.9,'private_note':'never publish'},
                        'location_verified':True,'private_setting':'never publish',**settings}}


class Connection:
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def execute(self,sql,args):
        self.calls.append((sql,args));self.result=[r for r in self.rows if r['brand_id']==args[0] and (args[1] or r['id'] in args[2])]
        return self
    def fetchall(self):return self.result


def context(monkeypatch,rows,grants):
    monkeypatch.setattr(central,'enabled',lambda:True)
    monkeypatch.setattr(central.multibrand,'available',lambda c,u:[{'id':k,'name':k} for k in grants])
    monkeypatch.setattr(central.multibrand,'project',lambda c,u,b:None if grants[b] is None else {'id':'staff','role':'delivery_manager','branch_ids':grants[b]})
    conn=Connection(rows)
    result=central.context(conn,{'id':'staff','role':'delivery_manager'})
    assert all('AND (%s OR id=ANY(%s))' in sql for sql,args in conn.calls)
    return result


def test_granted_verified_branch_reaches_actual_map_pin_contract(monkeypatch):
    result=context(monkeypatch,[row(),row('hidden'),row('other-brand','other')],{'kps':['visible']})
    branches=result['brands'][0]['branches']
    assert [b['id'] for b in branches]==['visible']
    assert branches[0]['location']=={'lat':28.4,'lng':45.9}
    assert branches[0]['location_verified'] is True
    assert 'settings' not in branches[0]
    assert 'private' not in json.dumps(result)
    program="""const fs=require('node:fs'),assert=require('node:assert/strict');global.window={};
require('./public/assets/dispatch-map.js');
const branches=JSON.parse(fs.readFileSync(0,'utf8'));
const pins=window.KPSDispatchMap.pins({branches});assert.equal(pins.length,1);
assert.equal(pins[0].kind,'branch');assert.equal(pins[0].id,'visible');"""
    subprocess.run(['node','-e',program],input=json.dumps(branches),text=True,
                   cwd=Path(__file__).resolve().parents[1],check=True)


@pytest.mark.parametrize('settings',[
    {'location_verified':False}, {'location':None}, {'location':{'lat':999,'lng':45}},
    {'location':{'lat':True,'lng':45}}, {'location':{'lat':28,'lng':float('nan')}},
])
def test_unverified_or_invalid_location_never_becomes_a_map_pin(monkeypatch,settings):
    result=context(monkeypatch,[row(**settings)],{'kps':['visible']})
    branch=result['brands'][0]['branches'][0]
    assert branch['location'] is None and branch['location_verified'] is False


def test_independent_brand_membership_and_branch_revocation(monkeypatch):
    rows=[row(),row('enb-visible','enbishan'),row('enb-hidden','enbishan')]
    result=context(monkeypatch,rows,{'kps':['visible'],'enbishan':['enb-visible']})
    assert [[r['id'] for r in b['branches']] for b in result['brands']]==[['visible'],['enb-visible']]
    result=context(monkeypatch,rows,{'kps':['visible'],'enbishan':None})
    assert [b['id'] for b in result['brands']]==['kps']
    result=context(monkeypatch,rows,{'kps':[]})
    assert result['brands'][0]['branches']==[]


def test_customers_and_disabled_rollout_never_receive_pins(monkeypatch):
    monkeypatch.setattr(central,'enabled',lambda:True)
    assert central.context(None,{'role':'customer'}) is None
    monkeypatch.setattr(central,'enabled',lambda:False)
    assert central.context(None,{'role':'owner'}) is None
