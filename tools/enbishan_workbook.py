"""Offline XLSX inspection and draft menu compilation. No network or database I/O."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
HEADERS = {
    'product_code': ['product_code', 'sku', 'code', 'كود الصنف', 'رمز الصنف', 'رقم الصنف'],
    'name': ['name', 'name_ar', 'اسم الصنف', 'الصنف', 'الاسم'],
    'name_en': ['name_en', 'english name', 'الاسم بالإنجليزي', 'الاسم بالانجليزي'],
    'category': ['category', 'category_ar', 'القسم', 'التصنيف'],
    'category_en': ['category_en', 'القسم بالانجليزي'],
    'size': ['size', 'الحجم', 'المقاس'],
    'size_en': ['size_en', 'الحجم بالانجليزي'],
    'flavour': ['flavour', 'flavor', 'النكهة'],
    'flavour_en': ['flavour_en', 'flavor_en', 'النكهة بالانجليزي'],
    'price': ['price', 'price_after', 'السعر', 'السعر بعد', 'السعر بعد الخصم'],
    'price_before': ['price_before', 'السعر قبل', 'السعر قبل الخصم'],
    'image': ['image', 'image_url', 'الصورة', 'رابط الصورة'],
}

# Presentation translations of exact source values, not new menu choices.
REFERENCE_CATEGORIES = {
    'Stuffed': ('المحاشي', 'Stuffed'), 'Fried': ('المقليات', 'Fried'),
    'Other': ('أصناف أخرى', 'Other'), 'Bakery': ('المخبوزات', 'Bakery'),
    'المشروبات - Beverage': ('المشروبات', 'Beverage'),
}
REFERENCE_SIZES = {'صغير': 'Small', 'وسط': 'Medium', 'كبير': 'Large',
                   'عائلي': 'Family', 'كبير جداً': 'Extra Large', 'فردية': 'Single'}
REFERENCE_FLAVOURS = {'دبس رمان': 'Pomegranate molasses', 'ليمون': 'Lemon',
                      'سبايسي': 'Spicy', 'مشكل': 'Mixed',
                      'باشن فروت': 'Passion fruit', 'بلاك بيرى': 'Blackberry',
                      'راسبيرى': 'Raspberry', 'ستروبرى': 'Strawberry',
                      'بلولوجن': 'Blue Lagoon', 'رمان': 'Pomegranate',
                      'سينابون': 'Cinnabon', 'كلاسيك': 'Classic', 'مكس': 'Mix', 'بيكان': 'Pecan'}


def xml_member(archive, name):
    raw = archive.read(name)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('XML entity declarations are not accepted.')
    return ET.fromstring(raw)


def read_workbook(path):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Workbook exceeds the 32 MiB input limit.')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (len(names) != len(set(names)) or len(names) > 2000
                or sum(e.file_size for e in entries) > 64 * 1024 * 1024
                or any(e.file_size > 16 * 1024 * 1024 for e in entries)
                or any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n for n in names)):
            raise ValueError('Unsafe or oversized workbook archive.')
        if any('vbaproject' in n.lower() or 'externallinks/' in n.lower() for n in names):
            raise ValueError('Macros and external workbook links are not accepted.')
        shared = []
        if 'xl/sharedStrings.xml' in names:
            shared = [''.join(node.itertext()) for node in xml_member(archive, 'xl/sharedStrings.xml').findall('m:si', NS)]
        relationships = {}
        for rel in xml_member(archive, 'xl/_rels/workbook.xml.rels'):
            if rel.get('TargetMode') == 'External':
                raise ValueError('External workbook relationships are not accepted.')
            target = rel.get('Target', '')
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in PurePosixPath(member).parts:
                raise ValueError('Unsafe worksheet target.')
            relationships[rel.get('Id')] = member
        sheets = {}
        source_rows = {}
        merged_cells = {}
        for sheet in xml_member(archive, 'xl/workbook.xml').findall('m:sheets/m:sheet', NS):
            name = sheet.get('name')
            if not name or name in sheets:
                raise ValueError('Worksheet names must be unique.')
            member = relationships[sheet.get('{' + NS['r'] + '}id')]
            rows = []
            row_numbers = []
            worksheet = xml_member(archive, member)
            merged_cells[name] = [node.get('ref') for node in worksheet.findall('m:mergeCells/m:mergeCell', NS)]
            for ordinal, row in enumerate(worksheet.findall('m:sheetData/m:row', NS), 1):
                if len(rows) >= 5001:
                    raise ValueError('Worksheet exceeds 5000 data rows.')
                cells = {}
                for cell in row.findall('m:c', NS):
                    if cell.find('m:f', NS) is not None:
                        raise ValueError('Resolve formulas to explicit values before import.')
                    reference = cell.get('r', '')
                    column = ''.join(c for c in reference if c.isalpha())
                    if not column or len(column) > 2 or column in cells:
                        raise ValueError('Invalid worksheet cell reference.')
                    kind = cell.get('t')
                    value = cell.findtext('m:v', '', NS)
                    if kind == 's':
                        value = shared[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(cell.find('m:is', NS).itertext())
                    elif kind in {'b', 'e'}:
                        raise ValueError('Boolean/error cells are not menu values.')
                    if len(value) > 2000:
                        raise ValueError('Cell text is too long.')
                    cells[column] = value
                if any(cells.values()):
                    rows.append(cells)
                    row_numbers.append(int(row.get('r', ordinal)))
            sheets[name] = rows
            source_rows[name] = row_numbers
        images = [{'member': n, 'sha256': hashlib.sha256(archive.read(n)).hexdigest(),
                   'bytes': archive.getinfo(n).file_size} for n in names if n.startswith('xl/media/')]
    return {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sheets': sheets,
            'source_rows': source_rows, 'merged_cells': merged_cells,
            'embedded_images': images, 'ordering_enabled': False}


def reference_menu(inspection, owner_approved=False):
    """Review the owner's THE MENU layout without guessing merged values or options.

    Source numbering is provenance, never a permanent SKU. The deterministic
    semantic key is retained in the review manifest for subsequent reimports.
    Ambiguous families remain outside the import payload until reviewed.
    """
    if owner_approved and inspection['source_sha256'] != 'ca574c39384e250535397f4bac40dfabc234487ffee8fa1d623344e085402a1e':
        raise ValueError('Owner decisions apply only to the reviewed source; review the changed workbook.')
    name = 'THE MENU'
    sheet = inspection['sheets'].get(name, [])
    numbers = inspection.get('source_rows', {}).get(name, [])
    by_number = {number: dict(row) for number, row in zip(numbers, sheet)}
    required = {'B': 'Name Item English', 'C': 'اسم الصنف بالعربي', 'D': 'Category - القسم',
                'G': 'العدد', 'H': 'الاختيارات', 'I': 'سعر المنيو بعد الخصم',
                'J': 'السعر الاساسى', 'K': 'Image Link - رابط الصورة', 'L': 'Item Price'}
    if any(by_number.get(2, {}).get(col) != value for col, value in required.items()):
        raise ValueError('Reference menu headers changed; review the mapping before import.')
    for merge in inspection.get('merged_cells', {}).get(name, []):
        match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', merge or '')
        if not match:
            raise ValueError('Invalid merged cell range.')
        col, start, end_col, end = match.groups()
        if col != end_col:  # The title is decorative, not data.
            if merge != 'A1:N1':
                raise ValueError('Review a horizontally merged data range before import.')
            continue
        if col not in {'C', 'D', 'H'} or not 3 <= int(start) <= int(end) <= 5001:
            raise ValueError('Review unsupported merged data before import.')
        value = by_number.get(int(start), {}).get(col, '')
        for number in range(int(start), int(end) + 1):
            row = by_number.setdefault(number, {})
            if row.get(col) and row[col] != value:
                raise ValueError('Conflicting merged cell values.')
            row[col] = value
    inventory, payload, unresolved = [], [], []
    for number, source in sorted(by_number.items()):
        if number < 3:
            continue
        source = {key: value.strip() for key, value in source.items()}
        sale, original = source.get('I', ''), source.get('J', '')
        from app.enbishan_catalog_import import halalas
        if halalas(original) < halalas(sale):
            raise ValueError('Original price below sale price at source row ' + str(number))
        semantic_key = '|'.join(source.get(k, '') for k in ('B', 'C', 'D'))
        code = 'enb-' + hashlib.sha256(semantic_key.encode()).hexdigest()[:24]
        size = source.get('F', '')
        size_en = REFERENCE_SIZES.get(size)
        if size_en is None or (source.get('E') and source['E'] != size_en):
            raise ValueError('Review unknown or conflicting size translation at source row ' + str(number))
        category = REFERENCE_CATEGORIES.get(source.get('D', ''))
        if category is None:
            raise ValueError('Review unknown source category at row ' + str(number))
        count = source.get('G', '')
        if count.isdigit():
            size += ' · ' + count + ' قطعة'
            size_en += ' · ' + count + (' piece' if int(count) == 1 else ' pieces')
        issues = []
        options = source.get('H', '')
        # Only the unambiguous grape-leaf flavour list is compiled. Other
        # choices retain their verbatim source for an explicit owner decision.
        flavours = options.splitlines() if options == 'دبس رمان\nليمون\nسبايسي\nمشكل' else ['']
        if options and flavours == ['']:
            issues.append('تأكيد بنية الاختيارات والتكرار')
        if source.get('C') in {'خليه', 'موهيتو', 'بوكس رمضان'}:
            issues.append('اعتماد الاسم والتركيبة والتوفر')
        if source.get('B') == 'Crispy & Cabbage':
            issues.append('تعارض الاسم العربي والإنجليزي')
        approved_name, approved_en, selection_set = source.get('C', ''), source.get('B', ''), ''
        if owner_approved:
            if number in range(10, 14):
                flavours = options.splitlines()[1:]
                if flavours != ['دبس رمان', 'ليمون', 'سبايسي', 'مشكل'] or not count.isdigit() or int(count) % 2:
                    raise ValueError('Reviewed mixed grape/cabbage source changed.')
                half = int(count) // 2
                size += f' ({half} ورق عنب + {half} ملفوف)'
                size_en += f' ({half} grape leaves + {half} cabbage)'
                issues = []
            elif number == 19:
                if options != 'أختر 3 من : قطعة بطاطس - قطعة فليفلة - 2 ورق عنب - 2 بصل - 2 كوسا - 2 باذنجان':
                    raise ValueError('Reviewed choose-three source changed.')
                code += '-choose3'
                selection_set = 'mahashi_choose_three'
                issues = []
            elif number == 41:
                approved_name, approved_en = 'ورق عنب كريسبي وملفوف', 'Crispy grape leaves & cabbage'
                size += ' (5 كريسبي + 5 ملفوف)'
                size_en += ' (5 crispy grape leaves + 5 cabbage)'
                issues = []
            elif number in range(42, 46):
                # One card, four flavours; stable code independent of display name.
                code = 'enb-khalyah-owner-20261020'
                approved_name, approved_en = 'خلية', 'Khaliya'
                flavours = [{42: 'سينابون', 43: 'كلاسيك', 44: 'مكس', 45: 'بيكان'}[number]]
                issues = []
            elif number in range(52, 55):
                approved_name, approved_en = {
                    52: ('موهيتو مع فتة', 'Mojito with fatta'),
                    53: ('موهيتو مع محاشي مشكل', 'Mojito with mixed mahashi'),
                    54: ('موهيتو مع ورق عنب', 'Mojito with grape leaves')}[number]
                flavours = [part.strip() for part in options.split(' - ')]
                if flavours != ['باشن فروت', 'بلاك بيرى', 'راسبيرى', 'ستروبرى', 'بلولوجن', 'رمان']:
                    raise ValueError('Reviewed mojito flavours changed.')
                issues = []
            elif number in range(55, 59):
                approved_name = {
                    55: 'بوكس رمضان · 5 كبة + 12 مسخن',
                    56: 'بوكس رمضان · 5 كبة + 6 مسخن + 5 سمبوسة',
                    57: 'بوكس رمضان · 5 كبة + 5 سمبوسة + 5 ورق عنب كريسبي',
                    58: 'بوكس رمضان · 5 سمبوسة + 12 مسخن'}[number]
                approved_en = {
                    55: 'Ramadan box · 5 kibbeh + 12 musakhan',
                    56: 'Ramadan box · 5 kibbeh + 6 musakhan + 5 samosa',
                    57: 'Ramadan box · 5 kibbeh + 5 samosa + 5 crispy grape leaves',
                    58: 'Ramadan box · 5 samosa + 12 musakhan'}[number]
                issues = []
        image = source.get('K', '')
        if not re.fullmatch(r'https://[^\s]+', image):
            image = ''
        inventory.append({'source_row': number, 'source': source, 'product_code': code,
                          'sale_halalas': halalas(sale), 'original_halalas': halalas(original),
                          'size_label': size, 'size_label_en': size_en,
                          'category_label': category[0], 'category_label_en': category[1],
                          'image_reference': image, 'review_required': issues})
        if issues:
            unresolved.append({'source_row': number, 'issues': issues})
            continue
        for flavour in flavours:
            payload.append({'product_code': code, 'name': approved_name,
                            'name_en': approved_en, 'category': category[0], 'category_en': category[1],
                            'size': size, 'size_en': size_en, 'flavour': flavour,
                            'flavour_en': REFERENCE_FLAVOURS.get(flavour, ''), 'price': sale,
                            'price_before': original, 'image': image,
                            **({'selection_set': selection_set} if selection_set else {})})
    return {'source_sha256': inspection['source_sha256'], 'inventory': inventory,
            'rows': payload, 'unresolved': unresolved, 'ordering_enabled': False,
            'missing_images': [r['source_row'] for r in inventory if not r['image_reference']]}


def menu_rows(inspection, sheet_name, column_map=None):
    rows = inspection['sheets'].get(sheet_name)
    if not rows or len(rows) < 2:
        raise ValueError('Choose a menu worksheet with a header and data rows.')
    normalize = lambda v: ' '.join(v.strip().lower().split())
    aliases = {normalize(v): field for field, values in HEADERS.items() for v in values}
    explicit = column_map or {}
    mapping = {}
    for column, header in rows[0].items():
        field = explicit.get(header) or aliases.get(normalize(header))
        if field:
            if field not in HEADERS or field in mapping.values():
                raise ValueError('Ambiguous or unknown menu header: ' + header)
            mapping[column] = field
        elif any(row.get(column) for row in rows[1:]):
            raise ValueError('Map the nonempty column explicitly: ' + header)
    if not {'product_code', 'name', 'category', 'price'} <= set(mapping.values()):
        raise ValueError('Product code, name, category and selling-price columns are required.')
    if any(value for row in rows[1:] for column, value in row.items() if column not in rows[0]):
        raise ValueError('A data column has no header; review the worksheet before import.')
    return [{field: row.get(column, '') for column, field in mapping.items()} for row in rows[1:]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--menu-sheet', help='Omit to inspect every worksheet without interpreting it.')
    parser.add_argument('--column-map', type=Path, help='Reviewed JSON mapping from original headers to canonical fields.')
    parser.add_argument('--reference-menu', action='store_true', help='Review the approved THE MENU source layout; unresolved choices stay out of drafts.')
    parser.add_argument('--owner-approved-20261020', action='store_true', help='Apply source-bound owner menu decisions; drafts remain disabled.')
    parser.add_argument('--owner-approved-20261022', action='store_true', help='Prepare exact final review: hidden drinks, mixed sizes and five original photos; still disabled drafts.')
    args = parser.parse_args()
    inspection = read_workbook(args.workbook)
    # Raw offline inspection and --help also work in the distinct prior-app
    # recovery image, which deliberately has no new ENBISHAN application routes.
    # Compile only when explicitly selected and the reviewed module is present.
    if args.reference_menu or args.menu_sheet:
        from app.enbishan_catalog_import import prepare_rows
    if args.reference_menu:
        output = reference_menu(inspection, owner_approved=args.owner_approved_20261020 or args.owner_approved_20261022)
        output['preview'] = prepare_rows(output['rows'])
        if args.owner_approved_20261022:
            from app import enbishan_menu_review
            output['owner_review'] = enbishan_menu_review.DECISION
            output['preview'] = enbishan_menu_review.apply(output['preview'], source_sha256=output['source_sha256'], rows=output['rows'])
    elif args.menu_sheet:
        mapping = json.loads(args.column_map.read_text()) if args.column_map else None
        rows = menu_rows(inspection, args.menu_sheet, mapping)
        output = {'source_sha256': inspection['source_sha256'], 'rows': rows,
                  'preview': prepare_rows(rows), 'embedded_images': inspection['embedded_images']}
    else:
        output = inspection
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
