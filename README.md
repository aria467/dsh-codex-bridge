# DSH Codex Bridge

[English](./README.md) · [简体中文](./docs/README_zh.md)

DSH Codex Bridge lets Codex delegate a scoped task to DeepSeek Harness (DSH), then return to the same Codex conversation to review the outcome. The repository contains a DSH plugin that accepts work from local agents and a Codex plugin that submits tasks and handles their results.

## Why this project exists

A DSH task may take longer than a single tool call, and the person using DSH may need to watch the work, answer an approval request, or change direction. This bridge gives that work its own visible DSH session while Codex keeps the original request and reviews the outcome afterward. It is useful for coding tasks with a clear workspace, constraints, and acceptance criteria.

The DSH side uses a local file protocol rather than a network service. Other agents on the same computer can use it too; see [the DSH protocol manual](./USING-DSH.md). The bridge does not expose DSH's internal tools to Codex. DSH performs the delegated task; Codex checks the result and decides what to do next.

## How the handoff works

1. Codex writes a task with its prompt, workspace, and permission preset to `~/.dsh-bridge/inbox/`, and records the originating Codex thread.
2. The DSH plugin claims the task and runs it in an ordinary DSH session. The session is visible in DSH, where the user can intervene or handle approvals.
3. DSH writes a receipt, status, and result under `outbox/`, then writes a signal under `handoffs/`.
4. The Codex Stop hook marks the submitted task ready for handoff. A macOS LaunchAgent watches for DSH's signal; when automatic continuation is enabled and the Codex thread is idle, it starts a new turn in that thread. Codex reads the status and result, inspects actual workspace changes, and continues the original request.

Automatic continuation is **off by default**. The user can always resume Codex manually with the task ID. Codex should not edit the same workspace while DSH is working, and DSH's result report is not a substitute for checking the files or running relevant tests. Approvals remain with the user in DSH.

### Choose an entry point

- **Using Codex:** Install both plugins below, then ask Codex to delegate a bounded task. For a first check, try: “Ask DSH to read this project's README and report its first Markdown heading and absolute path. Use a read-only permission preset; do not change files.” Check the DSH session and the returned result.
- **Integrating another local agent:** Follow [the task envelope and handoff contract](./USING-DSH.md). That manual describes the fields, capability discovery, states, and rejection handling.

## Installation

The v1 installer supports **macOS**. Install DSH, Codex, Git, and Python 3 so `dsh`, `codex`, `git`, and `python3` are available in your terminal. DSH must already have a `desktop` profile. Clone this repository into a directory you will keep: DSH installs the package as a local link to that directory.

```bash
git clone https://github.com/aria467/dsh-codex-bridge.git && cd dsh-codex-bridge && python3 scripts/install.py
```

The installer links `@aria467/dsh-bridge` from this checkout into the DSH desktop profile, installs `dsh-codex-bridge@local-dsh-bridge` in Codex, and sets up the macOS handoff LaunchAgent. If the older `@local/dsh-bridge` package is installed, it removes that package and bundle entry after adding the new one. The package is not fetched from npm. The installer does not restart either app. **Restart DSH and Codex manually** after installation. If Codex asks whether to trust the Stop hook, review that prompt yourself.

From the checkout, verify that DSH has published its capabilities and that the Codex app-server is reachable:

```bash
python3 codex-plugin/scripts/bridge.py check
python3 codex-plugin/scripts/handoff.py probe
```

Then run the read-only task above and inspect the DSH session and `outbox/` result. After a visible handoff check, enable automatic continuation only if you want it:

```bash
touch ~/.local/state/dsh-codex-bridge/auto_resume_enabled
```

Remove that file to disable it. The installer and LaunchAgent do not enable it for you; reinstalling preserves an existing switch.

### Update and troubleshoot

Run `git pull` and `python3 scripts/install.py` from the same checkout, then restart DSH and Codex manually. Keep the checkout in place while it is installed.

If `bridge.py check` cannot find `capabilities.json`, confirm that DSH restarted with the desktop profile and loaded the bridge bundle. If DSH's package manager rejects an install under its supply-chain policy, the installer stops without bypassing that policy; it restores the previous plugin link when one existed. Resolve the policy condition and rerun the installer. If DSH cannot remove the old local package, the installer backs up the profile and lockfile before removing only that local-link entry. If DSH finishes but Codex does not resume, check the automatic continuation switch, `handoffs/`, and `~/.local/state/dsh-codex-bridge/launchagent.err.log`. The task can still be reviewed manually from `outbox/`.

To uninstall, disable automatic continuation, remove the Codex plugin and marketplace with `codex plugin remove dsh-codex-bridge@local-dsh-bridge` and `codex plugin marketplace remove local-dsh-bridge`, then remove the DSH package with `dsh plugin --profile desktop remove @aria467/dsh-bridge`. Remove its entry from `dsh.profile.bundles` in the desktop profile if it remains. Stop and remove `~/Library/LaunchAgents/com.aria.dsh-codex-bridge.plist` separately. Task history is not deleted automatically.

## Local files and settings

`~/.dsh-bridge/` is the **runtime directory**, not a configuration file. It contains task prompts and results, so treat it as private local data rather than repository content.

| Path | Purpose |
| --- | --- |
| `capabilities.json`, `capabilities.md` | DSH's current presets, skills, commands, and permissions in machine-readable and readable forms. |
| `USING-DSH.md` | Optional local protocol manual; the capabilities manifest points to its configured path. |
| `inbox/` | Incoming task JSON; DSH also keeps claimed or rejected envelopes here. |
| `outbox/` | Per-task receipt, current status, and Markdown result. |
| `handoffs/` | Signals that a task has reached a handoff state. |

The DSH plugin's configuration fields are separate from those runtime files:

| Field | Default | Purpose |
| --- | --- | --- |
| `root` | Empty, meaning `~/.dsh-bridge` | Runtime directory. A custom root must also be configured on the Codex side. |
| `pollMs` | `1000` | Interval in milliseconds for scanning the inbox. |
| `manualName` | `USING-DSH.md` | Name of the protocol manual path published in the capabilities manifest. |

Codex keeps task bookkeeping and the `auto_resume_enabled` switch under `~/.local/state/dsh-codex-bridge/`. These are not DSH plugin settings. For the full file contract, use [the protocol manual](./USING-DSH.md).

## License

Released under the [MIT License](./LICENSE).
