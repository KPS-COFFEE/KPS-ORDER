"""One column contract for the UI, PDF and genuine XLSX exports."""
import io
import zipfile
from html import escape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

LABELS={
 'completed_orders':('الطلبات المكتملة','Completed orders'),'manager':('مدير التوصيل','Delivery manager'),
 'bank':('الموازنة المعتمدة','Approved balance'),'cash_due':('الكاش المطلوب','Cash due'),
 'cash_received':('الكاش المستلم بالخزنة','Cash received in treasury'),'pending_cash':('الكاش المنتظر','Pending cash'),
 'violations':('قيمة المخالفات المسجلة','Recorded incident value'),'traffic':('المخالفات المرورية','Traffic violations'),
 'incident_notes':('المخالفات وأسبابها وقيمتها','Incidents, reasons and amounts'),
 'traffic_notes':('تفاصيل المخالفات المرورية','Traffic violation details'),
 'adjustments':('التعديلات: قبل وبعد والمنفذ والوقت والسبب','Changes: before, after, actor, time and reason'),
 'status':('حالة الاعتماد','Approval status'),
 'eligible':('طلبات مستحقة للحافز','Eligible orders'),'excluded':('مستبعدة بسبب السائق','Excluded: driver'),
 'pending_review':('بانتظار تحديد المسؤولية','Responsibility pending'),'awaiting_approval':('بانتظار اعتماد الشفت','Awaiting shift approval'),
 'open_shifts':('شفتات غير معتمدة','Unapproved shifts'),'incomplete':('غير مكتملة','Incomplete'),
 'tier_1':('طلبات 1–20','Orders 1–20'),'tier_2':('طلبات 21–25','Orders 21–25'),'tier_3':('طلبات 26–30','Orders 26–30'),'tier_4':('طلبات 31 فأكثر','Orders 31+'),
 'label':('البيان','Description'),'day':('التاريخ التشغيلي','Operational date'),
 'branch':('الفرع','Branch'),'vehicle':('المركبة','Vehicle'),'responsibility':('المسؤولية','Responsibility'),
 'orders':('عدد الطلبات المنشأة','Orders created'),'correct':('طلبات صحيحة','Correct orders'),
 'processed':('طلبات عليها معالجة','Orders requiring treatment'),'cancelled':('طلبات ملغاة','Cancelled orders'),
 'gross':('المبيعات الإجمالية','Gross sales'),'cash':('التحصيل النقدي','Cash collected'),
 'card':('تحصيل الشبكة','Card collected'),'refund_cash':('مرتجع نقدي','Cash refunded'),
 'refund_card':('مرتجع شبكة','Card refunded'),'refunds':('إجمالي المرتجعات','Total refunded'),
 'total':('صافي التحصيل','Net collected'),'net_sales':('صافي المبيعات','Net sales'),
 'notes':('ملاحظات','Notes'),'quantity':('كمية مباعة','Sold quantity'),
 'returned_quantity':('كمية مرتجعة محددة','Specified returned quantity'),
 'driver':('السائق','Driver'),'shortage':('العجز','Shortage'),'surplus':('الفائض','Surplus'),
 'approved_shortage':('العجز المعتمد محاسبيًا','Accounting-reviewed shortage'),
 'review':('مراجعة المحاسبة','Accounting review'),'cumulative':('العدد التراكمي للفترة','Cumulative count in period'),
 'kind':('النوع','Type'),'amount':('المبلغ','Amount'),'method':('وسيلة الدفع','Payment method'),
 'reason':('السبب','Reason'),'actor':('منفذ / معتمد الإجراء','Actor / authorizer'),'action':('الإجراء المتخذ','Action taken'),
 'weekday':('اليوم','Day'),'period':('الفترة بتوقيت الرياض','Period, Riyadh')}
