# DSH Codex Bridge

A local Codex plugin that submits scoped work to the existing DeepSeek Harness file bridge and reviews its handoffs. It adds a delegation skill, a Stop hook, and an optional macOS LaunchAgent. The LaunchAgent also checks that the Codex thread is idle before continuing, so handoff delivery does not depend on hook trust. It does not expose DSH's internal tools or require an MCP server.

## Install

Run `python3 install.py` from this directory. The installer registers this source as a local Codex marketplace, installs the plugin with the Codex CLI, and installs a LaunchAgent. Restart Codex and review the plugin's hook trust prompt. Automatic same-thread continuation stays disabled until a visible UI pilot passes.

Run `python3 scripts/bridge.py check` and `python3 scripts/handoff.py probe` for diagnostics. To inspect pending work, run `python3 scripts/bridge.py status` and `python3 scripts/handoff.py once`.

After the UI pilot, create `~/.local/state/dsh-codex-bridge/auto_resume_enabled` to enable the LaunchAgent. Remove that file to disable automatic continuation. The user retains control of DSH approvals and cancellation in its own desktop UI.
