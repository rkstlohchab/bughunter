# BugHunter — Staging-only price integrity verification

The tool can now **actively compare quotes from a deployed staging service**. It does **not** create carts, place orders, redeem real coupons, invoke payment systems, or send price modifications to production checkout APIs. Use it only for a deployment you administer with a non-purchasable test SKU.

## Contract required on your staging service

Implement exactly two endpoints on the same staging origin:

1. `GET /.well-known/bughunter-price-audit.json` returns JSON like:

```json
{
  "protocol": "bughunter-price-quote-v1",
  "environment": "staging",
  "quote_only": true,
  "orders_enabled": false,
  "payments_enabled": false,
  "challenge": "PASTE_RANDOM_CHALLENGE_CREATED_IN_BUGHUNTER_HERE",
  "quote_path": "/__bughunter__/quote"
}
```

2. `POST /__bughunter__/quote` accepts only the following structure:

```json
{
  "test_sku": "TEST-ITEM",
  "quantity": 1,
  "client_unit_price_minor": 1,
  "dry_run": true,
  "challenge": "SAME_CHALLENGE"
}
```

It must calculate the authoritative catalog quote **without storing anything**, and return a receipt shaped like:

```json
{
  "quote_only": true,
  "order_created": false,
  "payment_initiated": false,
  "total_minor": 10000
}
```

Configure `TEST-ITEM` as a synthetic, non-purchasable product with an expected unit price of 10000 minor currency units. These values can be changed in the project configuration. The endpoint must never invoke payment or order services, even if an attacker supplies another method, SKU, or price. Ensure your server enforces that rather than merely returning `order_created:false`.

## Configure the app

1. In **Projects / Targets**, edit your authorized staging project.
2. Open **Staging price integrity tests (no purchases)** and enable it. Enter the exact origin, synthetic SKU and expected price, and generate a random challenge. Deploy the manifest with the matching challenge.
3. Save. BugHunter adds two exact allowed, read-only test endpoint rules to the project. The project scope must include the staging origin.
4. In Settings, verify the Burp/policy gateway connection (unless using BugHunter's own local fixture).
5. In Chat, type: `Test price integrity on my staging site`. Review the requested permission and approve the fixed batch.
6. Watch **Tasks** and use Stop to prevent subsequent requests. Export the evidence for source-linked case outcomes.

The worker sends **at most four HTTP requests** (one manifest, three dry-run quotes) using the existing project policy gateway. It never follows redirects, guesses product IDs, replays authenticated checkout traffic, or executes model-created payloads. Network or contract failures abort rather than silently falling back to real endpoints.

## Verified behavior vs limitations

- A quote influenced by a supplied client price is flagged as **review needed**, not as a confirmed production vulnerability.
- If the API does not explicitly confirm `quote_only:true`, `order_created:false`, and `payment_initiated:false`, BugHunter stops. These JSON claims cannot independently prove the absence of server-side effects; deploy a genuinely isolated staging service.
- For a production website, the existing Burp metadata/Scanner review remains passive, and any active Burp audit is separately authorized.
- The local fixture can be exercised by `backend/tests/test_staging_price.py`; the test process contains both correct and deliberately insecure quote implementations.
- Automatic purchase/checkout exploitation is not provided.

## Mac setup

```bash
cd ~/Downloads/bughunter
bash setup-mac.sh --core-only
bash scripts/build_burp_bridge.sh
npm run desktop:build
```

Burp Professional's extension still needs to be added through Burp **Extensions → Installed**; its official Montoya API and native macOS app build must be tested on your Mac.
