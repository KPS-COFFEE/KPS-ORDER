"""Execute the production dashboard ordering functions against mixed order states."""
import json
from pathlib import Path
import re
import subprocess


def test_dispatch_orders_newest_active_first_completed_last():
    source = (Path(__file__).parents[1] / 'public/ops.js').read_text()
    functions = '\n'.join(re.search(r'  function '+name+r'\([^\n]*\)\{.*?\n  \}', source, re.S).group(0)
                          for name in ('dispatchStage', 'sortDispatchOrders'))
    orders = [
        {'id': 'done-new', 'delivery_status': 'delivered', 'created_at': '2026-10-20T18:00:00Z'},
        {'id': 'active-old', 'delivery_status': 'picked_up', 'created_at': '2026-10-20T14:00:00Z'},
        {'id': 'cancelled-new', 'preparation_status': 'cancelled', 'created_at': '2026-10-20T19:00:00Z'},
        {'id': 'active-new', 'preparation_status': 'new', 'created_at': '2026-10-20T17:00:00Z'},
        {'id': 'rejected', 'preparation_status': 'rejected', 'created_at': '2026-10-20T16:00:00Z'},
        {'id': 'completed', 'completed_at': '2026-10-20T16:00:00Z', 'created_at': '2026-10-20T15:00:00Z'},
        {'id': 'problem', 'delivery_status': 'problem', 'created_at': '2026-10-20T16:00:00Z'},
        {'id': 'missing-date', 'delivery_status': 'assigned'},
    ]
    script = functions + '\nconst orders='+json.dumps(orders)+''';
const before=JSON.stringify(orders);
const all=sortDispatchOrders(orders).map(o=>o.id);
const done=sortDispatchOrders(orders.filter(o=>dispatchStage(o)==='delivered')).map(o=>o.id);
console.log(JSON.stringify({all,done,unchanged:JSON.stringify(orders)===before}));
'''
    result = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert result['all'] == ['active-new', 'problem', 'active-old', 'missing-date',
                             'cancelled-new', 'done-new', 'rejected', 'completed']
    assert result['done'] == ['done-new', 'completed']
    assert result['unchanged'] is True
