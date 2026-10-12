/**
 * Pure logic for the delegation bridge: envelope validation, task-state mapping,
 * and capability-manifest shaping. Nothing here touches Cordis, the filesystem,
 * or the harness — every function takes its inputs explicitly so this module can
 * be unit-tested outside a running DSH host (`node --test`).
 *
 * Vocabulary follows the field names external agents write, verbatim.
 */

/** Terminal states a task can settle into, plus the two non-terminal ones. */
export const TASK_STATES = Object.freeze({
  SUBMITTED: 'submitted',
  RUNNING: 'running',
  WAITING_FOR_APPROVAL: 'waiting_for_approval',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
  INTERRUPTED_BY_USER: 'interrupted_by_user',
  ORPHANED: 'orphaned',
});

/**
 * States from which the task will never advance again on its own.
 *
 * `waiting_for_approval` is deliberately absent: a human answering the approval
 * puts the task back into `running`, so it is not an end state. It still emits a
 * `not_done` handoff because the external agent's hook must know that nothing
 * will progress without a human.
 */
const TERMINAL_STATES = new Set([
  TASK_STATES.COMPLETED,
  TASK_STATES.FAILED,
  TASK_STATES.CANCELLED,
  TASK_STATES.INTERRUPTED_BY_USER,
  TASK_STATES.ORPHANED,
]);

/** States that a human decision or a new task can revive. */
const REVIVABLE_STATES = new Set([TASK_STATES.WAITING_FOR_APPROVAL]);

export function isTerminal(state) {
  return TERMINAL_STATES.has(state);
}

/** True when the task is parked pending a human decision rather than finished. */
export function isAwaitingDecision(state) {
  return REVIVABLE_STATES.has(state);
}

/**
 * The handoff signal is deliberately coarser than the state: the external agent's
 * hook reacts to exactly two values, so it never has to interpret our vocabulary.
 *
 * `done`     — the work on this task is over; read the result.
 * `not_done` — nothing will progress on its own. Either the task ended without
 *              finishing (interrupted, orphaned) or it is parked waiting for a
 *              human decision. Both mean "do not trust this as a result".
 */
export function handoffSignal(state) {
  switch (state) {
    case TASK_STATES.COMPLETED:
    case TASK_STATES.FAILED:
    case TASK_STATES.CANCELLED:
      return 'done';
    case TASK_STATES.INTERRUPTED_BY_USER:
    case TASK_STATES.WAITING_FOR_APPROVAL:
    case TASK_STATES.ORPHANED:
      return 'not_done';
    default:
      return null;
  }
}

/**
 * Map a `turn/end` reason onto a task state.
 *
 * `TurnEndReasonMap` (verbatim from @deepseek-ai/dsh-agent-loop):
 *   completed | aborted{reason: TurnEndCancelCause} | blocked | error{error}
 *   | max-tokens | interrupted | forked
 *   TurnEndCancelCause = {kind:'user'} | {kind:'parent'} | {kind:'hook',reason}
 *                      | {kind:'disposed'} | {kind:'legacy'}
 *
 * The user-typed distinction matters downstream: `interrupted_by_user` tells the
 * external agent that a human intervened, so it must re-check the result against
 * the latest intent instead of trusting the original prompt.
 */
export function stateFromTurnEnd(reason) {
  if (!reason || typeof reason !== 'object') {
    return { state: TASK_STATES.FAILED, detail: 'turn ended without a reason' };
  }
  switch (reason.kind) {
    case 'completed':
      return { state: TASK_STATES.COMPLETED, detail: null };
    case 'max-tokens':
      return { state: TASK_STATES.FAILED, detail: 'the turn hit its token limit' };
    case 'error': {
      const failure = reason.error ?? {};
      const code = failure.code ?? 'unknown';
      const message = failure.message ?? 'no message';
      return { state: TASK_STATES.FAILED, detail: `model error ${code}: ${message}` };
    }
    case 'aborted': {
      const cause = reason.reason ?? {};
      if (cause.kind === 'user') {
        return { state: TASK_STATES.INTERRUPTED_BY_USER, detail: 'a human interrupted this turn' };
      }
      const label = cause.kind === 'hook' && cause.reason ? `hook: ${cause.reason}` : String(cause.kind ?? 'unknown');
      return { state: TASK_STATES.CANCELLED, detail: `turn aborted (${label})` };
    }
    case 'blocked':
      return { state: TASK_STATES.FAILED, detail: 'the turn was blocked before running' };
    case 'interrupted':
      return { state: TASK_STATES.FAILED, detail: 'the turn was interrupted' };
    case 'forked':
      return { state: TASK_STATES.FAILED, detail: 'the turn was superseded by a fork' };
    default:
      return { state: TASK_STATES.FAILED, detail: `unrecognized turn end: ${String(reason.kind)}` };
  }
}

