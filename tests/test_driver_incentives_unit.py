"""Eligibility, scope, audit input and financial privacy invariants."""
import copy
import unittest
from datetime import date
from unittest.mock import Mock
from fastapi import HTTPException
from pydantic import ValidationError
from app import driver_incentives as di, blind_closure, accounting_export

class IncentivesTests(unittest.TestCase):
    driver={'id':'d','brand_id':'b','role':'driver','branch_ids':['one']}
    def row(self,**changes):
        return dict(order_id='o',order_reference='20',task_id='t',shift_id='s',
            driver_id='d',driver_name='Driver',operational_day=date(2026,10,13),
            shift_status='closed',task_status='delivered',preparation_status='completed',
            payment_status='paid',refund_ids=[],**changes)
    def decision(self,row,party):
        return {**row,'decision_task_id':row['task_id'],'saved_incident_key':di.incident(row)[1],
                'responsibility':party,'reason':'Actual reviewed reason','decision_version':1}
    def test_only_paid_delivered_approved_counts(self):
        self.assertEqual(di.classify(self.row())['state'],'eligible')
        for field,value,state in [('shift_status','submitted','awaiting_approval'),
                                  ('payment_status','pending','incomplete'),('task_status','assigned','incomplete')]:
            r=self.row();r[field]=value
            self.assertEqual(di.classify(r)['state'],state)
    def test_cancellation_and_refund_responsibility(self):
        for field,value in [('task_status','cancelled'),('preparation_status','cancelled'),('refund_ids',['r1'])]:
            r=self.row();r[field]=value
            self.assertEqual(di.classify(r)['state'],'pending_review')
            for party,state in [('driver','excluded'),('customer','eligible'),('branch','eligible')]:
                result=di.classify(self.decision(r,party))
                self.assertEqual(result['state'],state);self.assertEqual(result['reason'],'Actual reviewed reason')
    def test_new_refund_or_reassignment_invalidates_old_decision(self):
        r=self.row()
        r['refund_ids']=['r1'];r=self.decision(r,'customer')
        r['refund_ids'].append('r2')
        self.assertEqual(di.classify(r)['state'],'pending_review')
        r['refund_ids']=['r1'];r['task_id']='new-task'
        self.assertEqual(di.classify(r)['state'],'pending_review')
    def test_approved_exception_still_waits_for_shift(self):
        r=self.row();r.update(refund_ids=['r'],shift_status='submitted')
        self.assertEqual(di.classify(self.decision(r,'branch'))['state'],'awaiting_approval')
    def test_daily_tiers_reset_and_monthly_counter_accumulates(self):
        entries=[]
        for day,count in [('2026-10-01',20),('2026-10-02',25),('2026-10-03',30),('2026-10-04',33)]:
            entries.extend(dict(driver_id='d',driver_name='Driver',day=day,state='eligible') for _ in range(count))
        rows=di.aggregate(entries)
        self.assertEqual([r['cumulative'] for r in rows],[20,45,75,108])
        self.assertEqual([(r['tier_1'],r['tier_2'],r['tier_3'],r['tier_4']) for r in rows],[(20,0,0,0),(20,5,0,0),(20,5,5,0),(20,5,5,3)])
    def test_zero_task_shift_is_present(self):
        rows=di.aggregate([], [dict(user_id='d',driver_name='Driver',operational_day=date(2026,10,13),status='closed')])
        self.assertEqual(rows[0]['eligible'],0);self.assertEqual(rows[0]['open_shifts'],0)
    def test_driver_cannot_request_another_driver_or_write_decision(self):
        with self.assertRaises(HTTPException) as e:di.achievement('2026-10','other',self.driver)
        self.assertEqual(e.exception.status_code,403)
        body=di.Decision(task_id='t',incident_key='a'*64,version=0,responsibility='driver',reason='Reviewed')
        with self.assertRaises(HTTPException) as e:di.decide('o',body,Mock(),self.driver)
        self.assertEqual(e.exception.status_code,403)
    def test_decision_requires_reason_party_and_valid_revision(self):
        valid=dict(task_id='t',incident_key='a'*64,version=0,responsibility='customer',reason='Customer cancelled')
        for key,value in [('reason','  '),('responsibility','unknown'),('version',True),('version',-1)]:
            with self.assertRaises(ValidationError):di.Decision(**{**valid,key:value})
    def test_scope_and_canonical_task_selection_precede_counting(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[]
        di.source_rows(c,self.driver,date(2026,10,1),date(2026,10,31))
        sql,args=c.execute.call_args.args
        self.assertIn('LIMIT 1',sql);self.assertIn("NOT IN ('transferred','reassigned')",sql)
        self.assertIn('s.user_id=%s',sql);self.assertIn('o.brand_id=%s',sql)
        self.assertIn('s.branch_ids <@',sql);self.assertIn('d',args)
        scope,args=di.scoped({**self.driver,'role':'delivery_manager'})
        self.assertIn('manager_shift_id',scope);self.assertEqual(args,['d'])
    def test_pending_responsibility_blocks_manager_approval(self):
        c=Mock();r=self.row();r['task_status']='cancelled'
        c.execute.return_value.fetchall.return_value=[r]
        with self.assertRaises(HTTPException) as e:di.require_decisions(c,self.driver,{'id':'s','kind':'driver'})
        self.assertEqual(e.exception.status_code,409)
    def test_all_shift_amounts_removed_but_input_draft_can_restore_actual(self):
        original={'submission':{'counted_halalas':100,'terminal_total_halalas':200,'expected_halalas':150},
                  'transfers':[{'amount_halalas':100,'status':'confirmed'}]}
        saved=copy.deepcopy(original)
        self.assertEqual(blind_closure.driver_view(self.driver,original,True),{'submission':{},'transfers':[{'status':'confirmed'}]})
        self.assertEqual(blind_closure.driver_view(self.driver,original)['submission'],{'counted_halalas':100,'terminal_total_halalas':200})
        self.assertEqual(saved,original)
    def test_accountant_count_export_has_no_money_columns(self):
        for group in ['driver_incentives','driver_incentive_details']:
            self.assertFalse(set(accounting_export.COLUMNS[group]) & accounting_export.MONEY)
            result=accounting_export.contract({'group':group,'rows':[]},'en')
            self.assertTrue(all(not c['money'] for c in result['columns']))

if __name__=='__main__':unittest.main()
