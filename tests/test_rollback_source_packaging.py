"""Compatibility inventory refuses tampering, path escape and output overwrite."""
import importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rollback_builder',ROOT/'tools/rollback_prepare_compat.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
BASE='b528f14c5ea93e32a15a5c37f43f7e93477136e8'

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


def test_actual_context_retains_all_docker_inputs_and_distinct_prior_app(tmp_path):
 """Catch a new Docker COPY missing from the recovery context before building."""
 import ast,hashlib,shlex,subprocess,sys
 output=tmp_path/'recovery'
 identity=module.assemble(ROOT,ROOT/'tools/rollback_application_overlay',output)
 # No implicit candidate app tree is allowed: prior main stays exactly the
 # reviewed compatibility overlay; an untouched financial module stays baseline.
 assert (output/'app/main.py').read_bytes()==(ROOT/'tools/rollback_application_overlay/app/main.py').read_bytes()
 for name in ('app/orders.py','public/ops.js'):
  assert (output/name).read_bytes()!=(ROOT/name).read_bytes()
 for name in ('accounting','cash_bags','aggregate_driver_closure','aggregate_manager_closure','financial_clarifications','manual_bank_matching','order_duplicates','delivery_partial_returns','tracking_ingest'):
  path='app/'+name+'.py'
  assert (output/path).read_bytes()==module.git(ROOT,'show',BASE+':'+path),path
 # Every local Docker COPY source must exist. A path typo/new tooling input
 # must fail this assertion rather than being replaced by an empty placeholder.
 copy_inputs=[]
 for line in (output/'Dockerfile').read_text().replace('\\\n',' ').splitlines():
  if not line.lstrip().upper().startswith('COPY '):continue
  words=shlex.split(line)
  if not words or words[0].upper()!='COPY' or any(w.startswith('--from=') for w in words[1:]):continue
  paths=[w for w in words[1:] if not w.startswith('--')]
  for source in paths[:-1]:
   assert (output/source).exists(),source
   copy_inputs.append(source)
 assert 'tools/enbishan_workbook.py' in copy_inputs
 tool='tools/enbishan_workbook.py'
 actual=module.git(ROOT,'show',identity['candidate_sha']+':'+tool)
 assert (output/tool).read_bytes()==actual
 assert identity['secure_foundation_files'][tool]==hashlib.sha256(actual).hexdigest()
 # Its import surface remains stdlib only; no missing prior application module
 # or new runtime dependency is loaded by raw inspection/help.
 tree=ast.parse((output/tool).read_text())
 imports=[node.module or '' for node in tree.body if isinstance(node,ast.ImportFrom)]
 assert not any(name.startswith('app') for name in imports)
 help_result=subprocess.run([sys.executable,str(output/tool),'--help'],cwd=output,text=True,capture_output=True,timeout=10)
 assert help_result.returncode==0,help_result.stderr
 assert '--reference-menu' in help_result.stdout
 # Exact prior source/overlay and baseline identity continue to be recorded.
 assert identity['baseline_sha']==BASE
 assert identity['database_restore_performed'] is False
 recorded=json.loads((output/'app/rollback_source_identity.json').read_text())
 assert recorded==identity
