# Food-ordering methods and reproducible fixture

Authored design and worked local example; 2026-11-11. Prices are synthetic minor units. No order, payment, or delivery is created.

## Method: separate menu, addons, delivery, and promotions

Food quotes have independently priced components. Derive base prices and addon prices from the menu on the server; calculate delivery independently of client fields. Define whether a coupon includes delivery, which bundles are excluded, and whether minimum spend is measured before or after discounts. Freeze these rules before evaluating a result. Apply a consistent rounding rule in minor units.

Business-rule validation is application-specific; valid syntax alone cannot establish that a value is allowed in the workflow. [OWASP business logic data validation](https://owasp.github.io/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/10-Business_Logic/01-Business_Logic_Data_Validation) provides the general distinction. The numbers and implementation below are this repository's design.

## Worked example: bowl and drink

`bowl` costs 1800, `drink` adds 300, and delivery costs 250 minor units. A normal bowl-plus-drink quote is 2350. `FOOD10` discounts eligible menu subtotals of at least 2000 once, excluding delivery: 2100 * 90 / 100 + 250 = 2140. A bowl alone remains 2050 even with the coupon. The 4200-unit family bundle is excluded, so its delivered total remains 4450.

## Automated local cases

The `/vulnerable/food-cart` and `/fixed/food-cart` pair covers menu-price substitution, addon-price substitution, delivery-fee removal, minimum-spend обход, excluded bundles, repeated coupon entries, and negative quantities. Fixed calculations ignore client price fields, apply at most one eligible coupon, and reject invalid quantities. Every successful result declares the fixture identity, quote-only state, and absence of orders/payments. Results retain both major-unit totals and integer minor-unit totals.

## Boundaries of this example

Repeated coupon entries model a single-request replay/stacking defect. They do not demonstrate concurrent redemption. Taxes, tips, kitchen dispatch, stock, refunds, payment callbacks, per-account redemption history, and distributed transactions are not implemented by this fixture. Extend the fixture and its expected outcomes before claiming coverage for those features.