/** Rejection vocabulary written into `<task_id>.json.rejected`. */
export const REJECTION_REASONS = Object.freeze([
  'bad-json',
  'not-an-object',
  'missing-required-field',
  'wrong-field-type',
  'invalid-task-id',
  'task-id-already-used',
  'workspace-not-absolute',
  'workspace-not-found',
  'unknown-permission-preset',
  'unknown-agent-preset',
  'unknown-skill',
  'unknown-command',
  'session-not-found',
]);

const REQUIRED_FIELDS = ['task_id', 'workspace', 'prompt', 'permission_preset'];
const OPTIONAL_FIELDS = ['agent_preset', 'session_id', 'skills', 'commands'];
const STRING_FIELDS = [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS.filter((f) => f !== 'skills' && f !== 'commands')];
const STRING_ARRAY_FIELDS = ['skills', 'commands'];

// Conservative: letters, digits, dot, hyphen, underscore. No path separators, no
// whitespace — the task id becomes a file name, so anything else is a hazard.
const TASK_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function reject(reason, extra = {}) {
  return { ok: false, reason, ...extra };
}

/**
 * Validate one raw inbox payload.
 *
 * @param {unknown} raw          parsed JSON, or undefined when parsing failed
 * @param {object}  environment  what only the live host can answer:
 *   @param {boolean} parseFailed
 *   @param {(path: string) => boolean} workspaceExists
 *   @param {(name: string) => boolean} hasPermissionPreset
 *   @param {(id: string) => boolean}   hasAgentPreset
 *   @param {(name: string) => boolean} hasSkill
 *   @param {(name: string) => boolean} hasCommand        (name without leading slash)
 *   @param {(id: string) => boolean}   sessionExists
 *   @param {(taskId: string) => boolean} taskIdTaken
 * @returns {{ok: true, envelope: object} | {ok: false, reason: string, field?: string, message: string}}
 */
export function validateEnvelope(raw, environment) {
  const env = environment ?? {};
  if (env.parseFailed) {
    return reject('bad-json', { message: 'the task file is not valid JSON' });
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return reject('not-an-object', { message: 'the task file must contain a single JSON object' });
  }

  for (const field of REQUIRED_FIELDS) {
    if (!(field in raw)) {
      return reject('missing-required-field', {
        field,
        message:
          field === 'permission_preset'
            ? 'permission_preset is required and has no implicit default — pick one from the permissionPresets section of capabilities.json'
            : `${field} is required`,
      });
    }
  }

  const known = new Set([...REQUIRED_FIELDS, ...OPTIONAL_FIELDS]);
  for (const key of Object.keys(raw)) {
    if (!known.has(key)) {
      return reject('wrong-field-type', {
        field: key,
        message: `unknown field "${key}"; allowed fields are ${[...known].join(', ')}`,
      });
    }
  }

  for (const field of STRING_FIELDS) {
    if (field in raw && (typeof raw[field] !== 'string' || raw[field].trim() === '')) {
      return reject('wrong-field-type', { field, message: `${field} must be a non-empty string` });
    }
  }
  for (const field of STRING_ARRAY_FIELDS) {
    if (!(field in raw)) continue;
    const value = raw[field];
    if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || item.trim() === '')) {
      return reject('wrong-field-type', { field, message: `${field} must be an array of non-empty strings` });
    }
  }

  const taskId = raw.task_id;
  if (!TASK_ID_PATTERN.test(taskId)) {
    return reject('invalid-task-id', {
      field: 'task_id',
      message: 'task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127} (it becomes a file name)',
    });
  }
  if (env.taskIdTaken?.(taskId)) {
    return reject('task-id-already-used', {
      field: 'task_id',
      message: `task_id "${taskId}" already has a result; task ids are never reused — pick a new one`,
    });
  }

  const workspace = raw.workspace;
  if (!workspace.startsWith('/')) {
    return reject('workspace-not-absolute', {
      field: 'workspace',
      message: 'workspace must be an absolute path',
    });
  }
  if (env.workspaceExists && !env.workspaceExists(workspace)) {
    return reject('workspace-not-found', {
      field: 'workspace',
      message: `no such directory: ${workspace}`,
    });
  }

  if (env.hasPermissionPreset && !env.hasPermissionPreset(raw.permission_preset)) {
    return reject('unknown-permission-preset', {
      field: 'permission_preset',
      message: `unknown permission preset "${raw.permission_preset}"; available values are listed in capabilities.json`,
    });
  }

  if (raw.agent_preset !== undefined && env.hasAgentPreset && !env.hasAgentPreset(raw.agent_preset)) {
    return reject('unknown-agent-preset', {
      field: 'agent_preset',
      message: `unknown agent preset "${raw.agent_preset}"; available values are listed in capabilities.json`,
    });
  }

  if (raw.session_id !== undefined && env.sessionExists && !env.sessionExists(raw.session_id)) {
    return reject('session-not-found', {
      field: 'session_id',
      message: `no session with id "${raw.session_id}"`,
    });
  }

  for (const skill of raw.skills ?? []) {
    if (env.hasSkill && !env.hasSkill(skill)) {
      return reject('unknown-skill', {
        field: 'skills',
        message: `unknown skill "${skill}"; available skills are listed in capabilities.json`,
      });
    }
  }
  for (const line of raw.commands ?? []) {
    const name = line.startsWith('/') ? line.slice(1).split(/\s+/, 1)[0] : line.split(/\s+/, 1)[0];
    if (env.hasCommand && !env.hasCommand(name)) {
      return reject('unknown-command', {
        field: 'commands',
        message: `unknown command "${line}"; available commands are listed in capabilities.json`,
      });
    }
  }

  return {
    ok: true,
    envelope: {
      task_id: taskId,
      workspace,
      prompt: raw.prompt,
      permission_preset: raw.permission_preset,
      agent_preset: raw.agent_preset ?? null,
      session_id: raw.session_id ?? null,
      skills: [...(raw.skills ?? [])],
      commands: [...(raw.commands ?? [])],
    },
  };
}

