"""Cited, review-first web testing checklist based on public TBHM topics.

These are planning references, not runnable exploit payloads.
"""
from __future__ import annotations

SOURCE = "https://github.com/jhaddix/tbhm"

STAGES = (
    {
        "id": "discovery", "title": "Discovery & reconnaissance",
        "reference": SOURCE + "/blob/master/02_Discovery.md",
        "checks": (
            "Inventory explicitly in-scope hosts, HTTP services and technologies.",
            "Collect historical URLs, JavaScript paths and API schemas as leads.",
            "Record source, timestamp and scope decision for each asset.",
        ),
    },
    {
        "id": "mapping", "title": "Application mapping",
        "reference": SOURCE + "/blob/master/03_Mapping.md",
        "checks": (
            "Map authenticated and anonymous flows, methods and parameters.",
            "Record state transitions and data flows between roles and services.",
            "Identify object identifiers, file-handling paths and trust boundaries.",
        ),
    },
    {
        "id": "identity", "title": "Authentication, sessions & authorization",
        "reference": SOURCE + "/blob/master/04_Authorization_and_Session.md",
        "checks": (
            "Compare responses for two controlled user identities and roles.",
            "Check session invalidation, recovery and sensitive object access.",
            "Record expected ownership and actual server-side access decisions.",
        ),
    },
    {
        "id": "injection", "title": "Input handling",
        "reference": SOURCE + "/blob/master/05_XSS.md",
        "checks": (
            "Review reflected, stored and DOM input contexts for XSS.",
            "Check injection hypotheses in captured HTTP inputs.",
            "Review upload validation, storage and unsafe processing paths.",
        ),
    },
    {
        "id": "api", "title": "API surface",
        "reference": "https://owasp.org/API-Security/",
        "checks": (
            "Record object-level and property-level access restrictions.",
            "Inspect sensitive API functions and workflow requirements.",
            "Assess URL-fetching features, resource usage and error disclosures.",
        ),
    },
    {
        "id": "commerce", "title": "Commerce & business logic",
        "reference": SOURCE + "/blob/master/09_Privledge_Logic_Transport.md",
        "checks": (
            "Confirm server-derived item prices and integer quantity bounds.",
            "Check coupons for eligibility, stacking, minimum-spend and replay.",
            "Reprice shipping, taxes and currency after cart changes.",
            "Model allowed checkout states and verify payment-provider outcomes.",
            "Compare authoritative state with client-visible success messages.",
        ),
    },
    {
        "id": "reporting", "title": "Evidence & reporting",
        "reference": SOURCE,
        "checks": (
            "Preserve sanitized baseline and changed requests with correlation IDs.",
            "Reproduce the invariant violation using controlled test accounts.",
            "Keep hypothetical leads separate from confirmed findings.",
        ),
    },
)


def checklist() -> list[dict]:
    """Return fresh JSON-compatible data for RPC clients."""
    return [{**stage, "checks": list(stage["checks"])} for stage in STAGES]
