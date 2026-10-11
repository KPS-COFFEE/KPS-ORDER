"""Atomic, immutable receipt references allocated only when creating an order."""
from . import service_hours

CITY_CODES = {'حفر الباطن':'HFR', 'حفرالباطن':'HFR', 'الخفجي':'KFJ',
              'القيصومة':'QSM', 'الذيبية':'DHB', 'رفحاء':'RFH', 'رفحا':'RFH',
              'قبه':'QBH', 'قبة':'QBH', 'النعيرية':'NAR', 'الدمام':'DMM',
              'الأرطاوية':'ART', 'الزلفي':'ZLF', 'المجمعة':'MJM',
              'عنيزة':'ONZ', 'حائل':'HIL', 'شعبة نصاب':'SHB'}

def allocate(conn, branch_id, moment=None):
    day = service_hours.delivery_operational_date(moment)
    branch = conn.execute('SELECT city,branch_no FROM branches WHERE id=%s', (branch_id,)).fetchone()
    seq = conn.execute('INSERT INTO order_branch_counters(branch_id,last_number) VALUES(%s,1) '
                       'ON CONFLICT(branch_id) DO UPDATE SET last_number=order_branch_counters.last_number+1 '
                       'RETURNING last_number', (branch_id,)).fetchone()['last_number']
    prefix = CITY_CODES.get(branch['city'], 'BR') + str(branch['branch_no'])
    return f'{prefix}-{day:%y%m%d}-{seq:04d}'
