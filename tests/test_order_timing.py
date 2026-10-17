"""Source math contracts for the additive read-only timing screen."""
from pathlib import Path
import subprocess


def test_order_timing_math_and_readonly_ui():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(['node','tests/test_order_timing.cjs'],cwd=root,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    source=(root/'public/assets/order-timing.js').read_text()
    assert "api('/api/orders?'+q)" in source
    assert "q.set('manager_view'" in source
    assert 'mutation(' not in source
    assert '.slice(' not in source
    assert 'root.querySelector(\'.timing-screen\')!==screen' in source
