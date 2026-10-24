"""Execute actual map pin projection: timestamps never turn stale GPS into live data."""
import subprocess
from pathlib import Path


def test_driver_map_pins_fresh_stale_invalid_and_expired():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['node', '-e', r'''
const assert=require('node:assert/strict');global.window={};
require('./public/assets/dispatch-map.js');
const now=Date.now();
const driver=(id,age,extra={})=>({id,display_name:'Driver '+id,gps:{location:{lat:28.4,lng:45.9},updated_at:new Date(now-age).toISOString(),accuracy_m:12,...extra}});
const values=window.KPSDispatchMap.pins({drivers:[driver('fresh',1000),driver('stale',180000),driver('expired',1000000),driver('invalid',1000,{location:{lat:NaN,lng:1}}),{id:'missing'}],branches:[],orders:[]});
assert.equal(values.length,2);assert.equal(values[0].kind,'driver');assert.equal(values[0].stale,false);assert.equal(values[1].stale,true);assert.equal(values[0].key,'driver:fresh');
'''], cwd=root, check=True)