/**
 * Compose the single prompt text handed to the DSH session.
 *
 * Named skills and extra commands are prepended as an explicit instruction block
 * rather than silently injected: the session is a normal session, so anything it
 * is asked to do must be visible both to the model and to the human watching the
 * conversation in the GUI.
 */
export function composePrompt(envelope) {
  const lines = [];
  if (envelope.skills.length > 0) {
    lines.push(`Use these skills: ${envelope.skills.join(', ')}.`);
  }
  if (envelope.commands.length > 0) {
    lines.push(`Run these commands first: ${envelope.commands.join(' ')}.`);
  }
  if (lines.length === 0) return envelope.prompt;
  return `${lines.join('\n')}\n\n${envelope.prompt}`;
}

/** Shape the capability manifest. Every array is passed in already resolved. */
export function buildManifest(input) {
  return {
    revision: input.revision,
    bridge: {
      version: input.version,
      generated_at: input.generatedAt,
    },
    docs: {
      manual: input.manualPath,
    },
    skills: input.skills,
    agentPresets: input.agentPresets,
    commands: input.commands,
    permissionPresets: input.permissionPresets,
  };
}

/** Human-readable rendering of the same manifest, for the GUI-less reader. */
export function renderManifestMarkdown(manifest) {
  const section = (title, rows, columns) => {
    if (rows.length === 0) return `## ${title}\n\n_(none available)_\n`;
    const head = `| ${columns.join(' | ')} |\n| ${columns.map(() => '---').join(' | ')} |`;
    const body = rows.map((cells) => `| ${cells.map((c) => String(c ?? '').replaceAll('|', '\\|')).join(' | ')} |`).join('\n');
    return `## ${title}\n\n${head}\n${body}\n`;
  };

  return [
    '# DSH capabilities',
    '',
    `Revision: \`${manifest.revision}\``,
    `Bridge version: ${manifest.bridge.version}`,
    `Generated at: ${manifest.bridge.generated_at}`,
    '',
    `How to use these: see ${manifest.docs.manual}`,
    '',
    section('Skills', manifest.skills.map((s) => [s.name, s.description]), ['name', 'description']),
    section('Agent presets', manifest.agentPresets.map((p) => [p.id, p.name, p.description, p.isDefault ? 'yes' : '']), ['id', 'name', 'description', 'default']),
    section('Commands', manifest.commands.map((c) => [c.name, c.description, c.inputHint]), ['name', 'description', 'input']),
    section('Permission presets', manifest.permissionPresets.map((p) => [p.value, p.name, p.description]), ['value', 'name', 'description']),
  ].join('\n');
}
