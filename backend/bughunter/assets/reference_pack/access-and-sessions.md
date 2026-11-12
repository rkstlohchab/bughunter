# API access control and session comparison

Authored synthesis; references reviewed 2026-11-11.

## Method: two accounts, known ownership

Create one known object for account A and another for account B in the configured test environment. Keep separate cookie jars, headers, and evidence sessions. Establish successful access by each owner, then change only the object identifier while retaining the other account's credentials. Compare response contents and any durable state change with the access policy. Do not infer access from status code alone: identical status codes may hide filtered objects or generic errors.

Object authorization belongs at the operation that accesses the object. Unpredictable identifiers do not replace checks, and a user identifier comparison alone may miss indirect relationships. [OWASP API1:2023 BOLA](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/) describes the issue and required object-level checks.

## Method: session lifecycle

Capture session behavior before login, after login, after privilege changes, and after logout. Verify that old credentials lose the intended access; record cookie attributes and expiration separately from server invalidation. Never copy one account's credentials into the other's comparison session. Token rotation, server-side expiration, and transport protections solve different problems. See [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## Worked repository example: citation ownership

Test proposals query the knowledge library with their traffic record's `project_id`. Global reference notes and that project's notes may be returned; a different project's private note must not appear. The regression seeds identical searchable text in all three scopes, then checks source identities rather than relying on different wording. This tests retrieval isolation, not endpoint authorization on an external API.

## Evidence required

Store the account label, owned object, request, expected entitlement, matched response, and control result. Redact credentials from exported evidence. A shared cache, stale response, lost session cookie, or failed owner baseline invalidates the comparison and should produce an inconclusive result.
