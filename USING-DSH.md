# 通过文件把工作派给 DeepSeek Harness

**这份文档写给你——一个编码 agent。** 读完它，你就能把一项工作委托给本机上运行的 DeepSeek Harness（以下简称 DSH）去执行，并在它干完之后拿回结果。

先看清楚你我的分工，这决定了后面每一条规则为什么要那样写：

| 角色 | 是谁 | 负责什么 |
|---|---|---|
| **你** | 读这份文档的 agent | 派活、拿结果、**自己检查成果**、决定下一步 |
| **执行者** | DSH 会话 | 真正的活全在它那里干：读代码、改文件、跑命令 |
| **人** | 这台机器的使用者 | 在 DSH 界面里**实时看着**会话，随时插话、叫停、批准操作 |

有三件事你要牢牢记住，后面会反复用到：

1. **通信全靠文件。** 没有网络、没有端口、没有 token、没有协议握手。你把一个 JSON 文件写进一个目录，就是派活。
2. **DSH 干活期间你不要碰文件。** 你在把活交出去之后就结束了，等被唤醒再回来。这是分工，不是限制。
3. **结果要你自己验收。** DSH 只报告它做了什么，**不会替你跑测试**。检查成果是你的职责。

---

## 1. 一分钟上手

目录是 `~/.dsh-bridge/`，你只需要碰其中三个地方：

```
~/.dsh-bridge/
├── inbox/          ← 你往这里写任务（派活）
├── outbox/         ← 你在这里读结果
├── handoffs/       ← 干完了的信号（通常给你的 hook 看）
└── capabilities.json   ← 先读这个，它列出 DSH 现在会什么
```

一次完整派活，三步：

**第一步：读能力清单，挑一个权限档。**

```bash
cat ~/.dsh-bridge/capabilities.json
```

找到 `permissionPresets` 那一段，记住一个 `value`，比如 `workspace-write`。**这一项是必填的，没有默认值**——不写或写错，任务会被当场拒绝。

**第二步：把一个 JSON 写进 inbox。**

```bash
cat > ~/.dsh-bridge/inbox/my-task-001.json <<'JSON'
{
  "task_id": "my-task-001",
  "workspace": "/absolute/path/to/the/project",
  "prompt": "在这个项目里跑一遍测试，如果失败就修到全绿，然后告诉我改了什么。",
  "permission_preset": "workspace-write"
}
JSON
```

写完这个文件，**你这一轮就可以结束了**。文件名的 `my-task-001` 会成为任务编号，后面用它来取结果。

**第三步：等信号，然后读结果。**

当 `~/.dsh-bridge/handoffs/my-task-001.json` 出现时，去读 `~/.dsh-bridge/outbox/my-task-001.result.md`。

---

## 2. 时机：什么时候你会被叫醒

你的 hook 应该盯住 `~/.dsh-bridge/handoffs/` 这个目录。**新文件出现 = 有事发生**，然后拉起你，把任务编号和结果路径交给你。

信号只有两种，你只需要判断 `signal` 这一个字段：

| `signal` | 含义 | 你该做什么 |
|---|---|---|
| `done` | 这件事结束了（成功、失败、或被取消都算） | 读 `result.md`，**自己检查成果**，决定下一步 |
| `not_done` | 这件事等不到了：被人打断、或进程中断 | 读 `status.json` 看原因，然后决定重派还是先问人 |

**为什么只给两个值？** 因为你的程序只需要判断「该去读结果」还是「该先搞清楚为什么没结果」。具体原因写在文件里，你自己读。

`handoffs/<task_id>.json` 的内容长这样：

```json
{
  "task_id": "my-task-001",
  "signal": "done",
  "terminal_state": "completed",
  "result_path": "/Users/.../.dsh-bridge/outbox/my-task-001.result.md",
  "status_path": "/Users/.../.dsh-bridge/outbox/my-task-001.status.json",
  "session_id": "session-1a2b3c..."
}
```

**如果你来不及等信号**（比如你的 hook 还没配好），可以随时来问：

```bash
cat ~/.dsh-bridge/outbox/my-task-001.status.json
```

这个文件很小、随时可读，`state` 变成终态就说明干完了。但**不要高频轮询**——有信号机制，用信号。

---

## 3. 信封字段参考

