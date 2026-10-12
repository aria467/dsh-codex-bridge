# DSH Codex Bridge

[English](../README.md) · [简体中文](./README_zh.md)

DSH Codex Bridge 让 Codex 将范围明确的任务交给 DeepSeek Harness（DSH），再回到同一个 Codex 会话检查成果。本仓库同时包含接收本地代理任务的 DSH 插件，以及派发任务和处理交接结果的 Codex 插件。

## 为什么开发这个项目

DSH 的一项任务可能持续超过单次工具调用的等待时间；用户也可能需要观察工作过程、批准操作或改变要求。这个桥接让任务在可见的 DSH 会话中执行，Codex 则保留原始目标，并在任务结束后复核成果。它适合工作目录、约束和验收条件都能明确写出的编码任务。

DSH 端使用本地文件协议，不启动网络服务。同一台电脑上的其他代理也能接入，具体格式见 [DSH 协议手册](../USING-DSH.md)。桥接不会把 DSH 的内部工具暴露给 Codex。DSH 执行派出的任务；Codex 检查结果并决定下一步。

## 工作如何交接

1. Codex 将提示词、工作目录和权限预设写入 `~/.dsh-bridge/inbox/`，同时记下原 Codex 会话编号。
2. DSH 插件领取任务，在普通 DSH 会话中执行。用户可以在 DSH 界面观察、介入或处理批准请求。
3. DSH 将回执、状态和结果写入 `outbox/`，并在 `handoffs/` 写入交接信号。
4. Codex 的 Stop hook 将已派发任务标记为可交接。macOS LaunchAgent 检查 DSH 信号；启用自动接续且原 Codex 会话空闲时，它会在该会话启动新一轮。Codex 随后读取状态和结果，核对工作区的实际改动，并继续原始目标。

**默认关闭自动接续。** 用户也可以凭任务编号手动让 Codex 继续。DSH 工作期间，Codex 不应同时修改同一工作区；DSH 的成果报告也不能代替文件核对或必要的测试。批准操作始终由用户在 DSH 中处理。

### 两种使用入口

- **通过 Codex 使用：**按下文安装两端插件，然后让 Codex 派出有明确范围的任务。第一次可用只读任务验证：“请让 DSH 读取当前项目的 README，报告第一个 Markdown 标题和文件绝对路径。使用只读权限预设，不修改文件。”随后查看 DSH 会话及返回的结果。
- **接入其他本地代理：**阅读[任务信封与交接协议](../USING-DSH.md)，其中列出字段、能力发现、任务状态和拒绝处理。

## Installation / 本地安装

首版安装器支持 **macOS**。先安装 DSH、Codex、Git 和 Python 3，确保终端能调用 `dsh`、`codex`、`git` 和 `python3`；DSH 还需已有 `desktop` profile。将仓库克隆到准备长期保留的目录，因为 DSH 会通过本地链接引用该目录。

```bash
git clone https://github.com/aria467/dsh-codex-bridge.git && cd dsh-codex-bridge && python3 scripts/install.py
```

安装器会把 `@local/dsh-bridge` 加入 DSH desktop profile，安装 Codex 插件 `dsh-codex-bridge@local-dsh-bridge`，并设置 macOS 交接 LaunchAgent。它不会重启应用。安装后请**手动重启 DSH 和 Codex**。若 Codex 提示是否信任 Stop hook，请自行核查该提示。

在仓库目录运行以下命令，检查 DSH 是否已发布能力信息，以及 Codex app-server 是否可连接：

```bash
python3 codex-plugin/scripts/bridge.py check
python3 codex-plugin/scripts/handoff.py probe
```

再执行上面的只读任务，检查 DSH 会话与 `outbox/` 结果。确认交接过程可见且符合预期后，如需自动接续，可创建开关文件：

```bash
touch ~/.local/state/dsh-codex-bridge/auto_resume_enabled
```

删除该文件即可关闭自动接续。安装器和 LaunchAgent 都不会替你开启此开关。

### 更新与排障

在同一个克隆目录执行 `git pull` 和 `python3 scripts/install.py`，然后手动重启 DSH 与 Codex。安装期间不要移动或删除该目录。

若 `bridge.py check` 找不到 `capabilities.json`，确认 DSH 已重启、使用 desktop profile，且加载了桥接插件。若 DSH 包管理器因供应链策略拒绝安装，安装器会停止，不会绕过该策略；原先存在插件链接时，它会恢复旧链接。解决策略限制后重试即可。若 DSH 已完成而 Codex 没有接续，检查自动接续开关、`handoffs/` 和 `~/.local/state/dsh-codex-bridge/launchagent.err.log`；仍可从 `outbox/` 手动核对任务成果。

卸载时，先关闭自动接续，再运行 `codex plugin remove dsh-codex-bridge@local-dsh-bridge`、`codex plugin marketplace remove local-dsh-bridge` 和 `dsh plugin --profile desktop remove @local/dsh-bridge`。若 desktop profile 的 `dsh.profile.bundles` 中仍有该插件条目，需手动移除。`~/Library/LaunchAgents/com.aria.dsh-codex-bridge.plist` 也需单独停止并删除。历史任务与结果不会自动删除。

## 本地文件与配置

`~/.dsh-bridge/` 是**运行目录**，不是配置文件。这里可能包含任务提示词与成果，应作为本机私有数据处理，不要提交进仓库。

| 路径 | 用途 |
| --- | --- |
| `capabilities.json`、`capabilities.md` | DSH 当前的预设、技能、命令与权限，分别供程序和人阅读。 |
| `USING-DSH.md` | 可放在运行目录中的本地协议手册；能力清单会指向配置的手册路径。 |
| `inbox/` | 待领取的任务 JSON；DSH 也在这里保留已领取或被拒绝的任务信封。 |
| `outbox/` | 每项任务的回执、当前状态和 Markdown 成果。 |
| `handoffs/` | 任务到达交接状态时写入的信号。 |

DSH 插件有以下配置字段，与运行目录中的文件不同：

| 字段 | 默认值 | 用途 |
| --- | --- | --- |
| `root` | 空值，表示 `~/.dsh-bridge` | 运行目录；若更改，Codex 端也需同步配置。 |
| `pollMs` | `1000` | 扫描 inbox 的间隔，单位毫秒。 |
| `manualName` | `USING-DSH.md` | 能力清单中所指向的运行目录内协议手册名称。 |

Codex 的任务记录与 `auto_resume_enabled` 开关位于 `~/.local/state/dsh-codex-bridge/`，不属于 DSH 插件配置。完整文件协议见 [DSH 协议手册](../USING-DSH.md)。

## 许可证

本项目采用 [MIT License](../LICENSE)。
