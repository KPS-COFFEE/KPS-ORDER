"""Offline XLSX inspection and draft menu compilation. No network or database I/O."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.enbishan_catalog_import import prepare_rows

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
HEADERS = {
    'product_code': ['product_code', 'sku', 'code', 'كود الصنف', 'رمز الصنف', 'رقم الصنف'],
    'name': ['name', 'name_ar', 'اسم الصنف', 'الصنف', 'الاسم'],
    'name_en': ['name_en', 'english name', 'الاسم بالإنجليزي', 'الاسم بالانجليزي'],
    'category': ['category', 'category_ar', 'القسم', 'التصنيف'],
    'category_en': ['category_en', 'القسم بالانجليزي'],
    'size': ['size', 'الحجم', 'المقاس'],
    'flavour': ['flavour', 'flavor', 'النكهة'],
    'price': ['price', 'price_after', 'السعر', 'السعر بعد', 'السعر بعد الخصم'],
    'price_before': ['price_before', 'السعر قبل', 'السعر قبل الخصم'],
    'image': ['image', 'image_url', 'الصورة', 'رابط الصورة'],
}


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
        for sheet in xml_member(archive, 'xl/workbook.xml').findall('m:sheets/m:sheet', NS):
            name = sheet.get('name')
            if not name or name in sheets:
                raise ValueError('Worksheet names must be unique.')
            member = relationships[sheet.get('{' + NS['r'] + '}id')]
            rows = []
            for row in xml_member(archive, member).findall('m:sheetData/m:row', NS):
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
            sheets[name] = rows
        images = [{'member': n, 'sha256': hashlib.sha256(archive.read(n)).hexdigest(),
                   'bytes': archive.getinfo(n).file_size} for n in names if n.startswith('xl/media/')]
    return {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sheets': sheets,
            'embedded_images': images, 'ordering_enabled': False}


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
    args = parser.parse_args()
    inspection = read_workbook(args.workbook)
    if args.menu_sheet:
        mapping = json.loads(args.column_map.read_text()) if args.column_map else None
        rows = menu_rows(inspection, args.menu_sheet, mapping)
        output = {'source_sha256': inspection['source_sha256'], 'rows': rows,
                  'preview': prepare_rows(rows), 'embedded_images': inspection['embedded_images']}
    else:
        output = inspection
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