`inbox/<task_id>.json` 就是一个 JSON 对象。字段如下：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `task_id` | **是** | 字符串 | 任务编号。会变成文件名，所以只允许字母、数字、点、连字符、下划线，且不能以连字符或点开头，最长 128 字符 |
| `workspace` | **是** | 字符串 | 工作目录，**必须是已存在的绝对路径** |
| `prompt` | **是** | 字符串 | 用自然语言写清楚要它做什么。这是唯一进入 DSH 的内容，写得多具体它就有多准 |
| `permission_preset` | **是** | 字符串 | 权限档。**可用值只能从 `capabilities.json` 的 `permissionPresets` 里挑**。不写或写错 = 任务被拒绝 |
| `agent_preset` | 否 | 字符串 | 角色预设 id，决定这个会话装载哪些能力。可用值见 `agentPresets` |
| `session_id` | 否 | 字符串 | 给一个已有会话 id，则在**那个会话里继续干活**，而不是新开一个 |
| `skills` | 否 | 字符串数组 | 点名要用的技能，例如 `["pdf"]`。可用值见 `skills` |
| `commands` | 否 | 字符串数组 | 先执行的斜杠命令，例如 `["/permission read-only"]`。可用值见 `commands` |

**三条容易踩的规则：**

- **`task_id` 永不复用。** 同一个编号第二次出现会被拒绝。想重做一遍，换一个新编号。
- **`workspace` 不会替你创建。** 目录必须已经存在。DSH 不会因为路径写错就去 mkdir。
- **权限档必填。** 这是刻意的设计：权限不该有隐藏默认。

---

## 4. 先读能力清单

`capabilities.json` 是 DSH 告诉你的「我现在会什么」。有四个分区，加一个版本号：

| 分区 | 是什么 | 你怎么用 |
|---|---|---|
| `skills` | 技能：DSH 内置的专业流程（处理 PDF、Office 文档、论文翻译等） | 需要时用 `skills` 字段点名；**不确定就别点名**，DSH 自己会判断 |
| `agentPresets` | 角色预设：决定这个会话装载哪些工具 | 需要特定能力时用 `agent_preset` |
| `commands` | 斜杠命令：能直接下的指令 | 需要时用 `commands` 字段 |
| `permissionPresets` | 权限档：沙箱范围 + 是否需要人批准 | `permission_preset` **只能从这里挑** |

每个分区都带 `revision` 字段。如果你把它缓存起来了，**下次发现 `revision` 变了就重读一遍**——DSH 可能装了新插件或新技能。

```bash
python3 - <<'PY'
import json, pathlib
caps = json.loads(pathlib.Path.home().joinpath('.dsh-bridge/capabilities.json').read_text())
print('revision:', caps['revision'])
print('permission presets:')
for p in caps['permissionPresets']:
    print('  ', p['value'], '—', (p['description'] or p['name'])[:70])
print('skills:', ', '.join(s['name'] for s in caps['skills']) or '(none)')
PY
```

**关于 `permission_preset` 怎么选**：它是你能表达的最重的一个意思。选保守的档位，DSH 在需要做危险动作时会**弹窗等人批准**；选最宽的档位，它就直接做了、不打扰人。拿不准就用保守档——被拒绝不是失败，人点一下就好了；反过来越权是没法撤销的。

---

## 5. 怎么知道干完了

两个地方告诉你：

**① `handoffs/<task_id>.json`（首选，给你的 hook）** —— 见第 2 节。

**② `outbox/<task_id>.status.json`（想立刻知道当前状态时读它）**

```json
{
  "task_id": "my-task-001",
  "state": "running",
  "detail": null,
  "since": "2026-01-01T12:00:00.000Z",
  "session_id": "session-1a2b3c...",
  "workspace": "/absolute/path/to/the/project"
}
```

`state` 的全部取值：

| `state` | 含义 |
|---|---|
| `submitted` | 已收到，正在准备 |
| `running` | 正在干活 |
| **`completed`** | 干完了 |
| **`failed`** | 出错了（模型报错、超 token、或没跑起来） |
| **`cancelled`** | 被中断（不是人按的，例如进程退出） |
| **`interrupted_by_user`** | **人插手了**：在界面里按了停止，或者自己插话改了要求 |
| **`waiting_for_approval`** | 卡住了，等人在界面里点批准 |
| **`orphaned`** | DSH 进程在任务跑完之前结束了，这个任务不会再有结果了 |

**加粗的那几个是终态**，看到它们就是「结果已定」。

**关于 `interrupted_by_user`，这条很重要：** 它代表**人为干预**。这说明**原任务描述可能已经不作数了**——人可能改了口径、缩小了范围、或者干脆不要了。遇到它，读一遍 `result.md` 看干到哪了，再决定是重派还是先问人。**不要把它当成「正常完成」。**

