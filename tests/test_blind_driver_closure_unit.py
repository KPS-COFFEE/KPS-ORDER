"""Focused privacy and financial invariants; no live data or database connection."""
import copy
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from pydantic import ValidationError
from app import operations as op, blind_closure, reporting

class BlindClosureTests(unittest.TestCase):
    driver={'id':'driver','role':'driver','brand_id':'b','branch_ids':['one']}
    manager={'id':'manager','role':'delivery_manager','brand_id':'b','branch_ids':['one']}
    shift={'id':'shift','kind':'driver','user_id':'driver','brand_id':'b','branch_ids':['one'], 'status':'closing','manager_shift_id':'manager-shift','opening_halalas':0}

    def test_driver_response_redacts_nested_financial_sources_without_mutating_storage(self):
        value={'expected_halalas':6000,'collections':[{'amount_halalas':6000}], 'refunds':[], 'custody_events':[], 'shifts':[{'opening_halalas':500,'submission':{'counted_halalas':5900,'terminal_total_halalas':10000,'expected_halalas':6000,'difference_halalas':-100,'expected_card_halalas':11000,'card_difference_halalas':-1000}}]}
        original=copy.deepcopy(value)
        visible=blind_closure.driver_view(self.driver,value)
        self.assertEqual(visible,{'shifts':[{'submission':{'counted_halalas':5900,'terminal_total_halalas':10000}}]})
        self.assertEqual(value,original)
        self.assertIs(blind_closure.driver_view(self.manager,value),value)

    def test_terminal_input_rejects_negative_fractional_bool_and_overflow(self):
        for value in [-1,1.2,True,op.MAX_HALALAS+1]:
            with self.subTest(value=value),self.assertRaises(ValidationError):
                op.ClosureSubmit(counted_halalas=0,handover_halalas=0,terminal_total_halalas=value)

    def run_submit(self,terminal=1200,handover=True,counted=800,retained=0):
        body=op.ClosureSubmit(counted_halalas=counted,handover_halalas=counted-retained,retained_halalas=retained,terminal_total_halalas=terminal,
            handover=op.ClosureHandover(recipient_user_id='manager',to_shift_id='manager-shift',reference='D1') if handover else None)
        c=Mock()
        def execute(sql,params=None):
            response=Mock()
            if 'manager_handovers' in sql or 'custody_transfers' in sql:response.fetchone.return_value=None
            elif 'AS expected' in sql:response.fetchone.return_value={'expected':1000}
            elif 'SELECT payment_terminal' in sql:response.fetchone.return_value={'payment_terminal':{'terminal_id':'T1'}}
            else:response.fetchone.return_value=None
            return response
        c.execute.side_effect=execute
        with patch.object(op,'lock_shift',return_value=copy.deepcopy(self.shift)),patch.object(op,'shift_access'),patch.object(op,'unresolved',return_value=0),patch.object(op,'balance',return_value=900),patch.object(op.shift_vehicle,'finish'),patch.object(op,'create_custody') as custody,patch.object(op.db,'audit'):
            result=op.submit_close_in_tx(c,self.driver,'shift',body)
            self.assertEqual(custody.call_args.args[2].amount_halalas,counted)
            return result

    def test_submission_freezes_separate_cash_and_card_differences(self):
        sub=self.run_submit()['submission']
        self.assertEqual(sub['difference_halalas'],-100)
        self.assertEqual(sub['card_difference_halalas'],200)
        self.assertEqual(sub['expected_card_halalas'],1000)
        self.assertEqual(sub['terminal_total_halalas'],1200)

    def test_zero_terminal_is_explicitly_valid(self):
        self.assertEqual(self.run_submit(terminal=0)['submission']['terminal_total_halalas'],0)

    def test_driver_cannot_omit_terminal_or_cash_handover_or_retain_cash(self):
        for args in [{'terminal':None},{'handover':False},{'retained':100}]:
            with self.subTest(args=args),self.assertRaises(HTTPException) as error:self.run_submit(**args)
            self.assertEqual(error.exception.status_code,422)

    def test_card_discrepancy_keeps_actual_receipt_requirement_without_manager_financial_decision(self):
        s={**self.shift,'status':'submitted','submission':{'difference_halalas':0,'card_difference_halalas':100,'retained_halalas':0}}
        c=Mock()
        def execute(sql,params=None):
            result=Mock()
            result.fetchone.return_value={'n':0,'nrows':0} if 'count(*) nrows' in sql else None
            return result
        c.execute.side_effect=execute
        with patch('app.driver_incentives.require_decisions'),patch.object(op,'lock_shift',return_value=s),patch.object(op,'shift_access'),patch.object(op,'unresolved',return_value=0),self.assertRaises(HTTPException) as error:
            op.approve_close_in_tx(c,self.manager,'shift',op.Review())
        self.assertEqual(error.exception.status_code,409)
        self.assertIn('تأكيد فعلي',error.exception.detail)

    def test_open_trip_blocks_approval_before_financial_review(self):
        s={**self.shift,'status':'submitted','submission':{'difference_halalas':0,'card_difference_halalas':0,'retained_halalas':0}}
        c=Mock()
        c.execute.return_value.fetchone.return_value={'trip_id':'unreturned-trip'}
        with patch('app.driver_incentives.require_decisions') as financial_review,patch.object(op,'lock_shift',return_value=s),patch.object(op,'shift_access'),self.assertRaises(HTTPException) as error:
            op.approve_close_in_tx(c,self.manager,'shift',op.Review())
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(error.exception.detail['code'],'TRIP_RETURN_REQUIRED')
        financial_review.assert_not_called()
        self.assertIn('driver_work_trips',c.execute.call_args.args[0])
        self.assertIn('returned_at IS NULL',c.execute.call_args.args[0])

    def test_driver_cannot_read_financial_reports_even_when_owner_of_shift(self):
        row={'brand_id':'b','branch_ids':['one'],'user_id':'driver','snapshot':{'manager_user_id':'manager'}}
        self.assertFalse(reporting.can_read(self.driver,row))
        self.assertTrue(reporting.can_read(self.manager,row))

    def test_replayed_mutation_is_redacted_at_response_boundary(self):
        c=Mock();c.execute.return_value.fetchone.return_value=None
        stored={'expected_halalas':100,'submission':{'difference_halalas':5,'terminal_total_halalas':200}}
        with patch.object(op.db,'idempotent',return_value=stored):
            result=op.mutate(c,self.driver,Mock(headers={}), 'shift.submit:shift',{},Mock())
        self.assertEqual(result,{'submission':{}})
        self.assertIn('expected_halalas',stored)

if __name__=='__main__':unittest.main()
