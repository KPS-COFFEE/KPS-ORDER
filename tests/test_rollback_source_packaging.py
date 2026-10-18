"""Compatibility inventory refuses tampering, path escape and output overwrite."""
import importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rollback_builder',ROOT/'tools/rollback_prepare_compat.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
BASE='80a1d727f6695c4ce3685e726ab5b34674e1cfa0'

@pytest.mark.parametrize('name',['../outside.py','/tmp/outside.py','tools/outside.py'])
def test_inventory_rejects_paths_outside_application_inputs(tmp_path,name):
 overlay=tmp_path/'overlay';overlay.mkdir()
 (overlay/'manifest.json').write_text(json.dumps({'baseline_sha':BASE,'files':{name:'0'*64},'fallbacks':[]}))
 with pytest.raises(ValueError,match='Compatibility path'):
  module.assemble(ROOT,overlay,tmp_path/'output')
 assert not (tmp_path/'output').exists()


def test_inventory_hash_mismatch_fails_before_output_creation(tmp_path):
 overlay=tmp_path/'overlay';(overlay/'app').mkdir(parents=True)
 (overlay/'app/orders.py').write_text('unapproved code')
 (overlay/'manifest.json').write_text(json.dumps({'baseline_sha':BASE,'files':{'app/orders.py':'0'*64},'fallbacks':[]}))
 with pytest.raises(ValueError,match='hash mismatch'):
  module.assemble(ROOT,overlay,tmp_path/'output')
 assert not (tmp_path/'output').exists()


def test_source_builder_never_overwrites_existing_directory(tmp_path):
 overlay=tmp_path/'overlay';overlay.mkdir()
 (overlay/'manifest.json').write_text(json.dumps({'baseline_sha':BASE,'files':{'app/orders.py':'0'*64},'fallbacks':[]}))
 output=tmp_path/'output';output.mkdir();(output/'keep').write_text('preserved')
 with pytest.raises(ValueError,match='new empty'):
  module.assemble(ROOT,overlay,output)
 assert (output/'keep').read_text()=='preserved'
