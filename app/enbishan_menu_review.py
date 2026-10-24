"""Source-matched final owner review; pure preparation, never startup writes."""
from copy import deepcopy

from . import db, enbishan_menu_assets, menu_variants

DECISION = 'owner-final-20261022'
SOURCE_SHA256 = 'ca574c39384e250535397f4bac40dfabc234487ffee8fa1d623344e085402a1e'
ROWS_DIGEST = '5eff5cc9b27f3776e642657c3bec195d68dad40615abf67a86e4ce34bccbd9c7'
HIDDEN = frozenset({'enb-d1dbec7c9b6807c2ac279729', 'enb-b668a68a8f8104f89fdc8567',
                    'enb-078a4053bf0eef33bfe3557f'})
PHOTOS = {
    'enb-88282747bcacc9e36651a920': 'mojito-grape.jpeg',
    'enb-b54c043c94afde2a0947da54': 'mojito-mahashi.jpeg',
    'enb-45c335eea6e9a027a0a25d80': 'mojito-fattah.jpeg',
    'enb-d0258154e9dbb5a87e7db80f': 'ramadan-box.jpeg',
    'enb-6a9fa4ce56e6ec111966afce': 'crispy-small.jpeg',
}
MIX_SOURCE = 'enb-9aaf08c9efbb70f893716679'
MIX_SIZES = {
    'v-a0c277ce10ff7c635fd1e9ec': ('٦ حبات (٤ ورق عنب + ٢ ملفوف)', '6 pieces (4 grape leaves + 2 cabbage)'),
    'v-5eaf865e16c194c339bace33': ('١٢ حبة (٨ ورق عنب + ٤ ملفوف)', '12 pieces (8 grape leaves + 4 cabbage)'),
    'v-b804a3f4814cc6028d7ef9da': ('٢٤ حبة (١٦ ورق عنب + ٨ ملفوف)', '24 pieces (16 grape leaves + 8 cabbage)'),
}
CRISPY_SMALL_SIZE = 'v-774fa9ee015940670c61c84c'
MOJITO_PICKUP_ONLY_SOURCES = frozenset({
    'enb-88282747bcacc9e36651a920',
    'enb-b54c043c94afde2a0947da54',
    'enb-45c335eea6e9a027a0a25d80',
})


def pickup_only(product):
    """Owner decision 2026-10-23, scoped to original Enbishan mojito cards."""
    return (product.get('brand_id') == 'enbishan'
            and product.get('settings', {}).get('source_id') in MOJITO_PICKUP_ONLY_SOURCES)


def apply(prepared, *, source_sha256, rows):
    """Apply only this exact reviewed source; retain original records and prices.

    The existing owner preview/current-catalog/reviewed-digest/import controls
    remain authoritative. Output is still disabled, unverified draft data.
    """
    if source_sha256 != SOURCE_SHA256 or db.digest(rows) != ROWS_DIGEST:
        raise ValueError('The final owner review requires the exact reviewed source rows and workbook.')
    result = deepcopy(prepared)
    products = result['products']
    by_source = {p['settings']['source_id']: p for p in products}
    if (len(products) != 31 or len(by_source) != 31
            or not (HIDDEN | set(PHOTOS) | {MIX_SOURCE}) <= by_source.keys()
            or any(p['brand_id'] != 'enbishan' for p in products)):
        raise ValueError('The final owner review requires the complete 31-card Enbishan source.')
    for source_id in HIDDEN:
        by_source[source_id]['settings'].update(purchasable=False,
            owner_visibility={'decision_id': DECISION, 'visible': False, 'deleted': False})
    for source_id, filename in PHOTOS.items():
        product = by_source[source_id]
        settings = product['settings']
        path = '/assets/enbishan-menu/owner-review/' + filename
        if not enbishan_menu_assets.valid_bundled(path):
            raise ValueError('The approved owner review image is missing or changed: ' + filename)
        settings['owner_review_previous_images'] = deepcopy({
            key: settings.get(key) for key in ('image_url', 'variant_images')})
        settings['image_url'] = path
        settings['owner_image_approval'] = {'decision_id': DECISION, 'byte_preserved_original': True}
        for variant in settings.get('variant_images', []):
            if source_id != 'enb-6a9fa4ce56e6ec111966afce' or variant['options'].get('SIZE') == CRISPY_SMALL_SIZE:
                variant['image_url'] = path
    settings = by_source[MIX_SOURCE]['settings']
    size = next(g for g in settings['option_groups'] if g['code'] == 'SIZE')
    original_values = {v['code']: v for v in size['values']}
    if not set(MIX_SIZES) < original_values.keys():
        raise ValueError('The historical mixed grape/cabbage sizes changed.')
    # The original 36-piece option and all source prices/images remain in this
    # immutable import snapshot, outside the orderable three-size matrix.
    settings['owner_review_original_variants'] = deepcopy({key: settings[key] for key in
        ('option_groups', 'variant_price_matrix', 'variant_images', 'source_image_references')})
    settings['owner_review_retired_variant_codes'] = sorted(original_values.keys() - MIX_SIZES.keys())
    size['values'] = [{**original_values[code], 'name': labels[0], 'name_en': labels[1]}
                      for code, labels in MIX_SIZES.items()]
    for field in ('variant_price_matrix', 'variant_images', 'source_image_references'):
        settings[field] = [v for v in settings[field] if v['options'].get('SIZE') in MIX_SIZES]
    for product in products:
        product['settings']['owner_menu_review'] = DECISION
        menu_variants.validated(product)
    result.update(owner_review=DECISION, retained_products=31, review_visible_products=28,
                  ordering_enabled=False, catalog_digest=db.digest(products))
    return result