MONEY={'bank','cash_due','cash_received','pending_cash','violations','traffic','gross','cash','card','refund_cash','refund_card','refunds','total','net_sales','shortage','surplus','approved_shortage','amount'}
COMMON=['orders','correct','processed','cancelled','cash','card','refund_cash','refund_card','total','gross','net_sales','notes']
COLUMNS={
 'driver_statement':['driver','day','completed_orders','bank','cash_due','cash_received','pending_cash','shortage','surplus','status','notes','incident_notes','violations','traffic_notes','traffic','adjustments'],
 'complimentary':['day','label','kind','branch','driver','amount','reason','actor','action','notes'],
 'driver_incentives':['label','eligible','excluded','pending_review','awaiting_approval','open_shifts','cumulative','tier_1','tier_2','tier_3','tier_4'],
 'driver_incentive_details':['day','driver','label','review','responsibility','reason','actor'],'daily':['weekday','day','period',*COMMON], 'branch':['label',*COMMON], 'driver':['label',*COMMON],
 'driver_daily':['label','correct','processed','cumulative','cash','card','refunds','total'],
 'items':['label','quantity','returned_quantity','gross','correct','processed'],
 'deficits':['day','driver','label','orders','cash','card','total','shortage','surplus','approved_shortage','review','notes'],
 'actions':['day','label','kind','amount','method','reason','actor','action'],
 'cases':['day','label','branch','driver','vehicle','kind','amount','review','responsibility','reason','action']}

def presentation(data,lang='ar'):
    from datetime import date
    keys=COLUMNS[data['group']];rows=[]
    for source in data['rows']:
        r=dict(source)
        if data['group']=='daily':
            d=date.fromisoformat(r['day']);r['weekday']=(['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'] if lang=='ar' else ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])[d.weekday()]
            r['period']='16:00 → 16:00 (+1)'
        rows.append(r)
    totals={k:sum(r.get(k,0) for r in rows if isinstance(r.get(k), (int,float))) for k in keys if k in MONEY or k in ('completed_orders','eligible','excluded','pending_review','awaiting_approval','tier_1','tier_2','tier_3','tier_4','orders','correct','processed','cancelled','quantity','returned_quantity')}
    # Item counts overlap between items; financial action amounts overlap across
    # cancellation/refund records. Neither may be presented as a grand sales sum.
    if data['group']=='items':totals.pop('correct',None);totals.pop('processed',None)
    if data['group'] in ('actions','cases','complimentary'):totals={}
    return keys,rows,totals

def contract(data,lang):
    keys,rows,totals=presentation(data,lang)
    return {**data,'rows':rows,'totals':totals,'columns':[{'key':k,'label':LABELS[k][lang=='en'],'money':k in MONEY} for k in keys]}

def render(data,format,lang):
    keys,rows,totals=presentation(data,lang)
    titles={'driver_incentives':('استحقاق السائقين اليومي والشهري','Daily and monthly driver entitlement'),'driver_incentive_details':('تفاصيل وأسباب استحقاق السائقين','Driver entitlement decisions and reasons'),'daily':('إجمالي يوم التشغيل','Operational day totals'),'branch':('مبيعات الفروع','Branch sales'),'items':('مبيعات الأصناف','Item sales'),'driver':('مبيعات السائقين','Driver sales'),'driver_daily':('عداد السائق اليومي','Daily driver counter'),'deficits':('العجز والزيادة','Shortages and surpluses'),'actions':('المرتجعات والإلغاءات والمعالجات','Refunds, cancellations and treatments'),'cases':('ملاحظات المركبات والطلبات','Vehicle and order incidents')}
    titles['complimentary']=('الضيافة والتعويض والإلغاء وإعادة الطباعة','Hospitality, compensation, cancellations and reprints')
    titles['driver_statement']=('كشف السائق اليومي والشهري','Daily and monthly driver statement')
    title=('المحاسبة - ' if lang=='ar' else 'Accounting - ')+titles[data['group']][lang=='en']
    headers=[LABELS[k][lang=='en'] for k in keys]
    summary={keys[0]:'الإجمالي / Total',**totals}
    values=[[r.get(k,'')/100 if k in MONEY and isinstance(r.get(k), (int,float)) else r.get(k,'') for k in keys] for r in [*rows,summary]]
    units="" if data['group'].startswith('driver_incentive') else "SAR | "
    meta=f"{data['period']['start']} — {data['period']['end']} | Asia/Riyadh | "+units+('كشف مقفل / Closed statement' if data.get('frozen') else 'تقرير مباشر / Live report')
    if format=='xlsx':return xlsx([headers,*values],keys,[title,meta,data['definitions']],lang)
    if data['group']=='driver_statement':
        # A separate page per driver and section keeps daily figures readable;
        # long notes/audit entries never compress all columns onto one page.
        sections=[]
        for driver in dict.fromkeys(r['driver_id'] for r in rows):
            entries=[r for r in rows if r['driver_id']==driver]
            for section_keys in (['day','completed_orders','bank','cash_due','cash_received','pending_cash','shortage','surplus','status'],
                                 ['day','notes','incident_notes','violations','traffic_notes','traffic'],['day','adjustments']):
                section_rows=[[LABELS[k][lang=='en'] for k in section_keys]]
                section_rows += [[r.get(k,'')/100 if k in MONEY and isinstance(r.get(k),(int,float)) else r.get(k,'') for k in section_keys] for r in entries]
                section_rows.append([('الإجمالي' if lang=='ar' else 'Total') if i==0 else sum(r.get(k,0) for r in entries)/100 if k in MONEY else sum(r.get(k,0) for r in entries) if k=='completed_orders' else '' for i,k in enumerate(section_keys)])
                sections.append(dict(title=entries[0]['driver'],keys=section_keys,rows=section_rows))
        return pdf([headers,*values],keys,title,meta,data['definitions'],lang,sections=sections or None,compact=True)
    return pdf([headers,*values],keys,title,meta,data['definitions'],lang)

