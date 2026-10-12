---
name: setup-dsh-bridge
description: Set up and diagnose the local Codex to DeepSeek Harness plugin and its optional same-thread handoff.
---

# Set up the DSH bridge

The DSH desktop plugin must already be running and publishing `~/.dsh-bridge/capabilities.json`. Install this local Codex plugin using the accompanying `install.py`. Review and trust its Stop hook in Codex if prompted. The hook only arms tasks when a Codex turn ends.

Run `python3 "${PLUGIN_ROOT}/scripts/bridge.py" check` to inspect bridge availability and `python3 "${PLUGIN_ROOT}/scripts/handoff.py" probe` to check the local app-server socket. The macOS LaunchAgent polls only for handoff signals. It does not continue Codex until `~/.local/state/dsh-codex-bridge/auto_resume_enabled` exists.

Before enabling automatic continuation, send a harmless pilot task to DSH, end the Codex turn, and confirm that a subsequent `turn/start` appears in the same visible Codex chat. The socket thread ID alone does not prove the desktop UI updated. If the UI does not show the continuation, leave the gate off and use manual resume.
