# Task orchestrator: trust and execution boundaries

- **Operator starts a plan**, not an imported ZIP or model-generated text.
- A deterministic mode allowlist generates a bounded sequence of steps. There are no shell expressions in plan documents.
- **Safe preflight:** local passive dependency inventory, project-scoped knowledge references, and human-verified method matching. Unreviewed techniques and raw противостоящий prompts are not automatically executable instructions.
- **Explicit operator approval:** the existing coding-agent runner can be invoked in `read-only` mode. The web runner requires a valid authorization scope and retains per-request controls. External runner output is linked to the same durable task ledger.
- **Evidence:** a plan step only becomes completed after its own read-only action finishes or its linked task reports terminal completion. Reports link to evidence and do not infer proof of срывопригодности.
- **Recovery:** incomplete external work is marked interrupted on restart. It is never replayed without the operator reestablishing authorization. Existing backups include all new records because these reside in the same `entities` table.
- **Project isolation:** knowledge/method retrieval uses the selected project plus explicitly shared material. New reviewed methods are scoped to their evidence and project, and deleted with that project.
- **Further production work:** per-agent OS process sandboxing, robust auth/connection health checks, model routing, explicit operator resume plans, complete GUI E2E, signing/notarization, and Windows validation.
