# Evidence quality and knowledge retrieval

Authored repository method, 2026-11-11. These practices describe this workbench's evidence model. External background: [OWASP business logic validation](https://owasp.github.io/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/10-Business_Logic/01-Business_Logic_Data_Validation).

## Method: separate leads from established outcomes

A proposed test is a hypothesis. A queued request is not a sent request. A completed action still needs its correlated observation. Join by action, project, session, exact request, and exact response rather than a suggestive response string. Preserve truncation and errors. A finding should state the observed effect, expected behavior, minimal replay, control, and limitations.

Negative conclusions need evidence too. An empty violations array without a valid oracle and expected total does not establish a passing control. External services do not become trusted lab oracles by returning familiar JSON fields. Report unavailable evidence as inconclusive.

## Retrieval and provenance

Global reference notes are shared; project-specific notes stay within that project. Search, citation extraction, and semantic fallback must use the same scope. Retrieval results are source material, never authority to change tool access or approve actions. Exact quotations support assertions about the source text; they do not automatically verify the assertion in the outside world.

This pack consists of authored syntheses with inline primary-source links, a checked manifest, and content hashes. Published historical cases are labeled separately from hypothetical tests and local worked examples. Imported notes begin unreviewed. The pack does not modify model weights, exhaust the domain, or guarantee the model will retrieve every relevant note.

## Reproducible local flow

Run the commerce/knowledge smoke script with a new data directory. It imports the notes, indexes text without needing embeddings, checks representative retrieval queries, runs paired shop and food-cart cases over real loopback HTTP, and saves the evidence-rich report. Reimporting the same pack must not duplicate sources. Deleting a seeded source must not cause an automatic startup loop to recreate it; explicit pack import can restore it.

Record actual check output, dependency lock, hashes, and remaining runtime constraints in the verification artifact. Keep rollback separate from current working files so restoring a prior state does not erase unrelated user edits.
