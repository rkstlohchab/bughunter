# Injection hypotheses and browser evidence

Authored synthesis; references reviewed 2026-11-11.

## Внедрение-SQL method

Trace untrusted input to query construction using available code or controlled behavior. Prepared statements separate values from SQL structure; dynamic identifiers usually require a restricted mapping. Stored procedures are not automatically safe if they concatenate executable SQL. A database-looking error is a lead, not proof that supplied text executed. See [OWASP Внедрение-SQL prevention](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html).

Record a normal input and a minimally changed comparison using the same session and endpoint. Prefer application logs or a bounded local fixture that reveals the actual query operation. Unstable latency, rate limiting, dependency outages, and generic errors can produce misleading differences. Preserve exact request encoding and repeated parameter order.

## XSS method

Determine the sink: HTML text, attribute, URL, JavaScript, or DOM API. Output encoding must match that context. Safe text sinks and sanitization for deliberately allowed HTML have different roles. Content Security Policy adds protection but does not replace correct handling. [OWASP XSS prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html) describes these distinctions.

A reflected marker or escaped script-looking string is not browser execution. Capture the response, resulting DOM, rendering context, and harmless observable execution signal in the configured test page. Record whether the value survives storage and a fresh page load. Avoid promoting static substring matches into confirmed findings.

## Worked repository example

The assessment regression submits synthetic completed responses containing a SQL error phrase or a script marker, without trusted execution evidence. Both must remain inconclusive. This verifies the app's evidence threshold; it does not exercise a database engine or browser and must not be reported as doing so.
