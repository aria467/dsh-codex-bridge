/**
 * dsh-bridge — a file-mediated delegation bridge for DeepSeek Harness.
 *
 * External agents drop a task envelope into an inbox directory. This plugin claims
 * the file with an atomic rename, runs the task as an ordinary DSH session, writes
 * the outcome into an outbox, and drops a handoff signal for the external agent's
 * own hook to notice.
 *
 * There is no server, no port, no token, and no protocol negotiation: both sides
 * are programs on one machine exchanging files.
 *
 * Deliberate non-features (see the design record for why):
 *   - no HTTP route, no tool registration, no `$events` subscription
 *   - no task cancellation: stopping work is the human's move, from the GUI
 *   - no auto-answering of approvals: the bridge only reports them
 *   - no automatic cleanup, no concurrent scheduling, no source tagging
 *   - no rewriting of anything DSH produced
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

import z from '@deepseek-ai/schemastery';

import {
  TASK_STATES,
  handoffSignal,
  isTerminal,
  stateFromTurnEnd,
  validateEnvelope,
  composePrompt,
  buildManifest,
  renderManifestMarkdown,
} from './lib/protocol.js';

export const name = 'dsh-bridge';

/**
 * Every service listed here is a hard dependency: if one is absent the plugin
 * simply does not activate (Cordis semantics), which is the behavior we want —
 * the bridge is useless without them and should not half-run.
 *
 * sessionController.prompt(request, signal) requires an AbortSignal even for
 * in-process callers. The signal is checked before admission to the normal
 * DSH prompt queue.
 */
export const inject = [
  'sessionController',
  'workspaceRegistry',
  'permissionPresets',
  'skills',
  'agentPresets',
  'commands',
  'sessionQuery',
];

export const Config = z.object({
  root: z.string().default(''),
  pollMs: z.natural().default(1000),
  manualName: z.string().default('USING-DSH.md'),
});

const DIRS = ['inbox', 'outbox', 'handoffs'];

/** Session ids created by the bridge, so event routing can filter cheaply. */
const owned = new Map();

