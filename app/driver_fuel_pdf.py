"""Arabic/English fuel export from the same authorized ledger snapshot as UI."""
import io
import re
from pathlib import Path
from decimal import Decimal
from datetime import datetime


def render(data, lang='ar'):
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Flowable, Spacer, LongTable, TableStyle
    from . import service_hours

    ar = lang == 'ar'
    t = lambda arabic, english: arabic if ar else english
    font = 'KPSFuelReport'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).resolve().parents[1] /
                                                'public/assets/fonts/NotoKufiArabic-Regular.ttf')))
    def visual(value):
        # Keep ISO dates in their logical order inside mixed Arabic captions.
        text = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', lambda match: '\u200e'+match[0]+'\u200e', str(value))
        return get_display(arabic_reshaper.reshape(text), base_dir='R' if ar else 'L').replace('\u200e', '')
    navy, muted = colors.HexColor('#173c48'), colors.HexColor('#526471')

    class Text(Flowable):
        """Wrap logical Arabic before shaping, including unbroken staff names."""
        def __init__(self, value, size=8, color=navy):
            Flowable.__init__(self)
            self.value, self.size, self.color = str(value), size, color
            self.leading = size + 5

        def wrap(self, width, height):
            self.width, self.lines = width, []
            measure = lambda text: pdfmetrics.stringWidth(visual(text), font, self.size)
            for paragraph in self.value.split('\n'):
                line = ''
                for word in paragraph.split():
                    candidate = (line+' '+word).strip()
                    if line and measure(candidate) > width:
                        self.lines.append(line)
                        line = ''
                    if measure(word) > width:
                        for ch in word:
                            if line and measure(line+ch) > width:
                                self.lines.append(line)
                                line = ''
                            line += ch
                    else:
                        line = (line+' '+word).strip()
                self.lines.append(line)
            self.height = len(self.lines)*self.leading
            return width, self.height

        def split(self, width, height):
            self.wrap(width, height)
            count = int(height//self.leading)
            if count < 1 or count >= len(self.lines):
                return []
            return [Text('\n'.join(self.lines[:count]), self.size, self.color),
                    Text('\n'.join(self.lines[count:]), self.size, self.color)]

        def draw(self):
            self.canv.setFont(font, self.size)
            self.canv.setFillColor(self.color)
            for i, line in enumerate(self.lines):
                y = self.height-(i+1)*self.leading+4
                if ar:
                    self.canv.drawRightString(self.width, y, visual(line))
                else:
                    self.canv.drawString(0, y, visual(line))

    def number(value):
        return '-' if value is None else f'{Decimal(str(value)):,.2f}'.rstrip('0').rstrip('.')

    def money(value):
        return '-' if value is None else f'{Decimal(value)/100:,.2f}'

    filters, period, summary = data['filters'], data['period'], data['summary']
    title = t('البنزين والمسافات حسب السائق', 'Driver fuel and distance')
    selected = [t('السائق: ', 'Driver: ')+(filters['driver'] or t('محدد دون سجلات', 'Selected; no records')
                if filters['driver_selected'] else t('كل السائقين', 'All drivers')),
                t('السيارة: ', 'Vehicle: ')+(filters['vehicle'] or t('محددة دون سجلات', 'Selected; no records')
                if filters['vehicle_selected'] else t('كل السيارات', 'All vehicles')),
                t('المدينة: ', 'City: ')+(filters['city'] or t('كل المدن', 'All cities'))]
    story = [Text(title, 18), Spacer(1, 8),
             Text(t('من: ', 'From: ')+str(period['start'])+' | '+t('إلى: ', 'To: ')+str(period['end'])+' | '+t('البراند: ', 'Brand: ')+filters['brand'], 10),
             Text(' | '.join(selected), 9, muted), Spacer(1, 12)]
    cards = [t('المسافة المسجلة كم', 'Recorded distance km')+'\n'+number(summary['distance_km']),
             t('البنزين المسجل لتر', 'Recorded fuel L')+'\n'+number(summary['liters']),
             t('قيمة البنزين المعروفة ر.س', 'Known fuel amount SAR')+'\n'+money(summary['fuel_amount_known_halalas']),
             t('السجلات / السائقون', 'Records / drivers')+'\n'+str(summary['records'])+' / '+str(summary['drivers'])]
    available = landscape(A4)[0]-48
    cards = [Text(value, 10) for value in cards]
    if ar:
        cards.reverse()
    card_table = LongTable([cards], colWidths=[available/4]*4)
    card_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#edf5f6')),
                                    ('BOX', (0, 0), (-1, -1), .6, colors.HexColor('#cfdee2')),
                                    ('TOPPADDING', (0, 0), (-1, -1), 10),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                                    ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [card_table, Spacer(1, 10)]
    if summary['fuel_amount_halalas'] is None or summary['distance_missing_records']:
        story += [Text(t('الإجماليات غير مكتملة عند غياب قراءة أو مبلغ تعبئة. غير المسجل لا يعامل كصفر أو عجز على السائق.',
                         'Totals are incomplete where readings or fuel amounts are missing. Missing values are not zero or driver shortages.'),
                       9, colors.HexColor('#9d301d')), Spacer(1, 8)]
    headers = [t('اليوم التشغيلي', 'Operating day'), t('السائق', 'Driver'), t('السيارة', 'Vehicle'),
               t('المدينة', 'City'), t('عداد البداية', 'Start km'), t('عداد النهاية', 'End km'),
               t('المسافة كم', 'Distance km'), t('لتر البنزين', 'Fuel L'), t('القيمة ر.س', 'Amount SAR'),
               t('الحالة', 'Status')]
    values, missing_cells = [headers], []
    states = {'closed': t('مغلق', 'Closed'), 'open': t('مفتوح', 'Open'), 'exception': t('استثناء', 'Exception')}
    for index, row in enumerate(data['rows'], 1):
        values.append([str(row['operating_day']), row['driver_name'], row['plate'], ' / '.join(row['cities']),
                       number(row.get('start_odometer')), number(row.get('end_odometer')),
                       number(row.get('actual_distance')),
                       '-' if row['fuel_record_missing'] else number(row.get('liters')),
                       money(row.get('fuel_amount_halalas')), states.get(row['record_status'], row['record_status'])])
        for col in range(4, 9):
            if values[-1][col] == '-':
                missing_cells.append((9-col if ar else col, index))
    if not data['rows']:
        values.append([t('لا توجد سجلات للفلاتر المختارة', 'No records for selected filters'), *['']*9])
    table_rows = [[Text(value, 8, colors.white if i == 0 else navy) for value in row]
                  for i, row in enumerate(values)]
    weights = [1.2, 1.8, 1.15, 1.45, .85, .85, .85, .8, .9, .8]
    if ar:
        table_rows = [list(reversed(row)) for row in table_rows]
        weights.reverse()
    if not data['rows']:
        table_rows[1] = [Text(t('لا توجد سجلات للفلاتر المختارة', 'No records for selected filters')),
                         *[Text('') for _ in range(9)]]
    table = LongTable(table_rows, colWidths=[available*w/sum(weights) for w in weights],
                      repeatRows=1, splitByRow=1, splitInRow=1)
    commands = [('BACKGROUND', (0, 0), (-1, 0), navy), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f7f8')]),
                ('LINEBELOW', (0, 0), (-1, -1), .35, colors.HexColor('#dce5e8')),
                ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]
    if not data['rows']:
        commands.append(('SPAN', (0, 1), (-1, 1)))
    for col, row in missing_cells:
        commands.append(('BACKGROUND', (col, row), (col, row), colors.HexColor('#fff0ed')))
    table.setStyle(TableStyle(commands))
    story += [table, Spacer(1, 10), Text(data['note'], 8, muted)]
    out = io.BytesIO()
    generated = datetime.now(service_hours.RIYADH).strftime('%Y-%m-%d %H:%M')
    def footer(canvas, doc):
        canvas.setFont(font, 8)
        canvas.setFillColor(muted)
        canvas.drawString(24, 16, f'KPS ORDER | {generated}')
        canvas.drawRightString(landscape(A4)[0]-24, 16, str(doc.page))
    SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=24, rightMargin=24,
                      topMargin=24, bottomMargin=32, title=title).build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
