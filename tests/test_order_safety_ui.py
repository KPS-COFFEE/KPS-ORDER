"""Run actual extracted operational UI handlers with isolated Node API mocks."""
import subprocess
from pathlib import Path


def test_order_safety_operational_ui_handlers():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ['node', '--test', 'tests/test_order_safety_ui.cjs'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