def xlsx(rows,keys,notes,lang):
    def column(n):
        s=''
        while n:n,r=divmod(n-1,26);s=chr(65+r)+s
        return s
    def cell(v,ref,style):
        if isinstance(v,(int,float)):return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
        # inlineStr never interprets user content as a formula or external link.
        v=''.join(ch for ch in str(v) if ord(ch)>=32 or ch in '\n\t')
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(v)}</t></is></c>'
    sheet=[]
    for i,row in enumerate(rows,1):
        sheet.append(f'<row r="{i}">'+''.join(cell(v,f'{column(j)}{i}',1 if i==1 else 3 if keys[j-1] in ('shortage','approved_shortage') and isinstance(v,(int,float)) and v>0 else 2 if keys[j-1] in MONEY else 0) for j,v in enumerate(row,1))+'</row>')
    root='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    workbook=f'<workbook xmlns="{root}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/><sheet name="Definitions" sheetId="2" r:id="rId2"/></sheets></workbook>'
    types='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'+''.join(f'<Override PartName="/{p}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{t}+xml"/>' for p,t in [('xl/workbook.xml','sheet.main'),('xl/worksheets/sheet1.xml','worksheet'),('xl/worksheets/sheet2.xml','worksheet'),('xl/styles.xml','styles')])+'</Types>'
    relroot='http://schemas.openxmlformats.org/package/2006/relationships';reltype='http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    rels=lambda rs:f'<Relationships xmlns="{relroot}">'+''.join(f'<Relationship Id="rId{i}" Type="{reltype+t}" Target="{p}"/>' for i,t,p in rs)+'</Relationships>'
    styles=f'<styleSheet xmlns="{root}"><fonts count="3"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font><font><color rgb="FFC00000"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="4">'+''.join(f'<xf numFmtId="{num}" fontId="{font}" fillId="0" borderId="0" xfId="0" applyAlignment="1" applyNumberFormat="1"><alignment wrapText="1" vertical="top"/></xf>' for font,num in [(0,0),(1,0),(0,2),(2,2)])+'</cellXfs></styleSheet>'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',types);z.writestr('_rels/.rels',rels([(1,'officeDocument','xl/workbook.xml')]))
        z.writestr('xl/workbook.xml',workbook);z.writestr('xl/_rels/workbook.xml.rels',rels([(1,'worksheet','worksheets/sheet1.xml'),(2,'worksheet','worksheets/sheet2.xml'),(3,'styles','styles.xml')]))
        z.writestr('xl/styles.xml',styles)
        z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{root}"><sheetViews><sheetView workbookViewId="0" rightToLeft="{int(lang=="ar")}"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><cols>'+''.join(f'<col min="{i}" max="{i}" width="{45 if k in ("notes","reason","action") else 24}" customWidth="1"/>' for i,k in enumerate(keys,1))+'</cols><sheetData>'+''.join(sheet)+f'</sheetData><autoFilter ref="A1:{column(len(keys))}{max(1,len(rows)-1)}"/></worksheet>')
        z.writestr('xl/worksheets/sheet2.xml',f'<worksheet xmlns="{root}"><cols><col min="1" max="1" width="120" customWidth="1"/></cols><sheetData>'+''.join(f'<row r="{i}">'+cell(n,f'A{i}',0)+'</row>' for i,n in enumerate(notes,1))+'</sheetData></worksheet>')
    return out.getvalue()

