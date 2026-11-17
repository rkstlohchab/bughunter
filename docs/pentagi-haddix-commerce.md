# PentAGI + Haddix Methodology + Commerce Quote Coverage

This integration preserves BugHunterClaudex's two distinct execution boundaries:

- The BugHunter agent proposes HTTP checks through the existing approval/policy gateway.
- PentAGI runs as a separate, independently managed deployment. The BugHunter connector reads its flow summaries, but does **not** create, run, pause or modify PentAGI flows.

The handoff generator uses [Jason Haddix's Bug Hunter's Methodology](https://github.com/jhaddix/tbhm) topic structure to create a scoped investigation checklist. This is an authored adaptation, **not** a verbatim reproduction of Haddix's material.

## Configure the PentAGI connection

Deploy PentAGI separately using its [upstream documentation](https://github.com/vxcontrol/pentagi). Create a Bearer token under PentAGI Settings > API Tokens.

Start the BugHunter backend with the following environment variables:

```sh
export PENTAGI_API_URL="https://127.0.0.1:8443"
export PENTAGI_API_TOKEN="your-private-api-token"
# Optional: provide your PentAGI server's trusted CA certificate.
export PENTAGI_CA_BUNDLE="/path/to/ca.pem"
```

The bridge uses the documented `GET /api/v1/flows` endpoint. TLS verification stays enabled. An HTTP origin is accepted only for loopback development. The token is not written to BugHunter's SQLite database or included in RPC responses.

In BugHunter's **Automated cart security lab** page, use the PentAGI panel to inspect recent flows and generate a project-scoped handoff. Export/copy the plan, review it and initiate separate PentAGI activity from the PentAGI UI itself. PentAGI's external agent traffic does not pass through BugHunter's gateway, so enforce its network scope independently.

## Commerce lab coverage

The disposable 127.0.0.1 cart lab now tests:

- Client-controlled item pricing and invalid quantities
- Coupon eligibility, stacking, minimum spend, replay
- Food delivery fee and menu/add-on price integrity
- Synthetic checkout quote: shipping amount, currency, client total and payment-state claims

Checkout tests remain **quote-only**. Neither variant can create an order, process a payment, or fulfill a purchase. Each test is executed against a deliberately vulnerable variant and a fixed control, then correlated with actual action/traffic receipts. Findings remain lab-marked drafts.

## Verification

```sh
cd backend
uv run pytest -q tests/test_pentagi_bridge.py tests/test_checkout_lab.py tests/test_commerce_audit.py
cd ..
npx tsc --noEmit
```

The PentAGI API tests are mocked and never require a live instance. A real PentAGI connection check requires a separately running server and configured credentials.