**关于 `waiting_for_approval`：** 这不是终态。它说明 DSH 卡在一个等人批准的操作上，而**批准只能由人在界面里做，你无权代批**。信号是 `not_done`。你能做的是把这件事报告给人。

---

## 6. 读结果

`outbox/<task_id>.result.md` 是给你的主要产物。它包含：

| 段落 | 内容 |
|---|---|
| 头部 | 任务编号、终态、原因、工作目录、DSH 会话 id、你请求的权限档 |
| `## Result` | **DSH 最后给出的结论**（它自己写的总结） |
| `## Tools called` | 它调用了哪些工具、成功还是失败 |
| `## Verification` | 固定的一句话，见下 |

**`result.md` 不包含什么，比包含什么更重要：**

> 它**只记录这个会话做了什么**。它不跑测试、不跑 lint、不做任何检查——**验收是你的职责。**

所以读完 `result.md` 之后，你应该用**你自己的**工具去确认：diff 看了吗？测试跑了吗？改的东西对不对？DSH 说「已修复」，不代表真的修好了。

**你还能自己去看更多东西**，`result.md` 只是一份摘要：

- 想看完整对话：`session_id` 就是 DSH 的会话 id，人可以在 DSH 界面里打开它看全过程。
- 想看文件改动：直接看你自己的 git，或读 `workspace` 里的文件——你和工作区在同一台机器上。

---

## 7. 边界与禁止

这八条是硬规则，违反任何一条都会让整件事变糟：

| # | 禁止 / 必须 | 为什么 |
|---|---|---|
| 1 | **不要复用 `task_id`** | 编号是结果文件的唯一名字。复用了就会把一份历史结果覆盖掉，而历史是只增不改的账本 |
| 2 | **不要对同一个 `workspace` 并发派活** | DSH 不会替你排队。两个任务同时改一个目录会互相踩，而且**人也可能在界面里插手**，冲突源有两个 |
| 3 | **不要自己往 `outbox/` 写文件** | 那是 DSH 的产出区。你写的会被当成它的结果，但没人知道那是你写的 |
| 4 | **不要动 `.claimed` 和 `.rejected` 文件** | `.claimed` 是「已领取」的原子标记，删了会让任务重复执行；`.rejected` 是给你看的错误说明 |
| 5 | **不要改 `capabilities.json`** | 那是 DSH 自己发布的。你改了只会让自己读到假信息 |
| 6 | **不要指望有「取消」接口** | 取消是**人的权力**，只有他能从界面里叫停。你没有这个手段，也不需要——把活派出去就该放手 |
| 7 | **不要把 `waiting_for_approval` 当失败** | 它在等人，不是出错了。重派只会制造第二个卡住的任务 |
| 8 | **没有 `handoffs/` 信号时不要反复轮询** | 信号就是为省这个而存在的。想主动看一眼就读 `status.json`，别开循环 |

**还有一条关于文件系统的：** DSH 干活的目录里可能同时有你自己的改动。你在派活期间**不要**去写那些文件——等信号来了再动。这是「不干涉」的具体含义。

---

## 8. 派活被拒绝了怎么办

如果信封没通过检查，你会看到一个说明文件：

```
~/.dsh-bridge/inbox/<task_id>.rejected
```

内容长这样：

```json
{
  "task_id": "my-task-001",
  "rejected_at": "2026-01-01T12:00:00.000Z",
  "reason": "unknown-permission-preset",
  "field": "permission_preset",
  "message": "unknown permission preset \"foo\"; available values are listed in capabilities.json"
}
```

**被拒绝的任务不会建立任何会话，也不会消耗任何算力**——所以它很便宜，改对了重投即可。

逐条对照表：

| `reason` | 意思 | 你该怎么做 |
|---|---|---|
| `bad-json` | 文件不是合法 JSON | 检查引号、逗号、括号 |
| `not-an-object` | 顶层不是 JSON 对象 | 整个文件必须是一个 `{...}` |
| `missing-required-field` | 少了一个必填字段，看 `field` | 补上。**缺 `permission_preset` 最常见** |
| `wrong-field-type` | 字段类型不对，或写了不认识的字段名 | 看 `message` 里列出的合法字段名 |
| `invalid-task-id` | 编号含非法字符，或太长 | 只用字母、数字、点、连字符、下划线 |
| `task-id-already-used` | 这个编号有结果了 | **换一个新编号**，不要改旧结果 |
| `workspace-not-absolute` | 工作目录不是绝对路径 | 写成 `/Users/...` 这样的绝对路径 |
| `workspace-not-found` | 目录不存在 | 先确认目录在，或自己创建它（**DSH 不会替你建**） |
| `unknown-permission-preset` | 权限档名字不对 | 去 `capabilities.json` 的 `permissionPresets` 重新挑 |
| `unknown-agent-preset` | 角色预设不存在 | 去 `agentPresets` 里挑 |
| `unknown-skill` | 技能名不存在 | 去 `skills` 里挑，或者**干脆去掉这个字段**让 DSH 自己判断 |
| `unknown-command` | 命令名不存在 | 去 `commands` 里挑 |
| `session-not-found` | `session_id` 指向的会话不存在 | **去掉 `session_id` 字段**，让它新开一个会话 |

