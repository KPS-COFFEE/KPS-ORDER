"""Driver closure privacy and terminal reconciliation."""
HIDDEN = frozenset({
    'expected_halalas', 'difference_halalas', 'expected_card_halalas',
    'card_difference_halalas', 'collections', 'refunds', 'custody_events',
    'opening_halalas', 'closure_exception', 'exception_reason',
})

def driver_view(user, value, shift_money=False):
    from .complimentary import contains_complimentary, operational_view
    visible = operational_view(value) if contains_complimentary(value) else value
    return _driver_view(user, visible, shift_money)


def _driver_view(user, value, shift_money=False):
    if user.get('role') != 'driver':
        return value
    if isinstance(value, dict):
        return {key: _driver_view(user, item, shift_money) for key, item in value.items() if key not in HIDDEN and not (shift_money and key.endswith("_halalas"))}
    if isinstance(value, list):
        return [_driver_view(user, item, shift_money) for item in value]
    return value

def card_reconciliation(c, sid, actual, external_card=0):
    row = c.execute("""SELECT
        (SELECT coalesce(sum(amount_halalas),0) FROM collections WHERE shift_id=%s AND method='card')
        - (SELECT coalesce(sum(amount_halalas),0) FROM refunds WHERE shift_id=%s AND method='card') AS expected""", (sid,sid)).fetchone()
    expected = int(row['expected']) + external_card
    return {'terminal_total_halalas': actual, 'expected_card_halalas': expected,
            'card_difference_halalas': actual-expected}
