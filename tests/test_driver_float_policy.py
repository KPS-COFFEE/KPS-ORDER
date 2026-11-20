import pytest
from fastapi import HTTPException
from app.driver_float import validate_standing_amount

@pytest.mark.parametrize('amount',[0,20000])
def test_standing_float_matches_approved_fixed_policy(amount):
    validate_standing_amount(amount)

@pytest.mark.parametrize('amount',[True,False,None,'20000',1,10000,40000,-1])
def test_new_float_cannot_create_arbitrary_or_duplicate_allowance(amount):
    with pytest.raises(HTTPException) as exc:validate_standing_amount(amount)
    assert exc.value.status_code==422
