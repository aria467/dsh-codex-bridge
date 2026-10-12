---
name: delegate-to-dsh
description: Delegate a scoped coding task to the local DeepSeek Harness, then review its handoff in the same Codex conversation.
---

# Delegate to DeepSeek Harness

Use this workflow when the user asks Codex to route work to DSH. DSH runs separately and may take minutes. The user can intervene in its desktop session.

1. Read `~/.dsh-bridge/capabilities.json`. Select a listed `permissionPresets[].value` and, where relevant, a listed agent or skill preset.
2. Draft a strong, self-contained prompt: objective, absolute workspace, exact scope, constraints, acceptance criteria, expected report, and how to handle ambiguity or failures. Give DSH enough context to work without rapid back-and-forth.
3. Write the prompt to a temporary UTF-8 file, then run `python3 "${PLUGIN_ROOT}/scripts/bridge.py" submit --workspace /absolute/workspace --permission-preset PRESET --prompt-file /path/to/prompt`. For a new DSH session, add `--agent-preset standard` or another ID from `capabilities.json` when the user specifies a role. Do not combine it with `--dsh-session-id`; an existing session keeps its original role. This writes an atomic task envelope and records the current Codex thread ID. Do not write directly to the DSH inbox.
4. Tell the user the returned task ID, then end the turn. Do not touch workspace files while DSH is working. The Stop hook arms the handoff.
5. When a handoff arrives, read its status and result. Inspect the actual diff and run appropriate checks yourself. A DSH summary is not verification.

`waiting_for_approval` means the human must act in DSH. Never approve on their behalf. `interrupted_by_user` means the user's requirements may have changed; ask before repeating the work. A rejected envelope requires a new task ID. Do not run concurrent DSH tasks in the same workspace.

Automatic continuation requires a separately configured LaunchAgent and the `auto_resume_enabled` gate. Without it, the user can resume Codex manually with the task ID.