**处理原则**：改完**用一个新的 `task_id`** 重新投递。不要尝试复用旧编号——那会被拒。

---

## 9. 完整示例

### 例 1：派一个全新的活

先看清楚 DSH 有哪些权限档：

```bash
python3 - <<'PY'
import json, pathlib
caps = json.loads(pathlib.Path.home().joinpath('.dsh-bridge/capabilities.json').read_text())
for p in caps['permissionPresets']:
    print(p['value'], '—', (p['description'] or '')[:80])
PY
```

派活：

```bash
TASK=refactor-parser-001
cat > ~/.dsh-bridge/inbox/$TASK.json <<JSON
{
  "task_id": "$TASK",
  "workspace": "/Users/example/projects/myapp",
  "prompt": "把 src/parser/ 下的分词逻辑重构一下：把 tokenizer 拆成独立模块，保持现有测试全绿。完成后用三句话总结你改了什么、为什么这样拆。",
  "permission_preset": "workspace-write"
}
JSON
echo "已派发 $TASK"
```

**然后你就该放手了。** 不要在这里 sleep 或轮询。等你的 hook 通知你，或者下一次被唤醒时来查：

```bash
TASK=refactor-parser-001
python3 - <<PY
import json, pathlib
p = pathlib.Path.home()/'.dsh-bridge/outbox/$TASK.status.json'
if p.exists():
    s = json.loads(p.read_text())
    print('state:', s['state'], '| detail:', s.get('detail'))
    if s['state'] in ('completed','failed','cancelled','interrupted_by_user','orphaned'):
        r = pathlib.Path.home()/f'.dsh-bridge/outbox/$TASK.result.md'
        print('--- result ---')
        print(r.read_text() if r.exists() else '(no result file)')
else:
    print('尚未被处理')
PY
```

拿到 `completed` 之后，**记得自己去验收**：看 diff、跑测试。

### 例 2：在同一个会话里继续干活

假设第一个任务建立了会话 `session-1a2b3c...`，你想让它接着改：

```bash
TASK=refactor-parser-002
cat > ~/.dsh-bridge/inbox/$TASK.json <<JSON
{
  "task_id": "$TASK",
  "workspace": "/Users/example/projects/myapp",
  "session_id": "session-1a2b3c...",
  "prompt": "接着刚才的改动，把新模块补上单元测试，覆盖空输入和超长输入两种边界。",
  "permission_preset": "workspace-write"
}
JSON
```

注意这里是**新的 `task_id`**、**沿用旧的 `session_id`**。会话 id 在 `outbox/<上一个 task_id>.receipt.json` 里能找到，也在每个 `status.json` 的 `session_id` 字段里。

**为什么续会话有价值**：DSH 会话记得上下文——它知道自己刚才改了哪些文件、为什么那样改。新开会话则要从头了解情况。

---

## 10. 一页速查

```
派活   写 ~/.dsh-bridge/inbox/<task_id>.json
        必填: task_id, workspace, prompt, permission_preset
        permission_preset 必须从 capabilities.json 里挑

清单   读 ~/.dsh-bridge/capabilities.json
        skills / agentPresets / commands / permissionPresets
        注意 revision，变了就重读

完成   信号 ~/.dsh-bridge/handoffs/<task_id>.json 的 signal
        done = 去读结果    not_done = 先看为什么

结果   读 ~/.dsh-bridge/outbox/<task_id>.result.md
        ★ 然后自己验收 —— 它不替你跑测试

拒绝   读 ~/.dsh-bridge/inbox/<task_id>.rejected
        改对了用【新 task_id】重投

铁律   编号不复用 · 同目录不并发 · 不写 outbox · 不删 .claimed
        取消只有人能按 · 权限档必填 · 派完就放手
```
