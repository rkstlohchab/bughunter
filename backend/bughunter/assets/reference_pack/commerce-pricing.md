# Commerce pricing and discount validation

Authored synthesis; reference review date: 2026-11-11. Examples in this note describe the bundled fixture unless explicitly attributed to a publication.

## Method: establish the authoritative calculation

Record the catalog version, product identifier, quantity, account, currency, and discount policy before changing one input. Compare the server's stored cart with the returned quote and UI display. A reflected client price or changed DOM text does not establish an accepted transaction. Recalculate the expected value independently. Test price and quantity separately; a rejected malformed request does not prove the price control worked.

The OWASP payment guide covers price tampering, fractional/negative quantities, duplicated parameters, and workflow transitions. Its central testing implication is that validation must survive every stage carrying price or quantity. See [OWASP WSTG payment functionality](https://wstg.owasp.org/latest/4-Web_Application_Security_Testing/10-Business_Logic/10-Payment_Functionality/).

## Worked local example: excluded item

The bundled cart catalog prices `excluded` at 200 test units. The client proposes `unit_price=1`, quantity 1. The intentionally дефектный variant returns an accepted total of 1 and canonical total of 200. The fixed variant returns both totals as 200. For the `ELIGIBLE10` exclusion case, the fixed accepted coupon list must also be empty. These numbers belong to this fixture, not any restaurant.

## Pass, fail, and inconclusive

A passing control needs a completed action, matching trusted receipt, successful expected status, finite numeric totals, the expected account/product/quantity, and the exact expected total and coupon list. Quantity rejection needs the documented `invalid_quantity` error code. An HTTP 500, empty object, missing violations field, truncated response, or unrelated HTTP 400 is inconclusive or a failed regression, never a passing control. Preserve the tested request, response, receipt identifier, and paired fixed result.
