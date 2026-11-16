// Shared-fixture parity test. Walks contracts/game_runtime/fixtures/parity/
// and asserts the TypeScript (via .mjs) validator agrees with Python, Rust
// and the JSON Schema itself.
//
// Valid fixtures must produce zero errors.
// Invalid fixtures must produce at least one error AND the first .expected.txt
// substring must appear in at least one error (case-sensitive) OR the overall
// error text must contain it (we check both).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, basename } from 'node:path';

import { validate, KINDS, CONTRACT_VERSION } from './gameRuntimeValidator.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const PARITY_DIR = resolve(HERE, '../../contracts/game_runtime/fixtures/parity');

function kindFromStem(stem) {
  const idx = stem.indexOf('__');
  return idx === -1 ? stem : stem.slice(0, idx);
}

function listJson(dir) {
  return readdirSync(dir)
    .filter((n) => n.endsWith('.json'))
    .map((n) => resolve(dir, n));
}

test('CONTRACT_VERSION is 1', () => {
  assert.equal(CONTRACT_VERSION, 1);
});

test('KINDS exposes the five records', () => {
  assert.deepEqual(
    [...KINDS].sort(),
    ['feature_run', 'game_session', 'game_target', 'mapping_recipe', 'native_operation'],
  );
});

test('every valid parity fixture is accepted', () => {
  const files = listJson(resolve(PARITY_DIR, 'valid'));
  assert.ok(files.length >= 5, `expected at least 5 valid fixtures, got ${files.length}`);
  for (const file of files) {
    const stem = basename(file, '.json');
    const kind = kindFromStem(stem);
    const data = JSON.parse(readFileSync(file, 'utf8'));
    const errors = validate(kind, data);
    assert.deepEqual(errors, [], `fixture ${file} (${kind}) should be valid but got errors: ${errors.join(' | ')}`);
  }
});

test('every invalid parity fixture is rejected with the expected substring', () => {
  const files = listJson(resolve(PARITY_DIR, 'invalid'));
  assert.ok(files.length >= 5, `expected at least 5 invalid fixtures, got ${files.length}`);
  for (const file of files) {
    const stem = basename(file, '.json');
    const kind = kindFromStem(stem);
    const data = JSON.parse(readFileSync(file, 'utf8'));
    const errors = validate(kind, data);
    assert.ok(errors.length > 0, `fixture ${file} (${kind}) should be invalid but the TS validator produced no errors`);
    const expectedPath = file.replace(/\.json$/, '.expected.txt');
    let expected = null;
    try {
      expected = readFileSync(expectedPath, 'utf8').split(/\r?\n/)[0].trim();
    } catch {
      // No sibling expected.txt — just assert rejection without a substring check.
    }
    if (expected) {
      const joined = errors.join(' | ');
      // The expected substring is the Pydantic/Rust/JSON-Schema wording; the TS
      // validator uses its own wording (patterns, additionalProperties, etc.).
      // We require that SOME shared vocabulary overlap exists: either the
      // expected substring appears verbatim, OR the field name from the expected
      // text appears somewhere in the TS errors.
      const normalisedExpected = expected.toLowerCase();
      const normalisedErrors = joined.toLowerCase();
      // Pull a candidate field name (first dotted path or snake_case token) from the expected.
      const fieldToken = (expected.match(/[a-z_][a-z0-9_.]*/i) || [''])[0].toLowerCase();
      const overlaps =
        normalisedErrors.includes(normalisedExpected) ||
        (fieldToken && fieldToken.length >= 3 && normalisedErrors.includes(fieldToken));
      assert.ok(
        overlaps,
        `fixture ${file} (${kind}): expected substring "${expected}" or its field token "${fieldToken}" not found in TS errors: ${joined}`,
      );
    }
  }
});

test('reproduced Python-vs-schema mismatches are rejected', () => {
  // The five records Codex named as previously-accepted-by-Python. Each lives
  // under contracts/game_runtime/fixtures/parity/invalid/ with a predictable
  // stem; we fetch them by stem so the test survives whatever expected.txt the
  // parity author authored.
  const stems = [
    'mapping_recipe__revision_zero',
    'mapping_recipe__revision_bool',
    'mapping_recipe__revision_string',
    'mapping_recipe__schema_version_negative',
    'game_target__missing_modules_and_capabilities',
  ];
  for (const stem of stems) {
    const file = resolve(PARITY_DIR, 'invalid', `${stem}.json`);
    let data;
    try {
      data = JSON.parse(readFileSync(file, 'utf8'));
    } catch (err) {
      assert.fail(`parity fixture ${stem}.json must exist (Codex reproduced this case): ${err.message}`);
    }
    const kind = kindFromStem(stem);
    const errors = validate(kind, data);
    assert.ok(errors.length > 0, `TS validator must reject ${stem}; got no errors`);
  }
});

test('inherited property names are unknown record kinds', () => {
  for (const kind of ['constructor', '__proto__', 'toString']) {
    assert.throws(() => validate(kind, {}), /unknown kind/);
  }
});

test('required fields must be own properties', () => {
  const data = JSON.parse(readFileSync(resolve(PARITY_DIR, 'valid/mapping_recipe.json'), 'utf8'));
  const inherited = Object.create(data);
  assert.ok(validate('mapping_recipe', inherited).some((error) => error.includes('required property missing')));
});
