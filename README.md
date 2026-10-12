# DSH Codex Bridge

本仓库将 DeepSeek Harness（DSH）的通用文件桥插件与 Codex 插件放在同一份源码中。Codex 可以把有明确范围的任务交给 DSH，在 DSH 桌面会话中观察进度，并在完成后读取状态与成果。其他本地代理也可依照 [`USING-DSH.md`](USING-DSH.md) 使用文件协议。

## 安装（macOS）

需要已安装并可在终端调用的 `dsh`、`codex`、`git` 与 Python 3；DSH 须已创建 `desktop` profile。克隆到**会长期保留**的目录，再运行统一安装器：

```bash
git clone https://github.com/aria467/dsh-codex-bridge.git && cd dsh-codex-bridge && python3 scripts/install.py
```

安装器遵循 DSH 自身的依赖与供应链策略；若 DSH 拒绝安装，会恢复原有插件链接并停止，不会绕过策略。安装器把 DSH 包链接到该目录，确认 `@local/dsh-bridge` 已在 desktop bundle 列表中，安装 Codex marketplace 插件及 macOS LaunchAgent。已有的同名本地 marketplace 会迁移到新目录。安装完成后，**请手动重启 DSH**，并重启 Codex；若 Codex 提示信任 Stop hook，请先检查后自行决定。安装器不会自动重启 DSH，也不会自动开启同线程接续。

检查 DSH 桥接是否已启动：

```bash
python3 codex-plugin/scripts/bridge.py check
python3 codex-plugin/scripts/handoff.py probe
```

先运行一个只读任务，核对 DSH 会话、`~/.dsh-bridge/outbox/` 中的状态与结果，以及 Codex 的接续行为。完成可见的 UI 验证后，如需自动接续，创建 `~/.local/state/dsh-codex-bridge/auto_resume_enabled`；删除该文件即关闭自动接续。启动 LaunchAgent 本身不会开启这个开关。

## 更新与卸载

更新：在克隆目录运行 `git pull`，再运行 `python3 scripts/install.py`，随后手动重启 DSH 与 Codex。不要移动或删除克隆目录，否则 DSH 的本地链接会失效。

卸载：先关闭自动接续开关；运行 `codex plugin remove dsh-codex-bridge@local-dsh-bridge` 和 `codex plugin marketplace remove local-dsh-bridge`；运行 `dsh plugin --profile desktop remove @local/dsh-bridge`，检查 `~/.dsh/profiles/desktop/package.json` 的 `dsh.profile.bundles` 并移除该条目；随后手动重启 DSH。Codex 的 LaunchAgent `~/Library/LaunchAgents/com.aria.dsh-codex-bridge.plist` 可用 `launchctl bootout gui/$(id -u)/com.aria.dsh-codex-bridge` 停止，再删除 plist。历史任务与结果位于 `~/.dsh-bridge/` 和 `~/.local/state/dsh-codex-bridge/`，卸载不会自动删除它们。

## 使用与边界

Codex 插件的 `delegate` 技能负责构造任务，DSH 在普通桌面会话中执行。用户在 DSH 中批准、打断或修改任务后，Codex 应以最新状态为准。派活期间 Codex 不应编辑同一工作区。任务完成后先核对实际文件改动和测试结果，再继续原目标。

桥接文件只供同一台机器上的本地进程使用；拥有本机用户文件读写权的进程也可能读取或写入任务内容。请不要把 `~/.dsh-bridge/` 或 Codex 状态目录提交到仓库。当前桥接只传任务文本、路径、权限预设及成果，不提供 DSH 内部工具的远程调用。模型与思考强度由 DSH 会话设置决定；可选的 agent preset 由 DSH capabilities 公布。

## 故障排查

- `bridge.py check` 报缺少 `capabilities.json`：确认 DSH 已重启、desktop profile 含 `@local/dsh-bridge`，并查看 DSH 插件加载日志。
- Codex 插件仍指向旧目录：重跑统一安装器，再用 `codex plugin marketplace list --json` 检查 `local-dsh-bridge` 的 root。
- DSH 已完成而 Codex 未接续：检查 `~/.dsh-bridge/handoffs/`、`~/.local/state/dsh-codex-bridge/`，以及 LaunchAgent 日志；自动接续开关默认关闭。可在原 Codex 线程中手动提供任务编号并读取 outbox。
- DSH 中出现 `DeepSeek Messages transport failed`：这是模型传输失败，应先在 DSH 会话中检查其连接；桥接状态文件会记录该任务失败。

---

## DSH 文件桥实现说明

# @local/dsh-bridge

A file-mediated delegation bridge for DeepSeek Harness.

An external agent (Codex, Claude Code, or any program that can write a file) drops a
task envelope into `~/.dsh-bridge/inbox/`. This plugin claims it with an atomic
rename, runs the task as an **ordinary DSH session** in the requested workspace, and
writes the outcome to an outbox plus a handoff signal.

There is no server, no port, no token, and no protocol negotiation: both sides are
programs on one machine exchanging files.

## Why files instead of MCP

MCP's complexity exists to solve cross-vendor, cross-machine, unknown-peer problems.
This is two programs on one machine handing each other work, so the entire protocol
layer buys nothing and costs a lot: the client's tool timeout bounds how long a task
may run, the client may not receive server pushes, and authentication needs a
credential channel that ambient-environment scrubbing actively removes.

The filesystem has none of those properties. A task can run for hours; completion is
a file appearing, which is exactly what a hook watches; and the whole contract is
auditable with `cat`.

## Layout

```
~/.dsh-bridge/
├── capabilities.json   what DSH can currently do (skills, presets, commands, permissions)
├── capabilities.md     the same, human-readable
├── USING-DSH.md        the manual handed to external agents
├── inbox/              external agent writes here; `.claimed`/`.rejected` are bridge-owned
├── outbox/             receipt, status, and result per task
└── handoffs/           one signal file per finished task
```

See `USING-DSH.md` for the contract an external agent must follow.

## Design decisions worth not re-litigating

- **Tasks are ordinary sessions.** They appear in the GUI sidebar, the human can watch
  progress live, type into them, stop them, and answer their approval prompts. Nothing
  is hidden and nothing is tagged.
- **Cancellation belongs to the human.** There is no cancel file. Stopping work is done
  from the GUI, and the bridge reports the outcome (`interrupted_by_user`) so the
  external agent knows its original instruction may be stale.
- **Approvals are never auto-answered.** The bridge only reports that a task is parked.
  The plugin-development guidance is explicit that authorization actions are the user's.
- **Verification is the caller's job.** `result.md` records what the session did; it does
  not run tests or lint.
- **Task ids are never reused, and nothing is auto-deleted.** Both follow from the
  ledger being append-only, which is what makes manual bookkeeping possible.

## Config

| Field | Default | Meaning |
|---|---|---|
| `root` | `''` → `<home>/.dsh-bridge` | Runtime directory |
| `pollMs` | `1000` | Inbox scan interval |
| `manualName` | `USING-DSH.md` | Manual file name inside `root` |

## Development

Host plugin code has **no hot reload**: after changing `index.js`, reinstall the bundle
and restart DSH. Config-only changes are applied live.

```bash
node --test test/protocol.test.mjs
```

`lib/protocol.js` is deliberately free of Cordis, filesystem, and harness dependencies
so it can be tested outside a running host.
