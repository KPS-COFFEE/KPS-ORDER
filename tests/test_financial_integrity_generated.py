"""Independent deterministic money/evidence probes; no database or operational writes."""
import copy
import random
import unittest
from app.financial_integrity import inspect_snapshot


class GeneratedFinancialIntegrity(unittest.TestCase):
    def samples(self):
        rng = random.Random(20261115)
        for i in range(500):
            cash, card = rng.randrange(10**8), rng.randrange(10**8)
            cash_refund, card_refund = rng.randrange(cash + 1), rng.randrange(card + 1)
            completed = bool(i % 2)
            sid=f'shift-{i}'
            yield dict(
                shift=dict(id=sid, opening_halalas=0, submission=dict(expected_halalas=cash-cash_refund, counted_halalas=cash-cash_refund, difference_halalas=0)),
                custody_events=[dict(id=f'cash-effect-{i}', shift_id=sid, kind='collection', reference_id=f'cash-{i}', amount_halalas=cash),
                                dict(id=f'refund-effect-{i}', shift_id=sid, kind='refund', reference_id=f'cash-refund-{i}', amount_halalas=-cash_refund)],
                transfers=[],
                orders=[dict(id=f'order-{i}', total_halalas=cash + card, fulfilled_in_report=completed)],
                collections=[dict(id=f'cash-{i}', shift_id=sid, method='cash', amount_halalas=cash),
                             dict(id=f'card-{i}', method='card', amount_halalas=card)],
                refunds=[dict(id=f'cash-refund-{i}', shift_id=sid, method='cash', amount_halalas=cash_refund),
                         dict(id=f'card-refund-{i}', method='card', amount_halalas=card_refund)],
                summary=dict(unique_orders=1, completed_orders=int(completed),
                             gross_completed_halalas=(cash + card) if completed else 0,
                             cash_collected_halalas=cash, card_collected_halalas=card,
                             cash_refunded_halalas=cash_refund, card_refunded_halalas=card_refund,
                             net_collections_halalas=cash + card - cash_refund - card_refund))

    def test_valid_money_and_refunds_conserve_every_halalah_without_mutation(self):
        for source in self.samples():
            before = copy.deepcopy(source)
            self.assertTrue(inspect_snapshot(source)['verified'], inspect_snapshot(source))
            self.assertEqual(source, before)

    def test_equal_and_opposite_tender_errors_never_pass_a_matching_net(self):
        for source in self.samples():
            source['summary']['cash_collected_halalas'] += 1
            source['summary']['card_collected_halalas'] -= 1
            result = inspect_snapshot(source)
            self.assertFalse(result['verified'])
            codes = {issue['code'] for issue in result['issues']}
            self.assertIn('cash_collected_halalas', codes)
            self.assertIn('card_collected_halalas', codes)

    def test_replayed_receipt_or_refund_is_rejected_even_with_adjusted_totals(self):
        for source in self.samples():
            for collection in (True, False):
                forged = copy.deepcopy(source)
                row = forged['collections' if collection else 'refunds'][0]
                forged['collections' if collection else 'refunds'].append(copy.deepcopy(row))
                key = 'cash_collected_halalas' if collection else 'cash_refunded_halalas'
                forged['summary'][key] += row['amount_halalas']
                forged['summary']['net_collections_halalas'] += (1 if collection else -1) * row['amount_halalas']
                result = inspect_snapshot(forged)
                self.assertFalse(result['verified'])
                self.assertIn('DUPLICATE_OR_MISSING_ID', {issue['code'] for issue in result['issues']})

    def test_a_boolean_or_missing_value_cannot_document_a_real_zero(self):
        for value in (False, True, None, '0', 0.0):
            source = next(self.samples())
            source['summary']['cash_collected_halalas'] = value
            self.assertFalse(inspect_snapshot(source)['verified'])


if __name__ == '__main__':
    unittest.main()
