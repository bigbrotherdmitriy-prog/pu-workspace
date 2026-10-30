import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {validate, validPath, apply} from './agent.mjs';
const proposal = files => ({base: 'a'.repeat(40), files});
test('reject injection paths, infrastructure, secrets and dependency manifests', () => {
  for (const p of ['../docs/a.md', 'docs/../../x.py', 'docs//a.md', '.github/workflows/a.yml',
    'infra/a.py', 'backend/requirements.txt', 'docs/.env', 'docs/token.md', 'docs/a.md\nX',
    'frontend/src/../../../a.ts', '/docs/a.md', 'docs/a\\b.md']) assert.equal(validPath(p), false, p);
});
test('strict schema, duplicate paths and resource limits fail closed', () => {
  const f = {path: 'docs/task.md', content: 'safe'};
  assert.throws(() => validate(proposal([])));
  assert.throws(() => validate(proposal([f, f])));
  assert.throws(() => validate(proposal([{...f, command: 'echo bad'}])));
  assert.throws(() => validate(proposal([{...f, content: 'x'.repeat(100001)}])));
  assert.throws(() => validate({...proposal([f]), base: 'main'}));
  assert.throws(() => validate({...proposal([f]), shell: 'bad'}));
});
test('model commands are stored as text, never executed', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-agent-'));
  try {
    const content = '$(touch injected)\n::error::untrusted';
    apply(proposal([{path: 'docs/task.md', content}]), root);
    assert.equal(fs.readFileSync(path.join(root, 'docs/task.md'), 'utf8'), content);
    assert.equal(fs.existsSync(path.join(root, 'injected')), false);
  } finally { fs.rmSync(root, {recursive: true}); }
});
test('source changes require regression tests', () => {
  const source = {path: 'backend/app/example.py', content: 'value = 1'};
  assert.throws(() => validate(proposal([source])));
  assert.doesNotThrow(() => validate(proposal([source, {path: 'backend/tests/test_example.py', content: 'assert True'}])));
  assert.equal(validPath('frontend/src/example.test.tsx'), true);
  assert.doesNotThrow(() => validate(proposal([{path: 'frontend/src/example.tsx', content: ''},
    {path: 'frontend/src/example.test.tsx', content: ''}])));
});
test('symlink escape is rejected on Linux runners', {skip: process.platform === 'win32'}, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-agent-'));
  try {
    fs.symlinkSync(os.tmpdir(), path.join(root, 'docs'));
    assert.throws(() => apply(proposal([{path: 'docs/task.md', content: 'bad'}]), root));
  } finally { fs.rmSync(root, {recursive: true}); }
});
