// Runtime validator for game-runtime wire records. Loads the JSON Schemas
// in ../../contracts/game_runtime/ and applies them to incoming data,
// returning a list of human-readable error strings. Empty list == valid.
//
// Scope: supports the subset of JSON Schema Draft 2020-12 used by the
// game-runtime schemas — type (scalar or array), enum, required,
// properties, additionalProperties: false, pattern, minimum, maximum,
// items, nullable via type: ["string", "null"], and nested objects. No
// $ref, oneOf, allOf, const, format or dynamic refs: unneeded here.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SCHEMA_DIR = resolve(HERE, '../../contracts/game_runtime');

/** Kinds this validator understands. Maps snake_case wire name → schema filename. */
const KIND_FILES = {
  game_target: 'game_target.schema.json',
  game_session: 'game_session.schema.json',
  mapping_recipe: 'mapping_recipe.schema.json',
  native_operation: 'native_operation.schema.json',
  feature_run: 'feature_run.schema.json',
};

const SCHEMA_CACHE = Object.create(null);

function loadSchema(kind) {
  if (!Object.hasOwn(KIND_FILES, kind)) {
    throw new Error(`unknown kind '${kind}'; expected one of ${Object.keys(KIND_FILES).join(', ')}`);
  }
  if (!Object.hasOwn(SCHEMA_CACHE, kind)) {
    const path = resolve(SCHEMA_DIR, KIND_FILES[kind]);
    SCHEMA_CACHE[kind] = JSON.parse(readFileSync(path, 'utf8'));
  }
  return SCHEMA_CACHE[kind];
}

function typeOf(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  if (Number.isInteger(value)) return 'integer';
  const t = typeof value;
  if (t === 'number') return 'number';
  if (t === 'boolean') return 'boolean';
  if (t === 'string') return 'string';
  if (t === 'object') return 'object';
  return t;
}

function checkType(schemaType, value) {
  const actual = typeOf(value);
  if (Array.isArray(schemaType)) {
    for (const t of schemaType) {
      if (actual === t) return true;
      // integer satisfies number
      if (t === 'number' && actual === 'integer') return true;
    }
    return false;
  }
  if (schemaType === 'number' && actual === 'integer') return true;
  return actual === schemaType;
}

function validateAtPath(schema, data, path, errors) {
  if (schema === true) return;
  if (schema === false) {
    errors.push(`${path}: value forbidden by schema (schema=false)`);
    return;
  }

  // type
  if (schema.type !== undefined && !checkType(schema.type, data)) {
    const want = Array.isArray(schema.type) ? schema.type.join('|') : schema.type;
    errors.push(`${path}: expected type ${want}, got ${typeOf(data)}`);
    return;
  }

  // enum
  if (Array.isArray(schema.enum)) {
    const ok = schema.enum.some((candidate) => deepEqual(candidate, data));
    if (!ok) {
      errors.push(`${path}: value ${JSON.stringify(data)} is not one of ${JSON.stringify(schema.enum)}`);
    }
  }

  // pattern + minLength/maxLength (strings only)
  if (typeof data === 'string') {
    if (schema.pattern !== undefined) {
      try {
        const re = new RegExp(schema.pattern);
        if (!re.test(data)) {
          errors.push(`${path}: string ${JSON.stringify(data)} does not match pattern ${schema.pattern}`);
        }
      } catch (err) {
        errors.push(`${path}: invalid pattern in schema: ${err.message}`);
      }
    }
    if (schema.minLength !== undefined && data.length < schema.minLength) {
      errors.push(`${path}: string length ${data.length} is less than minLength ${schema.minLength}`);
    }
    if (schema.maxLength !== undefined && data.length > schema.maxLength) {
      errors.push(`${path}: string length ${data.length} is greater than maxLength ${schema.maxLength}`);
    }
  }

  // minimum / maximum (numbers only)
  if (typeof data === 'number') {
    if (schema.minimum !== undefined && data < schema.minimum) {
      errors.push(`${path}: value ${data} is less than minimum ${schema.minimum}`);
    }
    if (schema.exclusiveMinimum !== undefined && data <= schema.exclusiveMinimum) {
      errors.push(`${path}: value ${data} is not greater than exclusiveMinimum ${schema.exclusiveMinimum}`);
    }
    if (schema.maximum !== undefined && data > schema.maximum) {
      errors.push(`${path}: value ${data} is greater than maximum ${schema.maximum}`);
    }
    if (schema.exclusiveMaximum !== undefined && data >= schema.exclusiveMaximum) {
      errors.push(`${path}: value ${data} is not less than exclusiveMaximum ${schema.exclusiveMaximum}`);
    }
  }

  // objects
  if (typeOf(data) === 'object' && (schema.properties || schema.required || schema.additionalProperties === false)) {
    const props = schema.properties || {};
    if (Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (!Object.hasOwn(data, key)) {
          errors.push(`${path}.${key}: required property missing`);
        }
      }
    }
    if (schema.additionalProperties === false) {
      for (const key of Object.keys(data)) {
        if (!Object.hasOwn(props, key)) {
          errors.push(`${path}.${key}: unknown property is not allowed (additionalProperties: false)`);
        }
      }
    }
    for (const [key, subSchema] of Object.entries(props)) {
      if (Object.hasOwn(data, key)) {
        validateAtPath(subSchema, data[key], `${path}.${key}`, errors);
      }
    }
  }

  // arrays
  if (Array.isArray(data) && schema.items !== undefined) {
    for (let i = 0; i < data.length; i += 1) {
      validateAtPath(schema.items, data[i], `${path}[${i}]`, errors);
    }
  }
}

function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (typeof a === 'object' && typeof b === 'object') {
    const ak = Object.keys(a), bk = Object.keys(b);
    if (ak.length !== bk.length) return false;
    return ak.every((k) => deepEqual(a[k], b[k]));
  }
  return false;
}

/**
 * Validate `data` against the schema for `kind`. Returns an array of
 * error strings; empty array means the record is valid.
 *
 * @param {string} kind - One of: game_target, game_session, mapping_recipe, native_operation, feature_run
 * @param {unknown} data
 * @returns {string[]}
 */
export function validate(kind, data) {
  const schema = loadSchema(kind);
  const errors = [];
  validateAtPath(schema, data, '$', errors);
  return errors;
}

export const CONTRACT_VERSION = 1;
export const KINDS = Object.freeze(Object.keys(KIND_FILES));
