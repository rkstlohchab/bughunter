import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import vm from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

// Exercise the actual TypeScript classifier without running a desktop process.
const source = readFileSync(join(process.cwd(), 'src/taskIntent.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
});
const moduleContext = { exports: {} };
vm.runInNewContext(compiled.outputText, moduleContext);
const { classifyTask } = moduleContext.exports;

for (const [message, intent] of [
  ['Explain how a pointer works', 'chat'],
  ['Can you explain how a shopping cart works?', 'chat'],
  ['Find a pointer in my offline test process', 'memory'],
  ['Track the health value in my offline game', 'memory'],
  ['Create an ESP overlay for my offline game', 'memory'],
  ['Modify the money value in my test process', 'memory'],
  ['Audit my authorized website', 'website'],
  ['Do web penetration test on my site', 'website'],
  ['Run an AI prompt injection test', 'ai_test'],
  ['Evaluate my AI for prompt injection', 'ai_test'],
  ['Test my shopping cart', 'cart'],
  ['Deploy Claude Code and Codex to review files', 'agents'],
  ['Explain this uploaded PDF', 'chat'],
]) {
  test(`classifies ${JSON.stringify(message)} as ${intent}`, () => {
    assert.equal(classifyTask(message), intent);
  });
}

const { wantsMacControl } = moduleContext.exports;
for (const [message, expected] of [
  ['Open TextEdit and type a test message', true],
  ['Launch Notes and create a document', true],
  ['Control my Mac and click the selected app button', true],
  ['Scroll down in my desktop app', true],
  ['Click the Save button in TextEdit', true],
  ['How does macOS accessibility work?', false],
  ['Can ChatGPT control my Mac?', false],
  ['Explain how ESP works', false],
  ['Show my Burp history', false],
  ['Run Burp scan #42', false],
  ['Find a memory pointer in my offline test process', false],
]) {
  test(`Mac control classifier: ${JSON.stringify(message)} => ${expected}`, () => {
    assert.equal(wantsMacControl(message), expected);
  });
}

const { wantsMappingResearch } = moduleContext.exports;
for (const [message, expected] of [
  ['Research memory mappings for the score in my program', true],
  ['Find candidate mappings using my references', true],
  ['Find mappings for the score in my program', true],
  ['Research mappings for my program', true],
  ['Research references and propose mappings', true],
  ['Explain how memory mappings work', false],
  ['Research my documents', false],
  ['Check my website', false],
]) {
  test(`cited mapping workflow: ${message}`, () => assert.equal(wantsMappingResearch(message), expected));
}
