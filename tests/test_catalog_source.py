"""Offline acceptance contract for the independently recovered source snapshot.

These assertions pin reviewed source identities/content and honest unknowns.
They do not exercise or duplicate pricing, routing or settlement engines.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HTML_SHA256 = '2e5f31fc8ad2d0a3a46ae98dd50ae2c8018ab9963b9eb4d2ae60a49f722041d6'
PAGE_COMMIT = 'b9d4f523c18981032151f5ab5dad57bd23f946c5'
APEX_SOURCE_COMMIT = '22c66093bfbcca4b7539f4c31aa9099b8ee9d968'
BRANCH_SOURCE_PATH = 'app/v1011_canonical_branch_selectors.py'
OPTIONS_SOURCE_PATH = 'app/v1038_product_recipe_options_closure.py'

# Independently reviewed original deployed cards. The source repeated three
# names by placement; their original ids must not collapse during migration.
SOURCE_PRODUCTS = {
    'kps-01': ('كولد برو', 950), 'kps-02': ('قهوة اليوم بارد', 550),
    'kps-03': ('كوكيز شوكليت', 550), 'kps-04': ('قهوة اليوم حار', 550),
    'kps-05': ('قهوة اليوم بارد', 550), 'kps-06': ('V60 حار', 750),
    'kps-07': ('V60 بارد', 750), 'kps-08': ('كولد برو', 950),
    'kps-09': ('آيس تي خوخ', 600), 'kps-10': ('آيس كركديه', 650),
    'kps-11': ('موهيتو', 900), 'kps-12': ('آيس شوكليت', 850),
    'kps-13': ('سموذي شمام', 800), 'kps-14': ('ماتشا', 850),
    'kps-15': ('تيراميسو', 800), 'kps-16': ('سينابون', 550),
    'kps-17': ('ترايفل مانجو', 650), 'kps-18': ('كروسان رول', 550),
    'kps-19': ('كيكة دبي', 550), 'kps-20': ('براونيز كراميل', 550),
    'kps-21': ('كوكيز شوكليت', 550), 'kps-22': ('كرانشي', 550),
    'kps-23': ('كوكونت', 650), 'kps-24': ('كرانش شوكو', 700),
    'kps-25': ('بوكس قهوة', 3600), 'kps-26': ('بوكس كوكيز', 3400),
    'kps-27': ('بوكس كرانشي', 3400), 'kps-28': ('بوكس سينابون', 2450),
    'kps-29': ('بوكس براونيز', 3400), 'kps-30': ('كوب حراري', 2100),
    'kps-31': ('كوب حراري', 1800), 'kps-32': ('ماء نوفا 330 مل', 100),
    'kps-33': ('خلية بيكان', 1950), 'kps-34': ('خلية كلاسيك', 1950),
    'kps-35': ('كوب إضافي', 100),
}

# Full SHA-256 values measured from decoded original source image bytes.
SOURCE_ASSET_SHA256 = {
    'kps-01': '36c27597bfd3d8c73478d0745a337c4741a6318ba3fd190d1fe22ff4a43196e3',
    'kps-02': 'd9135faa0a8457f35dbb8c014cf989d5e53e3cb82454cef43c9c51de4a6ea3db',
    'kps-03': 'c64514169334724298493fcefe84e0946180c1e02a159180ed6646c0d1f29e08',
    'kps-04': '6ee5fe34cceb5ef77c4830dbc7fc48dc911a77f39479bbdbd8ffc67f6459e611',
    'kps-05': 'd9135faa0a8457f35dbb8c014cf989d5e53e3cb82454cef43c9c51de4a6ea3db',
    'kps-06': '3c7dd5131d5312ea6a44ee4f94cd5b96de9fef9de510127411bb22ab192f579f',
    'kps-07': 'c7697290eec9cef4404fbc75a61663f5b6b973cf4d3368bf11b75c35f9e94610',
    'kps-08': '36c27597bfd3d8c73478d0745a337c4741a6318ba3fd190d1fe22ff4a43196e3',
    'kps-09': 'a1399dc0069b2d18bcd2b2252a1293df599e7d9f9cacff49bad9f5b34a4e4d51',
    'kps-10': 'c8d7dfc33583d30f1f704026090ab0b9dced0ebf26f8cd8ff75e4d5ff3da64a3',
    'kps-11': '4199ba05a8e3b50c409b75841ec5daed3bde7f653e6fc5a5ad32b8fdfe3ec3d0',
    'kps-12': '0e6bf5c8757f3f669761f2d21fef477bb6a47fcb22be406344707b112d506049',
    'kps-13': '5174dfb186096bdaf2f1c7374d890a797bd178859a40e27c0c832a242218d6d9',
    'kps-14': '299cbe64131a32058e748552e490198c4f9774d7ed457308d4c51f2c235e57ee',
    'kps-15': 'edbe35424de5d8e958a5370d95e830f451f1cb7960d5e3439c4064508282c952',
    'kps-16': 'cbd6789f65cf9619a57afa90aefd94f784040085b0a6abfc956f280e3cb7ebfa',
    'kps-17': 'c0dcd65b4428f2e2120c6895afcca6d1b7456b97ceb6a55c0b706ad4b1ce21c0',
    'kps-18': '86e0de248b0c81598323240e7fee8eaa6fccbf0aeafc0424be767f50c39d75ce',
    'kps-19': '228371132e37bd7bffb7ce140b12bd0e7df9fbf529521540f7eb14453d3d5e60',
    'kps-20': '37a1f50ac7b188a75b3adcd3217714ee8b8f632306d20cdfb729d7ba51556c92',
    'kps-21': 'ce26825955c888f2a91565fd3356e34bdee3d870428b297fb3779d0e6bc56693',
    'kps-22': 'df12ad5156f0761416c4f8fb999f6da7e53ffdf27846e26e9289cac8b89ce035',
    'kps-23': '86b341e362b5194ba08c72bc98d5d97dd70b0a10d19215f689d6e8952574483b',
    'kps-24': '1951cbc39afcdcbad7cd635695a6294a627ad18699e7fb542f272bbb8a596214',
    'kps-25': '9e207505d29a33eaa778a64cb021360ba753d4622f08ef4fbf645b5495fe740b',
    'kps-26': '75f8ef11d0c8a0b1b76930c29fc93b2ea454aeb1f91958ea5b141e2e64ba2e3b',
    'kps-27': 'a5e37fbf3d1e39168144ae1bd1a0263fa88a946c82be0f53bea8deecb153e525',
    'kps-28': 'f502910fe5e1724edf6870b96991a0e83289df354a83e5674393d0bf57e7735d',
    'kps-29': '794aa64d8e887b716ae61a67ef001da72e3159e07e5b32cc625b57c17c14822b',
    'kps-30': '8037793c7cf2103a1d27da720aabb2fc07c5b0348652fffb535b739eb3fb93af',
    'kps-31': 'd8ece8dd82af77f04728d3cfcdc9c01c1b446a809b9276ff3e60d7fbf96653ca',
    'kps-32': '362d6590ff737195c12aed05376cfd8e2344dbba97c43bdbf052fe23eee08084',
    'kps-33': '9ed7f1cc098a142f0051775070d44c6b884e8f9b775139b7469183a3c8b12495',
    'kps-34': '8c01e210be90a80817ce86e7c7a8ce3116dea10d1f150e0d4ee28c07e9ef40d5',
    'kps-35': 'fc3d1c4bfde4bff42f08f7c9b7ee1ed6af692973986f20b199470ef8209042a9',
}
SOURCE_LOGO_SHA256 = 'fb911a7b4bb4ce7d5d9887c2cda4679c1a99949aca60ca1cf7b344e6bbc2ecc4'

# Values transcribed from the explicitly keyed official branch-number source.
SOURCE_BRANCHES = {
    1: ('حفر الباطن - المروج', 'حفر الباطن'),
    2: ('حفر الباطن - الخالدية', 'حفر الباطن'),
    3: ('حفر الباطن - النايفية', 'حفر الباطن'),
    4: ('حفر الباطن - العزيزية', 'حفر الباطن'),
    5: ('الذيبية', 'الذيبية'), 6: ('النعيرية - العزيزية', 'النعيرية'),
    7: ('الدمام - أحد', 'الدمام'), 8: ('الأرطاوية', 'الأرطاوية'),
    9: ('حفر الباطن - المروج', 'حفر الباطن'),
    10: ('القيصومة - الخالدية', 'القيصومة'),
    11: ('الخفجي - العزيزية', 'الخفجي'),
    12: ('حفر الباطن - الباطن', 'حفر الباطن'),
    13: ('الزلفي - الفاخرية', 'الزلفي'), 14: ('الدمام - الجامعيين', 'الدمام'),
    15: ('المجمعة', 'المجمعة'), 16: ('عنيزة', 'عنيزة'),
    17: ('حائل', 'حائل'), 18: ('شعبة نصاب', 'شعبة نصاب'),
    19: ('رفحاء', 'رفحاء'), 20: ('قبه', 'قبه'),
}
ELIGIBLE_BRANCH_NUMBERS = {1, 2, 3, 4, 5, 9, 10, 11, 12, 19, 20}
DELIVERY_CITIES = {'حفر الباطن', 'الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه'}


class SourceSnapshotContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))
        cls.branch_data = json.loads((ROOT / 'data/branches.json').read_text(encoding='utf-8'))
        cls.products = {p['id']: p for p in cls.catalog['products']}
        cls.branches = {b['branch_no']: b for b in cls.branch_data['branches']}

    def asset(self, url):
        self.assertTrue(url.startswith('/assets/'), 'Source assets must be served locally')
        path = (ROOT / 'public' / url.lstrip('/')).resolve()
        self.assertTrue(path.is_relative_to((ROOT / 'public/assets').resolve()))
        self.assertTrue(path.is_file(), f'Missing preserved source asset: {url}')
        return path

    def test_live_page_provenance_and_unverified_runtime_are_explicit(self):
        meta = self.catalog['metadata']
        self.assertEqual(meta['source_html_sha256'], SOURCE_HTML_SHA256)
        self.assertEqual(meta['source_commit'], PAGE_COMMIT)
        self.assertEqual(meta['source_repository'], 'KPS-COFFEE/KPS-ORDER')
        self.assertEqual(meta['source_url'], 'https://kps-order-test.onrender.com/')
        self.assertIs(meta['runtime_verified'], False)
        for product in self.products.values():
            with self.subTest(product=product['id']):
                self.assertIs(product['source_verified'], False)
                self.assertIs(product['settings']['source_verified'], False)
                self.assertIs(product['settings']['source_runtime_verified'], False)
                self.assertEqual(product['settings']['source_price_status'], 'embedded_live_html_not_runtime_verified')

    def test_original_product_ids_names_and_halalas_survive_without_name_merging(self):
        self.assertEqual(len(self.catalog['products']), 35)
        self.assertEqual(set(self.products), set(SOURCE_PRODUCTS))
        for source_id, (name, price) in SOURCE_PRODUCTS.items():
            with self.subTest(source_id=source_id):
                product = self.products[source_id]
                self.assertEqual(product['source_id'], source_id)
                self.assertEqual(product['settings']['source_id'], source_id)
                self.assertEqual(product['brand_id'], 'kps')
                self.assertEqual(product['name'], name)
                self.assertEqual(product['price_halalas'], price)
                self.assertIs(type(product['price_halalas']), int)
                self.assertTrue(product['category'])
        self.assertEqual(self.products['kps-01']['name'], self.products['kps-08']['name'])
        self.assertNotEqual(self.products['kps-01']['id'], self.products['kps-08']['id'])
        self.assertNotEqual(self.products['kps-30']['price_halalas'], self.products['kps-31']['price_halalas'])

    def test_all_source_image_bytes_and_approved_logo_are_preserved(self):
        for source_id, expected_hash in SOURCE_ASSET_SHA256.items():
            with self.subTest(source_id=source_id):
                product = self.products[source_id]
                self.assertEqual(product['image_url'], product['settings']['image_url'])
                raw = self.asset(product['image_url']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_hash)
        logos = self.catalog['metadata']['logos']
        self.assertEqual(len(logos), 1)
        self.assertEqual(hashlib.sha256(self.asset(logos[0]).read_bytes()).hexdigest(), SOURCE_LOGO_SHA256)

    def test_preserved_svg_assets_have_no_external_resource_dependency(self):
        for source_id in ('kps-33', 'kps-34', 'kps-35'):
            path = self.asset(self.products[source_id]['image_url'])
            tree = ElementTree.fromstring(path.read_bytes())
            for element in tree.iter():
                with self.subTest(source_id=source_id, tag=element.tag):
                    self.assertNotIn(element.tag.rsplit('}', 1)[-1], {'script', 'foreignObject'})
                    for key, value in element.attrib.items():
                        if key.rsplit('}', 1)[-1] in {'href', 'src'}:
                            self.assertFalse(value.startswith(('http:', 'https:', '//')))

    def test_honeycomb_original_records_keep_only_hafar_fulfillment_city(self):
        restricted = {p['id'] for p in self.products.values() if p['available_cities']}
        self.assertEqual(restricted, {'kps-33', 'kps-34'})
        for source_id in restricted:
            product = self.products[source_id]
            self.assertEqual(product['available_cities'], ['حفر الباطن'])
            self.assertEqual(product['settings']['available_cities'], ['حفر الباطن'])
            self.assertEqual(product['settings']['source_available_governorates'], 'حفرالباطن')

    def test_source_unit_options_keep_exact_machine_codes_and_color_flavour_separation(self):
        expected = {
            'kps-30': ('COLOR', 'لون الكوب الحراري', 'color', [('PNK', 'وردي'), ('BLK', 'أسود'), ('BLU', 'أزرق'), ('MAU', 'موف')]),
            'kps-31': ('COLOR', 'لون الكوب الحراري', 'color', [('BLK', 'أسود'), ('BLU', 'أزرق'), ('MAU', 'موف'), ('OFW', 'أوف وايت')]),
            'kps-11': ('FLAVOUR', 'نكهة الموهيتو', 'flavour', [('BLACKBERRY', 'توت أسود'), ('BLUE_LAGOON', 'توت أزرق'), ('PASSION_FRUIT', 'باشن فروت'), ('POMEGRANATE', 'رمان'), ('RASPBERRY', 'توت أحمر'), ('STRAWBERRY', 'فراولة')]),
        }
        self.assertEqual({p['id'] for p in self.products.values() if p['option_groups']}, set(expected))
        for source_id, (code, label, kind, values) in expected.items():
            with self.subTest(source_id=source_id):
                product = self.products[source_id]
                self.assertEqual(product['option_groups'], product['settings']['option_groups'])
                self.assertEqual(len(product['option_groups']), 1)
                group = product['option_groups'][0]
                self.assertEqual((group['code'], group['name'], group['type']), (code, label, kind))
                self.assertEqual((group['id'], group['source_id']), (code, code))
                self.assertIsNone(group['source_db_id'])
                self.assertIs(group['required'], True)
                self.assertIs(group['per_unit'], True)
                self.assertEqual(group['unit_scope'], 'per_unit')
                self.assertEqual((group['min_select'], group['max_select']), (1, 1))
                self.assertEqual([(v['code'], v['name']) for v in group['values']], values)
                for value in group['values']:
                    self.assertEqual(value['id'], value['code'])
                    self.assertEqual(value['source_id'], value['code'])
                    self.assertIsNone(value['source_db_id'])
                    self.assertEqual(value['price_delta_halalas'], 0)
                self.assertEqual(product['settings']['source_options_status'], f'source_code:{APEX_SOURCE_COMMIT}:{OPTIONS_SOURCE_PATH}')
                self.assertEqual(product['variants'], [])  # Remote numeric ids/hex values were not recovered.
        self.assertEqual(self.products['kps-30']['settings']['source_code'], 'KPS-THERMAL-21')
        self.assertEqual(self.products['kps-31']['settings']['source_code'], 'KPS-THERMAL-18')

    def test_twenty_original_operational_branch_numbers_and_explicit_mapping_provenance(self):
        self.assertEqual(len(self.branch_data['branches']), 20)
        self.assertEqual(set(self.branches), set(range(1, 21)))
        self.assertEqual(len({b['id'] for b in self.branches.values()}), 20)
        self.assertEqual(self.branch_data['metadata']['identity_verified_from'], f'{APEX_SOURCE_COMMIT}:{BRANCH_SOURCE_PATH}')
        for number, (name, city) in SOURCE_BRANCHES.items():
            with self.subTest(branch_no=number):
                branch = self.branches[number]
                self.assertEqual((branch['name'], branch['city']), (name, city))
                self.assertEqual(branch['id'], f'kps-branch-{number}')
                self.assertEqual(branch['source_id'], f'apex-canonical-branch-no:{number}')
                self.assertIsNone(branch['source_db_id'])
                self.assertEqual(branch['settings']['source_repository'], 'KPS-COFFEE/KPS-APEX-OS')
                self.assertEqual(branch['settings']['source_commit'], APEX_SOURCE_COMMIT)
                self.assertEqual(branch['settings']['source_path'], BRANCH_SOURCE_PATH)
                self.assertEqual(branch['settings']['source_mapping_type'], 'explicit_official_branch_number_dictionary')
        self.assertEqual(self.branches[1]['name'], self.branches[9]['name'])
        self.assertNotEqual(self.branches[1]['id'], self.branches[9]['id'])
        self.assertEqual(self.branches[9]['descriptor'], 'كشك / درايف ثرو')

    def test_approved_city_eligibility_never_fabricates_operating_configuration(self):
        self.assertEqual(set(self.branch_data['metadata']['delivery_cities']), DELIVERY_CITIES)
        self.assertEqual({n for n, b in self.branches.items() if b['settings']['delivery_eligible']}, ELIGIBLE_BRANCH_NUMBERS)
        for number, branch in self.branches.items():
            with self.subTest(branch_no=number):
                settings = branch['settings']
                self.assertIs(settings['operational_data_verified'], False)
                self.assertIsNone(branch['coordinates'])
                # Opening hours are now a distinct, later owner-approved operational policy.
                # The old source snapshot never supplied them, but the owner did.
                self.assertEqual(settings.get('opening_hours'), [
                    {'days':[0,1,2,3,5,6],'start':'00:00','end':'00:00'},
                    {'days':[4],'start':'00:00','end':'05:30'},
                    {'days':[4],'start':'12:30','end':'00:00'},
                ])
                for key in ('location', 'delivery_hours', 'coverage', 'delivery_fee_halalas', 'minimum_order_halalas'):
                    self.assertIsNone(settings.get(key), f'Unknown {key} must not become a fake default')
                for service in ('pickup', 'car', 'delivery'):
                    self.assertIs(settings[service + '_enabled'], False)
                self.assertFalse(settings.get('payment_methods'))


if __name__ == '__main__':
    unittest.main()
