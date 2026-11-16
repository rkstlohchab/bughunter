# Game runtime wire contracts

Authoritative JSON Schemas and shared fixtures for the records described in
`docs/game-lab/agent-implementation-plan.md` section 3. The Python
implementation lives in `backend/bughunter/game_runtime/contracts.py`; the
TypeScript mirror is `src/types/gameRuntime.ts`; the Rust mirror is
`src-tauri/src/game_runtime_types.rs`. All four must agree on field names,
types and shapes.

## Rules

- `CONTRACT_VERSION = 1`. Bump the integer for any breaking change and pin
  every runtime evidence report to the version it was produced against.
- Wire field names are `snake_case` everywhere (Python, JSON, TypeScript,
  Rust). Rust uses `#[serde(rename_all = "snake_case")]`.
- 64-bit numeric values (addresses, module RVAs, memory sizes, pointer
  offsets) are serialised as hex strings. JavaScript `Number` cannot safely
  hold them.
- Addresses match the pattern
  `^(mod:[A-Za-z0-9._-]+\+0x[0-9A-Fa-f]+|abs:0x[0-9A-Fa-f]+|null|placeholder|\+0x0)$`.
  `null`, `placeholder` and `+0x0` are distinct: a missing value is `null`,
  a deliberate un-filled slot is `placeholder`, and a legitimate zero offset
  is `+0x0`. Numeric JSON (e.g. `0`) is rejected by the validator.
- Module-relative roots (`mod:Name+0xRVA`) survive restarts; absolute roots
  (`abs:0x...`) are process-bound and should be revalidated.
- Health, team and visibility fields are nullable. A renderer must not fill
  unknown data with `100`, `0` or a guessed team.

## Files

- `game_target.schema.json`, `game_session.schema.json`,
  `mapping_recipe.schema.json`, `native_operation.schema.json`,
  `feature_run.schema.json` - JSON Schema Draft 2020-12.
- `fixtures/valid/<record>.json` - one valid example per record.
- `fixtures/invalid/<record>__<reason>.json` - rejected examples. Each has
  a sibling `<record>__<reason>.expected.txt` whose first line is a
  substring the Python validator must emit.

## Schema evolution

1. Edit `contracts.py`, the `*.schema.json` files and the Rust/TypeScript
   mirrors in the same change.
2. Bump `CONTRACT_VERSION` (and, when a record layout changes,
   `MappingRecipe.schema_version`).
3. Add or update fixtures under `fixtures/valid` and `fixtures/invalid`.
4. Run `backend/tests/test_game_runtime_contracts.py`, `npx tsc -p
   tsconfig.json --noEmit` and `cargo check --locked --manifest-path
   src-tauri/Cargo.toml` and attach the results to the change.
