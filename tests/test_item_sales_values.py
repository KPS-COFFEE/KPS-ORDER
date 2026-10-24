import unittest
from app.item_sales import new_item, add_line
from app.accounting_export import contract


class ItemSalesTests(unittest.TestCase):
    def test_sales_and_each_free_reason_never_mix(self):
        row=new_item('coffee','Coffee')
        item={'quantity':2,'line_total_halalas':3000}
        self.assertTrue(add_line(row,item,{'total_halalas':4000,'snapshot':{}}))
        for kind in ('hospitality','advertising','compensation'):
            self.assertFalse(add_line(row,{**item,'line_total_halalas':0},
                             {'total_halalas':0,'snapshot':{'complimentary':{'kind':kind}}}))
            self.assertEqual(row[kind+'_quantity'],2)
        self.assertFalse(add_line(row,{**item,'line_total_halalas':0,'is_gift':True},
                         {'total_halalas':4000,'snapshot':{}}))
        self.assertFalse(add_line(row,{**item,'line_total_halalas':0},
                         {'total_halalas':0,'snapshot':{}}))
        self.assertEqual((row['quantity'],row['gross'],row['gift_quantity'],row['zero_value_quantity']),(2,3000,2,2))

    def test_unsold_catalog_item_remains_zero_and_export_totals_keep_free_units_separate(self):
        rows=[new_item('coffee','Coffee'),new_item('unsold','Unsold item')]
        add_line(rows[0],{'quantity':1,'line_total_halalas':1000},{'total_halalas':1000,'snapshot':{}})
        add_line(rows[0],{'quantity':3,'line_total_halalas':0,'is_gift':True},{'total_halalas':1000,'snapshot':{}})
        data=contract({'group':'items','rows':rows},'en')
        self.assertEqual(len(data['rows']),2)
        self.assertEqual(data['rows'][1]['quantity'],0)
        self.assertEqual((data['totals']['quantity'],data['totals']['gross'],data['totals']['gift_quantity']),(1,1000,3))
        self.assertNotIn('correct',data['totals'])

    def test_saved_history_without_free_breakdown_does_not_claim_zero_gifts(self):
        historical={'group':'items','rows':[{'label':'Old coffee','quantity':5,'gross':5000}]}
        for lang,unknown in [('ar','غير موثقة'),('en','Not recorded')]:
            report=contract(historical,lang)
            self.assertEqual(report['totals']['quantity'],5)
            self.assertEqual(report['totals']['gross'],5000)
            for key in ('gift_quantity','hospitality_quantity','advertising_quantity','compensation_quantity','zero_value_quantity'):
                self.assertEqual(report['rows'][0][key],unknown)
                self.assertEqual(report['totals'][key],unknown)
        self.assertNotIn('gift_quantity',historical['rows'][0])


if __name__=='__main__':
    unittest.main()
