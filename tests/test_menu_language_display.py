"""Collect the offline shipped-JavaScript language contract in the full CI suite."""
import shutil
import subprocess
from pathlib import Path


def test_menu_language_display_contract():
    node = shutil.which('node')
    assert node, 'Node is required for the menu language acceptance contract.'
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, '--test', 'tests/test_menu_language_display.cjs'],
                            cwd=root, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