export function apply(ctx, config) {
  const root = config.root === '' ? path.join(os.homedir(), '.dsh-bridge') : config.root;
  const manualPath = path.join(root, config.manualName);
  const revision = String(Date.now());

  for (const dir of DIRS) fs.mkdirSync(path.join(root, dir), { recursive: true });

  const rejectedPath = (id) => path.join(root, 'inbox', `${id}.rejected`);
  const receiptPath = (id) => path.join(root, 'outbox', `${id}.receipt.json`);
  const statusPath = (id) => path.join(root, 'outbox', `${id}.status.json`);
  const resultPath = (id) => path.join(root, 'outbox', `${id}.result.md`);
  const handoffPath = (id) => path.join(root, 'handoffs', `${id}.json`);

  const writeJson = (file, value) => {
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`);
    fs.renameSync(tmp, file);
  };
  const readJson = (file) => {
    try {
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
      return undefined;
    }
  };

  // ---------------------------------------------------------------- capabilities

  /** The control agent exists only so command enumeration has a live scope. */
  let controlAgent;

  const ensureControlAgent = async () => {
    if (controlAgent !== undefined) return controlAgent;
    // A dedicated, never-prompted session exists only to give command enumeration a
    // live agent scope: ctx.commands.list(agent) resolves agent-scoped shadows.
    const created = await ctx.sessionController.create({ cwd: root });
    const resolved = await ctx.sessionController.resolveAgent(created.sessionId);
    if ('error' in resolved) throw resolved.error;
    controlAgent = resolved.agent;
    return controlAgent;
  };

  const collectCapabilities = async () => {
    const [skills, presets] = await Promise.all([
      ctx.skills.list(),
      ctx.agentPresets.list(),
    ]);
    const catalog = ctx.permissionPresets.catalog();

    let commands = [];
    try {
      const agent = await ensureControlAgent();
      commands = ctx.commands.list(agent);
    } catch (error) {
      ctx.logger?.warn?.(`dsh-bridge: command enumeration failed: ${String(error)}`);
    }

    return {
      skills: skills.map((skill) => ({ name: skill.name, description: skill.description ?? '' })),
      agentPresets: presets.map((preset) => ({
        id: preset.id,
        name: preset.name ?? '',
        description: preset.description ?? '',
        isDefault: preset.isDefault === true,
      })),
      commands: commands.map((command) => ({
        name: command.name,
        description: command.description ?? '',
        inputHint: command.input?.hint ?? '',
      })),
      permissionPresets: catalog.options.map((option) => ({
        value: option.value,
        name: option.name ?? '',
        description: option.description ?? '',
      })),
    };
  };

  const publishCapabilities = async () => {
    const caps = await collectCapabilities();
    const manifest = buildManifest({
      revision,
      version: '0.1.3',
      generatedAt: new Date().toISOString(),
      manualPath,
      ...caps,
    });
    writeJson(path.join(root, 'capabilities.json'), manifest);
    fs.writeFileSync(path.join(root, 'capabilities.md'), renderManifestMarkdown(manifest));
    return manifest;
  };

  // ------------------------------------------------------------------ validation

  /**
   * A task id counts as used once it produced a durable outcome — a result, a
   * rejection, or a handoff.
   *
   * It must NOT consult the `.claimed` file: claiming happens immediately before
   * validation, so reading it back would make every task reject itself. The claim
   * is still the guard against two deliveries of one id, because the atomic rename
   * lets exactly one of them win (the loser's source path is already gone).
   */
  const taskIdTaken = (id) =>
    fs.existsSync(resultPath(id)) ||
    fs.existsSync(rejectedPath(id)) ||
    fs.existsSync(handoffPath(id));

  const validate = (raw, parseFailed) => {
    const tasks = new Set(owned.keys());
    return validateEnvelope(raw, {
      parseFailed,
      workspaceExists: (p) => {
        try {
          return fs.statSync(p).isDirectory();
        } catch {
          return false;
        }
      },
      hasPermissionPreset: (name) => {
        try {
          ctx.permissionPresets.resolve(name);
          return true;
        } catch {
          return false;
        }
      },
      hasAgentPreset: (id) => {
        try {
          ctx.agentPresets.resolve(id);
          return true;
        } catch {
          return false;
        }
      },
      hasSkill: (skillName) => cmdCache.skills.has(skillName),
      hasCommand: (cmdName) => cmdCache.commands.has(cmdName),
      // ctx.sessions.get only sees live sessions; the cached list also covers cold ones.
      sessionExists: (id) => ctx.sessions.get(id) !== undefined || cmdCache.sessions.has(id),
      taskIdTaken: (id) => tasks.has(id) || taskIdTaken(id),
    });
  };

  // Capability snapshots the validator consults; refreshed alongside the manifest.
  const cmdCache = { skills: new Set(), commands: new Set(), sessions: new Set() };

  const refreshCaches = async () => {
    const caps = await collectCapabilities();
    cmdCache.skills = new Set(caps.skills.map((s) => s.name));
    cmdCache.commands = new Set(caps.commands.map((c) => c.name));
    try {
      const sessions = await ctx.sessionController.list();
      cmdCache.sessions = new Set(sessions.map((s) => s.id));
    } catch (error) {
      ctx.logger?.warn?.(`dsh-bridge: session list failed: ${String(error)}`);
    }
  };

  // ---------------------------------------------------------------- task running

  const writeHandoff = (taskId) => {
    const status = readJson(statusPath(taskId)) ?? {};
    const signal = handoffSignal(status.state);
    if (signal === null) return;
    writeJson(handoffPath(taskId), {
      task_id: taskId,
      signal,
      terminal_state: status.state,
      result_path: resultPath(taskId),
      status_path: statusPath(taskId),
      session_id: status.session_id ?? null,
    });
  };

  const setStatus = (taskId, patch) => {
    const previous = readJson(statusPath(taskId)) ?? { task_id: taskId };
    const next = { ...previous, ...patch, since: new Date().toISOString() };
    writeJson(statusPath(taskId), next);
    return next;
  };

  const onSessionEvent = (session, event) => {
    const task = owned.get(String(session.id));
    if (task === undefined) return;

    if (event.type === 'turn/start') {
      setStatus(task.taskId, { state: TASK_STATES.RUNNING, detail: null });
      return;
    }
    if (event.type === 'approval/asked') {
      setStatus(task.taskId, {
        state: TASK_STATES.WAITING_FOR_APPROVAL,
        detail: `waiting for a human decision on ${event.data.toolName}`,
        approval: { toolName: event.data.toolName, reason: event.data.reason ?? null },
      });
      writeHandoff(task.taskId);
      return;
    }
    if (event.type === 'approval/decided') {
      setStatus(task.taskId, {
        state: TASK_STATES.RUNNING,
        detail: `approval decided: ${event.data.outcome}`,
        approval: null,
      });
      return;
    }
    if (event.type === 'tool/call') {
      task.toolCalls.push({ name: event.data.name, isError: null });
      return;
    }
    if (event.type === 'tool/result') {
      const pending = task.toolCalls[task.toolCalls.length - 1];
      if (pending !== undefined && pending.isError === null) {
        pending.isError = event.data.error !== undefined;
      }
      return;
    }
    if (event.type === 'assistant/message') {
      const text = (event.data.message?.content ?? [])
        .filter((block) => block.type === 'text')
        .map((block) => block.text)
        .join('')
        .trim();
      if (text !== '') task.lastAssistantText = text;
      return;
    }
    if (event.type === 'turn/end') {
      const { state, detail } = stateFromTurnEnd(event.data.reason);
      setStatus(task.taskId, { state, detail });
      writeResult(task);
      writeHandoff(task.taskId);
    }
  };

  const writeResult = (task) => {
    const status = readJson(statusPath(task.taskId)) ?? {};
    const lines = [
      `# Task ${task.taskId}`,
      '',
      `- state: \`${status.state}\``,
      `- detail: ${status.detail ?? '(none)'}`,
      `- workspace: \`${task.envelope.workspace}\``,
      `- session: \`${task.sessionId}\``,
      `- permission preset requested: \`${task.envelope.permission_preset}\``,
      '',
      '## Result',
      '',
      task.lastAssistantText ?? '_(the session produced no assistant text)_',
      '',
      '## Tools called',
      '',
    ];
    if (task.toolCalls.length === 0) {
      lines.push('_(none)_');
    } else {
      lines.push('| tool | outcome |', '| --- | --- |');
      for (const call of task.toolCalls) {
        const outcome = call.isError === null ? 'unknown' : call.isError ? 'error' : 'ok';
        lines.push(`| ${call.name} | ${outcome} |`);
      }
    }
    lines.push('', '## Verification', '', 'This report only records what the session did. It does not run tests,', 'lint, or any other check — verifying the result is the caller\'s job.', '');
    fs.writeFileSync(resultPath(task.taskId), `${lines.join('\n')}\n`);
  };

  const dispatch = async (taskId, envelope) => {
    const promptText = composePrompt(envelope);

    let workspace = await ctx.workspaceRegistry.resolveByPath(envelope.workspace);
    if (workspace === undefined) workspace = await ctx.workspaceRegistry.create(envelope.workspace);

    let sessionId = envelope.session_id;
    if (sessionId === null) {
      const created = await ctx.sessionController.create({
        workspaceId: workspace.id,
        ...(envelope.agent_preset === null ? {} : { agentPreset: envelope.agent_preset }),
      });
      sessionId = created.sessionId;
    }
    sessionId = String(sessionId);

    // Register ownership and the initial status BEFORE submitting, because followup
    // wakes the driver synchronously and the first events would otherwise arrive
    // while this task is still unknown to the event router.
    const task = { taskId, envelope, sessionId, toolCalls: [], lastAssistantText: null };
    owned.set(sessionId, task);
    setStatus(taskId, {
      state: TASK_STATES.SUBMITTED,
      detail: null,
      session_id: sessionId,
      workspace: envelope.workspace,
    });

    // A newly created session is not in ctx.sessions yet, so resolve the live agent
    // rather than reading the session store directly. permissionPresets.set() takes
    // a Session, and this must happen before the prompt so the turn starts under the
    // requested policy rather than changing mid-turn.
    let agent;
    try {
      const resolved = await ctx.sessionController.resolveAgent(sessionId);
      if ('error' in resolved) throw resolved.error;
      agent = resolved.agent;
      ctx.permissionPresets.set(agent.session, envelope.permission_preset);
    } catch (error) {
      throw new Error(`could not prepare session ${sessionId}: ${String(error?.message ?? error)}`);
    }

    // The request ID keeps prompt admission idempotent if a caller retries.
    await ctx.sessionController.prompt({
      requestId: taskId,
      sessionId,
      mode: 'queue',
      content: [{ type: 'text', text: promptText }],
    }, new AbortController().signal);

    writeJson(receiptPath(taskId), {
      task_id: taskId,
      session_id: sessionId,
      workspace: envelope.workspace,
      workspace_id: workspace.id,
      permission_preset: envelope.permission_preset,
      agent_preset: envelope.agent_preset,
      accepted_at: new Date().toISOString(),
    });

    setStatus(taskId, { state: TASK_STATES.RUNNING, detail: null });
  };

  // ------------------------------------------------------------------ inbox scan

  const scan = async () => {
    let names;
    try {
      names = fs.readdirSync(path.join(root, 'inbox'));
    } catch {
      return;
    }
    for (const entry of names) {
      if (!entry.endsWith('.json')) continue;
      const file = path.join(root, 'inbox', entry);
      const base = entry.slice(0, -'.json'.length);

      const raw = fs.readFileSync(file, 'utf8');
      let parsed;
      let parseFailed = false;
      try {
        parsed = JSON.parse(raw);
      } catch {
        parseFailed = true;
      }

      // Claim first: a single rename wins, so a second bridge instance or a racing
      // external agent cannot make this task run twice.
      const claimed = `${file}.claimed`;
      try {
        fs.renameSync(file, claimed);
      } catch {
        continue;
      }

      const verdict = validate(parsed, parseFailed);
      if (!verdict.ok) {
        writeJson(rejectedPath(base), {
          task_id: parseFailed ? null : (parsed?.task_id ?? null),
          rejected_at: new Date().toISOString(),
          reason: verdict.reason,
          field: verdict.field ?? null,
          message: verdict.message,
        });
        continue;
      }

      try {
        await dispatch(verdict.envelope.task_id, verdict.envelope);
      } catch (error) {
        setStatus(verdict.envelope.task_id, {
          state: TASK_STATES.FAILED,
          detail: `the bridge could not start this task: ${String(error?.message ?? error)}`,
        });
        const task = [...owned.values()].find((item) => item.taskId === verdict.envelope.task_id)
          ?? { taskId: verdict.envelope.task_id, envelope: verdict.envelope, sessionId: null, toolCalls: [], lastAssistantText: null };
        writeResult(task);
        writeHandoff(verdict.envelope.task_id);
      }
    }
  };

  /** Claimed files with no result were cut off by a previous process. */
  const reconcile = () => {
    const dir = path.join(root, 'inbox');
    let names;
    try {
      names = fs.readdirSync(dir);
    } catch {
      return;
    }
    for (const entry of names) {
      if (!entry.endsWith('.json.claimed')) continue;
      const taskId = entry.slice(0, -'.json.claimed'.length);
      if (fs.existsSync(resultPath(taskId)) || fs.existsSync(rejectedPath(taskId))) continue;
      const status = readJson(statusPath(taskId));
      if (status !== undefined && isTerminal(status.state)) continue;
      setStatus(taskId, {
        state: TASK_STATES.ORPHANED,
        detail: 'the previous DSH process ended before this task finished',
      });
      writeHandoff(taskId);
    }
  };

  // ------------------------------------------------------------------- lifecycle

  ctx.effect(() => {
    // `global: true` is the safe superset: an unscoped listener already receives
    // every session's events, but scoped dispatch rules make the explicit flag the
    // documented way to guarantee it.
    const off = ctx.on('session/event', onSessionEvent, { global: true });
    return () => off?.();
  }, 'dsh-bridge: session event routing');

  ctx.effect(() => {
    reconcile();
    void publishCapabilities().catch((error) => {
      ctx.logger?.warn?.(`dsh-bridge: capability publish failed: ${String(error)}`);
    });
    const timer = setInterval(() => {
      void refreshCaches().catch(() => {});
      void scan().catch((error) => {
        ctx.logger?.warn?.(`dsh-bridge: inbox scan failed: ${String(error)}`);
      });
    }, config.pollMs);
    return () => clearInterval(timer);
  }, 'dsh-bridge: inbox polling');
}
