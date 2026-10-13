"""Driver closure privacy and terminal reconciliation."""
HIDDEN = frozenset({
    'expected_halalas', 'difference_halalas', 'expected_card_halalas',
    'card_difference_halalas', 'collections', 'refunds', 'custody_events',
    'opening_halalas', 'closure_exception', 'exception_reason',
})

def driver_view(user, value):
    if user.get('role') != 'driver':
        return value
    if isinstance(value, dict):
        return {key: driver_view(user, item) for key, item in value.items() if key not in HIDDEN}
    if isinstance(value, list):
        return [driver_view(user, item) for item in value]
    return value

def card_reconciliation(c, sid, actual):
    row = c.execute("""SELECT
        (SELECT coalesce(sum(amount_halalas),0) FROM collections WHERE shift_id=%s AND method='card')
        - (SELECT coalesce(sum(amount_halalas),0) FROM refunds WHERE shift_id=%s AND method='card') AS expected""", (sid,sid)).fetchone()
    expected = int(row['expected'])
    return {'terminal_total_halalas': actual, 'expected_card_halalas': expected,
            'card_difference_halalas': actual-expected}
