# Agent Toolkit P1 安装与接入

`2026.11.23-p1` 面向 Unix 环境。安装使用 Python 3.9+ 和 Node 24，自带带哈希清单的源码包、依赖锁和合成验收。登录与工作区数据留在接收环境。

## 安装、验证与回滚

解压 `Codex-Agent-Toolkit.zip` 后，从解压目录执行。以下以 Codex 为例。

```sh
python3 install.py doctor --host codex
python3 install.py plan --host codex
python3 install.py install --host codex
python3 install.py verify --host codex
```

`doctor` 和 `plan` 检查包清单、Node、npm、目标目录和现有配置，输出会涉及的事件及路径。`install` 使用锁文件装依赖，在通过合成检查后切换配置。相同版本可重复运行。中断后重跑原命令。使用缓存时可带 `--offline`。输出的 `guard` 和 `skills` 是安装后绝对路径。

默认安装目录为 `~/.agent-toolkit`。用 `--home` 换目录，用 `--config` 指定宿主设置文件，用 `--node` 和 `--npm` 指定解释器。已有 Toolkit P0 注册项时，先从计划和设置中核实命令，再用 `--replace-command` 指明要替换的完整命令。其他 Hook 保留。

用 `python3 install.py rollback --host codex` 恢复上一次配置改动。若安装后有人改过该配置，回滚会保留这些编辑并报告冲突。版本目录留作复验和恢复。使用相同 `--home` 和 `--config` 才能定位原记录。

## Agent 接入

| 宿主 | 配置与触发 |
| --- | --- |
| Codex | `--host codex` 合并 `hooks.json`，只注册主助手 `Stop` |
| Claude Code | `--host claude` 合并 `settings.json`。`SessionStart` 提供当前任务命令，`Stop` 核对证据 |
| Gemini CLI | `--host gemini` 合并 `settings.json`。`SessionStart` 提供当前任务命令，`AfterAgent` 核对证据 |
| 其他 Agent | `--host manual` 安装共享 CLI，不写宿主配置 |

Claude Code 和 Gemini CLI 的启动通知只提供会话命令。实际编辑前才登记。开启宿主相应的 Hook 功能并确认信任后，从真实会话复验只读、已登记改动缺证据、补齐证据后放行。`install.py verify` 验证的是安装文件、合成检查及注册配置。独立长期指令审查仍使用接收方可选的 Codex CLI 登录。

使用安装输出的 `task_command` 作为入口，让本任务的 `begin` 和 `verify` 都使用该安装。手工使用时在同一个工作区传同一个任务 ID。Codex CLI 会话也可沿用它提供的会话变量。Codex 只注册收尾 Hook，开始任务时让 Agent 使用安装输出的命令。

```sh
python3 /path/to/guard.py --session task-1 begin
python3 /path/to/guard.py --session task-1 verify --review '核对了业务结果' -- npm test
```

使用该项目真正的验证命令。现有工作区基线、当前文件证据、审查诊断分别由组件运行说明和相应 Skill 描述。

## 接入 CI

先把经过核实的 P1 ZIP 放入目标仓库的 `vendor/agent-toolkit-p1.zip`，令基准分支可以读取它，再将包中的 `examples/github-actions.yml` 放入目标工作流目录。工作流使用只读权限，只从基准提交加载检查器，候选提交只作为 Git 数据读取。需要在宿主仓库要求 `code-guard` 通过并保护工作流和基准包更新的审查路径。

```sh
python3 /path/to/guard.py check --repo /path/to/repo --base BASE_SHA --head HEAD_SHA
```

退出 0 表示通过，或本次没有需检查的源文件，输出 JSON 区分两者。退出 1 表示新增或加重的代码诊断。退出 2 表示验证不可用。检查基准与候选提交之间的源文件差异，不运行候选项目代码。独立指令审查和项目业务验证仍各自验收。