def pdf(rows,keys,title,meta,definitions,lang,sections=None,compact=False):
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A3,landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,LongTable,TableStyle,Spacer,Flowable,PageBreak
    from reportlab.lib import colors
    name='AccountingKufi'
    if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(Path(__file__).resolve().parents[1]/'public/assets/fonts/NotoKufiArabic-Regular.ttf')))
    style=ParagraphStyle('accounting',fontName=name,fontSize=9,leading=14 if compact else 16,alignment=2 if lang=='ar' else 0)
    class ArabicText(Flowable):
        def __init__(self,value,style):
            Flowable.__init__(self);self.value=str(value);self.style=style
        def wrap(self,width,height):
            self.width=width;self.lines=[]
            measure=lambda v:pdfmetrics.stringWidth(get_display(arabic_reshaper.reshape(v)),name,self.style.fontSize)
            for paragraph in self.value.split('\n'):
                line=''
                for word in paragraph.split():
                    if measure(word)>width:
                        if line:self.lines.append(line);line=''
                        chunk=''
                        for character in word:
                            if chunk and measure(chunk+character)>width:self.lines.append(chunk);chunk=character
                            else:chunk+=character
                        line=chunk
                    else:
                        candidate=(line+' '+word).strip()
                        if line and measure(candidate)>width:self.lines.append(line);line=word
                        else:line=candidate
                self.lines.append(line)
            self.height=len(self.lines)*self.style.leading
            return width,self.height
        def split(self,width,height):
            self.wrap(width,height);count=int(height//self.style.leading)
            if count<1 or count>=len(self.lines):return []
            return [ArabicText('\n'.join(self.lines[:count]),self.style),ArabicText('\n'.join(self.lines[count:]),self.style)]
        def draw(self):
            self.canv.setFont(name,self.style.fontSize);self.canv.setFillColor(self.style.textColor)
            for i,line in enumerate(self.lines):self.canv.drawRightString(self.width,self.height-(i+1)*self.style.leading+4,get_display(arabic_reshaper.reshape(line)))
    text=lambda v:ArabicText(v,style) if lang=='ar' else Paragraph(escape(str(v)),style)
    def render_table(section_rows,section_keys):
        cells=[[text(f'{v:,.2f}' if isinstance(v,float) else v) for v in row] for row in section_rows]
        keys=list(section_keys)
        if lang=='ar':cells=[list(reversed(r)) for r in cells];keys=list(reversed(keys))
        weights=[7 if k=='adjustments' else 2.8 if k in ('notes','incident_notes','traffic_notes','reason','action','label','driver_name','branches') else 1.3 for k in keys];available=landscape(A3)[0]-48
        widths=[available*w/sum(weights) for w in weights]
        table=LongTable(cells,colWidths=widths,repeatRows=1,splitByRow=1,splitInRow=1)
        commands=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#cccccc')),('VALIGN',(0,0),(-1,-1),'TOP')]
        if sections is None:commands.append(('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#eeeeee')))
        for k in ('shortage','approved_shortage'):
            if k in keys:
                col=keys.index(k)
                for row in cells[1:]:row[col].style=ParagraphStyle('red',parent=style,textColor=colors.HexColor('#b00020'))
        if 'fuel_status' in keys:
            col=keys.index('fuel_status')
            palette={'green':'#165b2b','orange':'#875000','red':'#b00020','grey':'#555555',
                     'أخضر':'#165b2b','برتقالي':'#875000','أحمر':'#b00020','رمادي':'#555555'}
            for source,row in zip(section_rows[1:],cells[1:]):
                raw=source[list(section_keys).index('fuel_status')]
                row[col].style=ParagraphStyle('fuel',parent=style,textColor=colors.HexColor(palette.get(str(raw),'#555555')))
        table.setStyle(TableStyle(commands));return table
    out=io.BytesIO()
    def footer(canvas,doc):canvas.setFont(name,9);canvas.drawString(24,14,str(doc.page))
    story=[text(title),text(meta),Spacer(1,12)]
    if sections:
        for i,section in enumerate(sections):
            if i:story.extend([PageBreak(),text(title),text(meta),Spacer(1,12)])
            story.extend([text(section['title']),Spacer(1,8),render_table(section['rows'],section['keys'])])
    else:story.append(render_table(rows,keys))
    story.extend([Spacer(1,12),text(definitions)])
    SimpleDocTemplate(out,pagesize=landscape(A3),rightMargin=24,leftMargin=24,topMargin=24,bottomMargin=30).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
