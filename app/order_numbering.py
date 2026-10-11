"""Atomic, immutable receipt references allocated only when creating an order."""
from datetime import datetime
from zoneinfo import ZoneInfo

CITY_CODES = {'حفر الباطن':'HFR', 'حفرالباطن':'HFR', 'الخفجي':'KFJ',
              'القيصومة':'QSM', 'الذيبية':'DHB', 'رفحاء':'RFH', 'رفحا':'RFH',
              'قبه':'QBH', 'قبة':'QBH', 'النعيرية':'NAR', 'الدمام':'DMM',
              'الأرطاوية':'ART', 'الزلفي':'ZLF', 'المجمعة':'MJM',
              'عنيزة':'ONZ', 'حائل':'HIL', 'شعبة نصاب':'SHB'}

def allocate(conn, branch_id, moment=None):
    day = (moment or datetime.now(ZoneInfo('Asia/Riyadh'))).astimezone(ZoneInfo('Asia/Riyadh')).date()
    branch = conn.execute('SELECT city,branch_no FROM branches WHERE id=%s', (branch_id,)).fetchone()
    seq = conn.execute('INSERT INTO order_daily_counters(branch_id,receipt_date,last_number) VALUES(%s,%s,1) '
                       'ON CONFLICT(branch_id,receipt_date) DO UPDATE SET last_number=order_daily_counters.last_number+1 '
                       'RETURNING last_number', (branch_id,day)).fetchone()['last_number']
    prefix = CITY_CODES.get(branch['city'], 'BR') + str(branch['branch_no'])
    return f'{prefix}-{day:%y%m%d}-{seq:04d}'
