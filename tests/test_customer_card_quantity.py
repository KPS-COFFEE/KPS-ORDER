"""Execute behavior checks against the functions shipped in customer.js."""
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize('script', ['test_customer_card_quantity.cjs', 'test_customer_cart_minimum.cjs'])
def test_shipped_customer_draft_controls(script):
    subprocess.run(['node', 'tests/' + script], cwd=Path(__file__).resolve().parents[1], check=True)
