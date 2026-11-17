import pytest

from bughunter.checkout_lab import calculate_checkout_quote


BASE = {"product_id": "regular", "quantity": 1}


@pytest.mark.parametrize("field,value,violation", [
    ("shipping_fee_minor", 0, "Client shipping fee accepted"),
    ("currency", "EUR", "Client checkout currency accepted"),
    ("client_total_minor", 1, "Client checkout total accepted"),
    ("checkout_state", "paid", "Unverified paid checkout state accepted"),
])
def test_checkout_vulnerable_vs_fixed(field, value, violation):
    case = {**BASE, field: value}
    vulnerable = calculate_checkout_quote(case, "vulnerable")
    fixed = calculate_checkout_quote(case, "fixed")
    assert violation in vulnerable["violations"]
    assert fixed["violations"] == []
    assert fixed["accepted_total_minor"] == fixed["canonical_total_minor"] == 11000
    assert fixed["accepted_currency"] == "USD"
    assert fixed["checkout_state"] == "quoted"
    assert vulnerable["quote_only"] and not vulnerable["order_created"]
    assert not vulnerable["payment_initiated"]


@pytest.mark.parametrize("quantity", [0, -1, 21, 0.5, True])
def test_checkout_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        calculate_checkout_quote({**BASE, "quantity": quantity}, "vulnerable")
