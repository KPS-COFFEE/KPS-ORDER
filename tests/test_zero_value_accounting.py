"""No-collection values must stay distinct from sales and unknown history."""
import unittest
from copy import deepcopy

from app.complimentary import accounting_values, prepare, KINDS, operational_view
from app.accounting_export import contract, render


class ZeroValueAccountingTests(unittest.TestCase):
    def test_operational_copy_exposes_type_and_preserves_private_accounting_evidence(self):
        for kind in KINDS:
            snapshot=prepare({'subtotal_halalas':8500,'delivery_fee_halalas':1000,'total_halalas':9500,
                              'notes':'Private accounting reason','items':[{'name':'Coffee','quantity':2,
                              'line_total_halalas':8500,'unit_price_halalas':4250}]},
                             kind,'Private accounting reason','manager')
            saved={'orders':[{'total_halalas':0,'snapshot':snapshot}],'summary':{'completed_orders':1}}
            before=deepcopy(saved)
            visible=operational_view(saved)
            self.assertEqual(visible['orders'][0]['snapshot']['complimentary'],{'kind':kind})
            self.assertNotIn('notes',visible['orders'][0]['snapshot'])
            self.assertEqual(visible['orders'][0]['snapshot']['items'][0]['quantity'],2)
            self.assertEqual(visible['summary']['completed_orders'],1)
            self.assertEqual(saved,before)
            self.assertEqual(accounting_values(saved['orders'][0])['amount'],8500)

    def test_each_recorded_reason_retains_menu_and_delivery_values(self):
        quote={'subtotal_halalas':8500,'delivery_fee_halalas':1000,'total_halalas':9500,'items':[]}
        original=deepcopy(quote)
        for kind, labels in KINDS.items():
            snapshot=prepare(quote,kind,'Approved reason','manager')
            order={'total_halalas':0,'snapshot':snapshot}
            for lang,index in [('ar',0),('en',1)]:
                row=accounting_values(order,lang)
                self.assertEqual((row['amount'],row['delivery_value'],row['payable']),(8500,1000,0))
                self.assertEqual(row['kind'],labels[index])
                self.assertEqual(row['reason'],'Approved reason')
                self.assertIn('لا مديونية' if lang=='ar' else 'no customer',row['treatment'])
            self.assertEqual(quote,original)

    def test_unclassified_zero_uses_saved_gift_prices_and_flags_missing_reason(self):
        order={'total_halalas':0,'snapshot':{'subtotal_halalas':0,'delivery_fee_halalas':0,
               'items':[{'line_total_halalas':0,'original_line_total_halalas':2500},
                        {'line_total_halalas':0,'original_line_total_halalas':1500}]}}
        row=accounting_values(order,'en')
        self.assertEqual((row['amount'],row['payable']),(4000,0))
        self.assertIn('review required',row['reason'])
        self.assertIn('Zero-value',row['kind'])

    def test_missing_historical_values_are_not_replaced_with_zero(self):
        row=accounting_values({'total_halalas':0,'snapshot':{}},'en')
        self.assertEqual(row['amount'],'Not recorded')
        self.assertEqual(row['delivery_value'],'Not recorded')
        self.assertEqual(row['payable'],0)

    def test_cancellation_reason_stays_separate_from_original_value(self):
        row=accounting_values({'total_halalas':6000,'preparation_status':'cancelled',
              'cancel_details':{'reason':'Customer cancelled'},
              'snapshot':{'subtotal_halalas':5000,'delivery_fee_halalas':1000}},'en')
        self.assertEqual((row['amount'],row['delivery_value'],row['payable']),(5000,1000,0))
        self.assertEqual(row['reason'],'Customer cancelled')

    def test_bilingual_report_and_xlsx_share_values_without_sales_totals(self):
        data={'group':'complimentary','rows':[{'amount':8500,'delivery_value':1000,'payable':0,
              'kind':'Hospitality','reason':'Approved reason'}],
              'period':{'start':'2026-10-23','end':'2026-10-23'},'definitions':'Reference values'}
        for lang in ('ar','en'):
            report=contract(data,lang)
            self.assertEqual(report['totals'],{})
            self.assertEqual(report['rows'][0]['amount'],8500)
            columns={c['key']:c for c in report['columns']}
            self.assertTrue(columns['delivery_value']['money'])
            self.assertTrue(columns['payable']['money'])
            self.assertIn('Reference' if lang=='en' else 'المرجعية',columns['amount']['label'])
            self.assertTrue(render(data,'xlsx',lang).startswith(b'PK'))

    def test_old_frozen_reports_remain_unchanged_and_missing_fields_are_explicit(self):
        data={'group':'complimentary','rows':[{'amount':8500}]}
        report=contract(data,'en')
        self.assertEqual(report['rows'][0]['payable'],'Not recorded')
        self.assertEqual(report['rows'][0]['delivery_value'],'Not recorded')
        self.assertEqual(data['rows'],[{'amount':8500}])


if __name__=='__main__':
    unittest.main()
