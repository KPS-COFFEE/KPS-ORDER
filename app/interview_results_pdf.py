"""Printable committee ranking from the same graded results as the screen."""
from io import BytesIO
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, LongTable, TableStyle


def render(rows, preview=False):
    font = 'KPSInterviewBold'
    if font not in pdfmetrics.getRegisteredFontNames():
        path = Path(__file__).resolve().parents[1] / 'public/assets/fonts/NotoKufiArabic-Bold.ttf'
        pdfmetrics.registerFont(TTFont(font, str(path)))
    ink = colors.HexColor('#173c35')
    pale = colors.HexColor('#e4f1eb')
    def visual(value):
        return get_display(arabic_reshaper.reshape(str(value)), base_dir='R')

    def cell(value, width, size):
        lines, current = [], ''
        for word in str(value).split():
            joined = (current+' '+word).strip()
            if current and pdfmetrics.stringWidth(visual(joined),font,size)>width-12:
                lines.append(current)
                current = ''
            for char in ((' ' if current else '')+word):
                if pdfmetrics.stringWidth(visual(current+char),font,size)>width-12:
                    lines.append(current)
                    current = ''
                current += char
        return '\n'.join(visual(s) for s in lines+[current])

    stream = BytesIO()
    width, height = landscape(A4)
    doc = SimpleDocTemplate(stream, pagesize=(width,height), leftMargin=24,rightMargin=24,
                            topMargin=83,bottomMargin=34,title='كشف نتائج اختبارات المرشحين')
    def banner(pdf, document):
        pdf.setFillColor(ink)
        pdf.setFont(font,20)
        pdf.drawRightString(width-30,height-38,visual('كشف نتائج اختبارات المرشحين'))
        pdf.setFont(font,10)
        pdf.drawRightString(width-30,height-61,visual('الترتيب حسب الدرجة النهائية   •   عدد المختبرين '+str(len(rows))))
        pdf.drawString(30,height-39,'KPS')
        pdf.drawRightString(width-30,17,visual('خاص بلجنة المقابلات'))
        pdf.drawString(30,17,visual('صفحة '+str(document.page)))
        if preview:
            pdf.drawString(30,height-61,visual('نموذج توضيحي ببيانات افتراضية'))

    story = []
    from itertools import groupby
    for sections, group in groupby(rows, key=lambda r:tuple((s['title'],s['maximum']) for s in r['sections'])):
        group = list(group)
        widths = [55,156]+[(width-60-55-156-77)/len(sections)]*len(sections)+[77]
        labels = ['المركز','اسم المرشح']+[f'{title} (من {m})' for title,m in sections]+[f"النهائية (من {group[0]['maximum']})"]
        short = ['إقفال الفرع','القيود','العهد والرواتب','إقفال الشهر','الضريبة والزكاة','التحليل المالي']
        if len(sections)==6:
            labels = ['المركز','اسم المرشح']+short+['النهائية']
        headers = [cell(t,w,11) for t,w in zip(labels,widths)]
        if len(sections)==6:
            for index,maximum in enumerate([m for _,m in sections]+[group[0]['maximum']],2):
                headers[index] += '\n'+visual('من '+str(maximum))
        data = [headers[::-1]]
        for r in group:
            values=[r['rank'],r['candidate']]+[f"{s['points']:g}" for s in r['sections']]+[f"{r['score']:g}"]
            data.append([cell(v,w,12) for v,w in zip(values,widths)][::-1])
        table=LongTable(data,colWidths=widths[::-1],repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,0),11),
            ('FONTSIZE',(0,1),(-1,-1),12),('LEADING',(0,0),(-1,-1),19),
            ('ALIGN',(0,0),(-1,-1),'CENTER'),('ALIGN',(-2,1),(-2,-1),'RIGHT'),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),7),
            ('BOTTOMPADDING',(0,0),(-1,-1),7),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f4f7f5')]),
            ('BACKGROUND',(0,1),(0,-1),pale),('TEXTCOLOR',(0,1),(-1,-1),ink),
            ('BACKGROUND',(0,0),(-1,0),ink),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('INNERGRID',(0,0),(-1,-1),0.35,colors.HexColor('#bccdc4'))]))
        story.append(table)
    doc.build(story,onFirstPage=banner,onLaterPages=banner)
    return stream.getvalue()
