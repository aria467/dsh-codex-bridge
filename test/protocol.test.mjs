import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_STATES,
  handoffSignal,
  isAwaitingDecision,
  isTerminal,
  stateFromTurnEnd,
  validateEnvelope,
  composePrompt,
  buildManifest,
  renderManifestMarkdown,
} from '../lib/protocol.js';

const GOOD = {
  task_id: 't-20260101-7f3a',
  workspace: '/example/projects/foo',
  prompt: 'refactor the parser tokenizer',
  permission_preset: 'workspace-write',
};

/** An environment where everything asked about exists and nothing is taken. */
function env(overrides = {}) {
  return {
    workspaceExists: () => true,
    hasPermissionPreset: () => true,
    hasAgentPreset: () => true,
    hasSkill: () => true,
    hasCommand: () => true,
    sessionExists: () => true,
    taskIdTaken: () => false,
    ...overrides,
  };
}

test('a complete envelope is accepted and normalized', () => {
  const result = validateEnvelope(GOOD, env());
  assert.equal(result.ok, true);
  assert.deepEqual(result.envelope, {
    task_id: GOOD.task_id,
    workspace: GOOD.workspace,
    prompt: GOOD.prompt,
    permission_preset: 'workspace-write',
    agent_preset: null,
    session_id: null,
    skills: [],
    commands: [],
  });
});

test('missing permission_preset is rejected and points at the manifest', () => {
  const { permission_preset, ...withoutPreset } = GOOD;
  const result = validateEnvelope(withoutPreset, env());
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'missing-required-field');
  assert.equal(result.field, 'permission_preset');
  assert.match(result.message, /capabilities\.json/);
});

test('every required field is enforced', () => {
  for (const field of ['task_id', 'workspace', 'prompt', 'permission_preset']) {
    const { [field]: _dropped, ...rest } = GOOD;
    const result = validateEnvelope(rest, env());
    assert.equal(result.ok, false, `${field} should be required`);
    assert.equal(result.reason, 'missing-required-field');
    assert.equal(result.field, field);
  }
});

test('unparseable JSON is reported as bad-json', () => {
  const result = validateEnvelope(undefined, env({ parseFailed: true }));
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'bad-json');
});

test('non-object payloads are rejected', () => {
  for (const payload of [null, 42, 'text', []]) {
    const result = validateEnvelope(payload, env());
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'not-an-object');
  }
});

test('unknown fields are named in the message', () => {
  const result = validateEnvelope({ ...GOOD, wrokspace: '/x' }, env());
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'wrong-field-type');
  assert.equal(result.field, 'wrokspace');
});

test('task_id must be filename-safe', () => {
  // An empty or whitespace-only id is caught by the generic string check first;
  // both rejections are correct, so assert the actual reason rather than assuming.
  for (const bad of ['', '   ']) {
    const result = validateEnvelope({ ...GOOD, task_id: bad }, env());
    assert.equal(result.ok, false, `task_id ${JSON.stringify(bad)} should be rejected`);
    assert.equal(result.reason, 'wrong-field-type');
    assert.equal(result.field, 'task_id');
  }
  for (const bad of ['a b', '../escape', 'has/slash', 'x'.repeat(129), '-leading-dash', 'x!y', 'caf\u00e9']) {
    const result = validateEnvelope({ ...GOOD, task_id: bad }, env());
    assert.equal(result.ok, false, `task_id ${JSON.stringify(bad)} should be rejected`);
    assert.equal(result.reason, 'invalid-task-id', `reason for ${JSON.stringify(bad)}`);
  }
  // Dots, hyphens and underscores are allowed after the first character.
  for (const good of ['a', 'dot.not.first', 't-20260101-7f3a', 'a_b-1.2', 'x'.repeat(128)]) {
    assert.equal(validateEnvelope({ ...GOOD, task_id: good }, env()).ok, true, `task_id ${JSON.stringify(good)}`);
  }
});

test('a reused task_id is rejected, protecting historical results', () => {
  const result = validateEnvelope(GOOD, env({ taskIdTaken: () => true }));
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'task-id-already-used');
  assert.match(result.message, /never reused/);
});

