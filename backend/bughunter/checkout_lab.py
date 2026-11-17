"""Synthetic checkout quote oracle. No orders, payments or fulfillment occur."""
from __future__ import annotations

from .lab import CATALOG, CartValidationError

SHIPPING_MINOR = 1000
CURRENCY = "USD"


def calculate_checkout_quote(body: dict, mode: str) -> dict:
    if not isinstance(body, dict):
        raise ValueError("Checkout quote must be a JSON object")
    product_id = body.get("product_id")
    if product_id not in CATALOG:
        raise ValueError("Unknown lab product")
    quantity = body.get("quantity", 1)
    if type(quantity) is not int or not 1 <= quantity <= 20:
        raise CartValidationError("invalid_quantity", "Quantity must be between 1 and 20")
    canonical_minor = int(CATALOG[product_id]["price"] * 100) * quantity + SHIPPING_MINOR
    shipping = body.get("shipping_fee_minor", SHIPPING_MINOR)
    total = body.get("client_total_minor", canonical_minor)
    currency = body.get("currency", CURRENCY)
    state = body.get("checkout_state", "quoted")
    if (type(shipping) is not int or abs(shipping) > 1_000_000
            or type(total) is not int or abs(total) > 10_000_000
            or not isinstance(currency, str) or currency not in {"USD", "EUR"}
            or not isinstance(state, str) or state not in {"quoted", "paid"}):
        raise ValueError("Invalid synthetic checkout fields")
    accepted_shipping = SHIPPING_MINOR
    accepted_currency = CURRENCY
    accepted_state = "quoted"
    accepted_minor = canonical_minor
    violations: list[str] = []
    if mode == "vulnerable":
        accepted_shipping = shipping
        accepted_currency = currency
        accepted_state = state
        accepted_minor = total if "client_total_minor" in body else canonical_minor - SHIPPING_MINOR + shipping
        if shipping != SHIPPING_MINOR:
            violations.append("Client shipping fee accepted")
        if currency != CURRENCY:
            violations.append("Client checkout currency accepted")
        if total != canonical_minor and "client_total_minor" in body:
            violations.append("Client checkout total accepted")
        if state != "quoted":
            violations.append("Unverified paid checkout state accepted")
    return {
        "fixture": "synthetic-checkout-quote-v1", "lab_evidence": True, "quote_only": True,
        "order_created": False, "payment_initiated": False, "product_id": product_id,
        "quantity": quantity, "accepted_total_minor": accepted_minor,
        "canonical_total_minor": canonical_minor, "accepted_total": accepted_minor / 100,
        "canonical_total": canonical_minor / 100, "accepted_coupons": [],
        "accepted_currency": accepted_currency, "checkout_state": accepted_state,
        "accepted_shipping_minor": accepted_shipping, "violations": violations,
    }
