"""Atomic, immutable receipt references allocated only when creating an order."""
from . import service_hours

CITY_CODES = {'حفر الباطن':'HFR', 'حفرالباطن':'HFR', 'الخفجي':'KFJ',
              'القيصومة':'QSM', 'الذيبية':'DHB', 'رفحاء':'RFH', 'رفحا':'RFH',
              'قبه':'QBH', 'قبة':'QBH', 'النعيرية':'NAR', 'الدمام':'DMM',
              'الأرطاوية':'ART', 'الزلفي':'ZLF', 'المجمعة':'MJM',
              'عنيزة':'ONZ', 'حائل':'HIL', 'شعبة نصاب':'SHB'}

NEXT_NUMBER_SQL = ('INSERT INTO order_branch_counters(branch_id,last_number) VALUES(%s,1) '
                   'ON CONFLICT(branch_id) DO UPDATE SET last_number=order_branch_counters.last_number+1 '
                   'RETURNING last_number')


def reference_prefix(branch, moment=None):
    day = service_hours.delivery_operational_date(moment)
    # Brand identity is explicit order metadata, never a different number format.
    # This only allocates future references; saved historical strings stay immutable.
    return CITY_CODES.get(branch['city'], 'BR') + str(branch['branch_no']) + f'-{day:%y%m%d}-'


def allocate(conn, branch_id, moment=None):
    branch = conn.execute('SELECT city,branch_no,brand_id FROM branches WHERE id=%s', (branch_id,)).fetchone()
    prefix = reference_prefix(branch, moment)
    seq = conn.execute(NEXT_NUMBER_SQL, (branch_id,)).fetchone()['last_number']
    return f'{prefix}{seq:04d}'