test('a freshly claimed task reports its own problem, not task-id reuse', () => {
  // Regression guard. The bridge renames the inbox file to `.claimed` and only then
  // validates, so "already used" must never be inferred from the claim itself.
  // With a clean environment every distinct defect must surface as itself — if
  // reuse detection ever starts counting the claim, these all collapse into
  // `task-id-already-used` and the real defect becomes invisible to the caller.
  const clean = env({ taskIdTaken: () => false });

  const badPreset = validateEnvelope({ ...GOOD, permission_preset: 'nope' }, { ...clean, hasPermissionPreset: () => false });
  assert.equal(badPreset.reason, 'unknown-permission-preset');

  const noWorkspace = validateEnvelope({ ...GOOD, workspace: '/nope' }, { ...clean, workspaceExists: () => false });
  assert.equal(noWorkspace.reason, 'workspace-not-found');

  const relativeWorkspace = validateEnvelope({ ...GOOD, workspace: 'rel/path' }, clean);
  assert.equal(relativeWorkspace.reason, 'workspace-not-absolute');

  const missingPrompt = validateEnvelope({ task_id: GOOD.task_id, workspace: GOOD.workspace, permission_preset: 'workspace-write' }, clean);
  assert.equal(missingPrompt.reason, 'missing-required-field');
  assert.equal(missingPrompt.field, 'prompt');
});

test('workspace must be an existing absolute path', () => {
  const relative = validateEnvelope({ ...GOOD, workspace: 'projects/foo' }, env());
  assert.equal(relative.reason, 'workspace-not-absolute');

  const missing = validateEnvelope({ ...GOOD, workspace: '/nowhere' }, env({ workspaceExists: () => false }));
  assert.equal(missing.reason, 'workspace-not-found');
});

test('each unknown-* rejection names the offending value', () => {
  const preset = validateEnvelope(GOOD, env({ hasPermissionPreset: () => false }));
  assert.equal(preset.reason, 'unknown-permission-preset');

  const agentPreset = validateEnvelope({ ...GOOD, agent_preset: 'nope' }, env({ hasAgentPreset: () => false }));
  assert.equal(agentPreset.reason, 'unknown-agent-preset');

  const skill = validateEnvelope({ ...GOOD, skills: ['nope'] }, env({ hasSkill: () => false }));
  assert.equal(skill.reason, 'unknown-skill');

  const command = validateEnvelope({ ...GOOD, commands: ['/nope arg'] }, env({ hasCommand: () => false }));
  assert.equal(command.reason, 'unknown-command');

  const session = validateEnvelope({ ...GOOD, session_id: 'sess_x' }, env({ sessionExists: () => false }));
  assert.equal(session.reason, 'session-not-found');
});

test('command names are compared without the leading slash or arguments', () => {
  const seen = [];
  const result = validateEnvelope(
    { ...GOOD, commands: ['/permission read-only'] },
    env({ hasCommand: (name) => (seen.push(name), true) }),
  );
  assert.equal(result.ok, true);
  assert.deepEqual(seen, ['permission']);
});

test('string arrays reject non-string members', () => {
  const result = validateEnvelope({ ...GOOD, skills: [7] }, env());
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'wrong-field-type');
  assert.equal(result.field, 'skills');
});

test('turn/end maps onto task states exactly as documented', () => {
  assert.deepEqual(stateFromTurnEnd({ kind: 'completed' }), { state: TASK_STATES.COMPLETED, detail: null });
  assert.equal(stateFromTurnEnd({ kind: 'max-tokens' }).state, TASK_STATES.FAILED);
  assert.equal(stateFromTurnEnd({ kind: 'blocked' }).state, TASK_STATES.FAILED);
  assert.equal(stateFromTurnEnd({ kind: 'interrupted' }).state, TASK_STATES.FAILED);
  assert.equal(stateFromTurnEnd({ kind: 'forked' }).state, TASK_STATES.FAILED);
  assert.equal(
    stateFromTurnEnd({ kind: 'error', error: { code: 'E_LLM', message: 'boom' } }).detail,
    'model error E_LLM: boom',
  );
});

test('a user abort is distinguished from every other abort', () => {
  assert.equal(stateFromTurnEnd({ kind: 'aborted', reason: { kind: 'user' } }).state, TASK_STATES.INTERRUPTED_BY_USER);
  for (const cause of [{ kind: 'parent' }, { kind: 'disposed' }, { kind: 'legacy' }, { kind: 'hook', reason: 'r' }]) {
    assert.equal(stateFromTurnEnd({ kind: 'aborted', reason: cause }).state, TASK_STATES.CANCELLED);
  }
});

test('a missing or unrecognized reason fails closed', () => {
  assert.equal(stateFromTurnEnd(undefined).state, TASK_STATES.FAILED);
  assert.equal(stateFromTurnEnd({ kind: 'brand-new-kind' }).state, TASK_STATES.FAILED);
});

