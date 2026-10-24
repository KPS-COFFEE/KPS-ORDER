"""Client-only plate formatting cannot mutate fleet/history identities."""
from pathlib import Path
import subprocess


def test_saudi_private_plate_contracts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', 'tests/test_saudi_plate.cjs'], cwd=root,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
