# GraphQL transport and effect classification

Authored synthesis and repository behavior; references reviewed 2026-11-11.

## Transport facts

GraphQL HTTP requests can carry a document in JSON POST bodies or URL query parameters. Operation selection may depend on `operationName`; variables and extensions add context. The HTTP specification requires GET requests that select mutations to be rejected. This requirement is a server obligation, not proof that an arbitrary server complies. [GraphQL over HTTP working draft](https://http-spec.graphql.org/draft/) is the transport reference; it does not define the semantics of a particular application's resolvers.

## Method: inspect the decoded envelope

Decode transport parameters before checking operation text. JSON Unicode escapes can hide a token from a raw substring check. Examine every batch member and retain duplicate parameter values when classifying; choosing the first value while the server chooses the last loses the intended correspondence. A persisted-query hash without a trusted document mapping does not establish the operation's effect.

## Repository decision

The policy classifier treats recognized mutation documents as W-меняющий. Other recognizable GraphQL requests, persisted operations, ambiguous encodings, and multipart bodies require individual review even when a broad path rule is marked read-only. The helper is conservative and is not a GraphQL grammar validator. A harmless query can therefore require review. No new parser dependency or endpoint discovery is involved.

## Worked example and regression

A path rule permits GET and POST for `/api/*`. A POST to `/api/products` carrying a JSON `query` whose mutation keyword includes a Unicode escape still waits for approval. Likewise, `/api/products?query=mutation...` cannot inherit the products rule. Tests include JSON batches, duplicate document keys, form encoding, persisted extensions, malformed JSON, and normal queries. The expected result is an approval-pending action; no request is sent by these classifier tests.