test('handoff signals collapse the vocabulary into two values', () => {
  assert.equal(handoffSignal(TASK_STATES.COMPLETED), 'done');
  assert.equal(handoffSignal(TASK_STATES.FAILED), 'done');
  assert.equal(handoffSignal(TASK_STATES.CANCELLED), 'done');
  assert.equal(handoffSignal(TASK_STATES.INTERRUPTED_BY_USER), 'not_done');
  assert.equal(handoffSignal(TASK_STATES.WAITING_FOR_APPROVAL), 'not_done');
  assert.equal(handoffSignal(TASK_STATES.ORPHANED), 'not_done');
  assert.equal(handoffSignal(TASK_STATES.RUNNING), null);
  assert.equal(handoffSignal(TASK_STATES.SUBMITTED), null);
});

test('terminal, revivable and unknown states are told apart', () => {
  const terminal = ['completed', 'failed', 'cancelled', 'interrupted_by_user', 'orphaned'];
  for (const state of terminal) {
    assert.equal(isTerminal(state), true, `${state} should be terminal`);
    assert.equal(isAwaitingDecision(state), false);
    assert.notEqual(handoffSignal(state), null, `${state} should emit a handoff`);
  }
  // Parking on an approval is a handoff trigger but NOT an end state: answering it
  // returns the task to `running`, which is why it must stay non-terminal.
  assert.equal(isTerminal(TASK_STATES.WAITING_FOR_APPROVAL), false);
  assert.equal(isAwaitingDecision(TASK_STATES.WAITING_FOR_APPROVAL), true);
  assert.equal(handoffSignal(TASK_STATES.WAITING_FOR_APPROVAL), 'not_done');

  // Nothing has happened yet, so no handoff and no terminality.
  for (const state of ['submitted', 'running']) {
    assert.equal(isTerminal(state), false);
    assert.equal(isAwaitingDecision(state), false);
    assert.equal(handoffSignal(state), null);
  }
  // Unknown input is never terminal, so a typo cannot silently emit a handoff.
  assert.equal(isTerminal('not-a-state'), false);
  assert.equal(isTerminal(undefined), false);
  assert.equal(handoffSignal('not-a-state'), null);
});

test('composePrompt prefixes only what was actually asked for', () => {
  const bare = { prompt: 'do the thing', skills: [], commands: [] };
  assert.equal(composePrompt(bare), 'do the thing');

  const withSkills = composePrompt({ prompt: 'do it', skills: ['pdf'], commands: [] });
  assert.equal(withSkills, 'Use these skills: pdf.\n\ndo it');

  const withBoth = composePrompt({ prompt: 'do it', skills: ['pdf'], commands: ['/permission read-only'] });
  assert.equal(withBoth, 'Use these skills: pdf.\nRun these commands first: /permission read-only.\n\ndo it');
});

test('the manifest carries all four capability sections and a revision', () => {
  const manifest = buildManifest({
    revision: 'rev-1',
    version: '0.1.0',
    generatedAt: '2026-01-01T00:00:00.000Z',
    manualPath: '/home/u/.dsh-bridge/USING-DSH.md',
    skills: [{ name: 'pdf', description: 'PDF work' }],
    agentPresets: [{ id: 'standard', name: 'Standard', description: 'd', isDefault: true }],
    commands: [{ name: 'permission', description: 'switch preset', inputHint: '<preset>' }],
    permissionPresets: [{ value: 'workspace-write', name: 'workspace-write', description: 'd' }],
  });
  assert.equal(manifest.revision, 'rev-1');
  assert.equal(manifest.docs.manual, '/home/u/.dsh-bridge/USING-DSH.md');
  for (const key of ['skills', 'agentPresets', 'commands', 'permissionPresets']) {
    assert.equal(manifest[key].length, 1, `${key} should be carried through`);
  }
});

test('the markdown manifest survives empty sections and pipes', () => {
  const empty = renderManifestMarkdown(
    buildManifest({
      revision: 'r',
      version: '0',
      generatedAt: 'now',
      manualPath: '/m',
      skills: [],
      agentPresets: [],
      commands: [],
      permissionPresets: [],
    }),
  );
  assert.match(empty, /## Skills/);
  assert.match(empty, /_\(none available\)_/);

  const piped = renderManifestMarkdown(
    buildManifest({
      revision: 'r',
      version: '0',
      generatedAt: 'now',
      manualPath: '/m',
      skills: [{ name: 'a|b', description: 'c|d' }],
      agentPresets: [],
      commands: [],
      permissionPresets: [],
    }),
  );
  assert.match(piped, /a\\\|b/);
});
