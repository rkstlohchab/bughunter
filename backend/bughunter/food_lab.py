"""Deterministic food-order quotes for the disposable loopback shop.

All prices are synthetic minor currency units. No payment or order is created.
The vulnerable variant intentionally trusts the indicated client fields.
"""
MENU = {
    'bowl': {'name': 'Vegetable rice bowl', 'price_minor': 1800, 'coupon_eligible': True},
    'family-meal': {'name': 'Family meal bundle', 'price_minor': 4200, 'coupon_eligible': False},
}
ADDONS = {'drink': 300, 'dessert': 500}
DELIVERY_MINOR = 250


def calculate_food_cart(body, mode):
    from .lab import CartValidationError
    if not isinstance(body, dict): raise ValueError('Food cart must be a JSON object')
    product = body.get('product_id')
    if not isinstance(product, str) or product not in MENU: raise ValueError('Unknown menu item')
    item = MENU[product]
    quantity = body.get('quantity', 1)
    if type(quantity) is not int or abs(quantity) > 1000:
        raise CartValidationError('invalid_quantity', 'Food quantity must be a bounded integer')
    if mode != 'vulnerable' and not 1 <= quantity <= 20:
        raise CartValidationError('invalid_quantity', 'Quantity must be an integer from 1 to 20')
    addons = body.get('addons', [])
    if (not isinstance(addons, list) or len(addons) > 10
            or any(not isinstance(a, str) or a not in ADDONS for a in addons)):
        raise ValueError('Unknown or excessive food addons')
    coupons = body.get('coupon', [])
    if isinstance(coupons, str): coupons = [coupons] if coupons else []
    if not isinstance(coupons, list) or len(coupons) > 10 or any(c != 'FOOD10' for c in coupons):
        raise ValueError('Unknown food coupon')
    addon_total = sum(ADDONS[a] for a in addons)
    canonical_subtotal = (item['price_minor'] + addon_total) * quantity
    eligible = item['coupon_eligible'] and canonical_subtotal >= 2000
    canonical_coupons = ['FOOD10'] if coupons and eligible else []
    canonical_total = (canonical_subtotal * 90 // 100 if canonical_coupons else canonical_subtotal) + DELIVERY_MINOR
    accepted_subtotal, delivery, accepted_coupons = canonical_subtotal, DELIVERY_MINOR, canonical_coupons
    violations = []
    if mode == 'vulnerable':
        values = [body.get('client_unit_price_minor', item['price_minor']),
                  body.get('client_addon_total_minor', addon_total), body.get('delivery_fee_minor', DELIVERY_MINOR)]
        if any(type(v) is not int or abs(v) > 1_000_000 for v in values):
            raise ValueError('Food prices must be bounded minor-unit integers')
        price, addons_price, delivery = values
        accepted_subtotal, accepted_coupons = (price + addons_price) * quantity, coupons
        if price != item['price_minor']: violations.append('Client-controlled menu price accepted')
        if addons_price != addon_total: violations.append('Client-controlled addon price accepted')
        if delivery != DELIVERY_MINOR: violations.append('Client-controlled delivery fee accepted')
        if coupons and not item['coupon_eligible']: violations.append('Excluded meal received coupon')
        if coupons and canonical_subtotal < 2000: violations.append('Food minimum-spend restriction bypassed')
        if len(coupons) > 1: violations.append('Food coupon replay accepted')
        if not 1 <= quantity <= 20: violations.append('Invalid food quantity accepted')
    accepted_total = accepted_subtotal
    for _ in accepted_coupons: accepted_total = accepted_total * 90 // 100
    accepted_total += delivery
    return {'lab_evidence': True, 'fixture': 'food-ordering-v1', 'quote_only': True,
            'order_created': False, 'payment_initiated': False, 'product_id': product, 'quantity': quantity,
            'accepted_total': accepted_total / 100, 'canonical_total': canonical_total / 100,
            'accepted_total_minor': accepted_total, 'canonical_total_minor': canonical_total,
            'accepted_coupons': accepted_coupons, 'violations': violations}
