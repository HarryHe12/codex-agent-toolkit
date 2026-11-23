# Codex P0 异机安装与验收

版本 `2026.11.23-p0`。安装使用 `Codex-Agent-Toolkit.zip` 内的 `implementation/`。按 `PACKAGE-MANIFEST.json` 的相对路径与 SHA-256 核对来件。公开仓库安装来源以它实际发布的版本为准。

## 安装及升级

1. 核对机器、系统用户、目标工作区和实际 `CODEX_HOME`。未设置时使用 `~/.codex`。依据包清单核对来件。按来源、事件和命令识别已有 Hook，再决定合并范围。
2. 备份本次将合并或替换的 `hooks.json`、组件源码和相关 Skill。保留其他 Hook 与用户设置。登录、运行状态和收据由接收方在自己的环境维护。
3. 把包内 `implementation/code-guard/`、`implementation/md-governance/`、`implementation/skills/` 的选用内容分别放入 `CODEX_HOME` 下的同名目录。实测环境为 macOS、Node 24 和 Python 3.9。独立审查另需接收方自己的 Codex CLI 登录。
4. 在选定的 `CODEX_HOME` 内按下方命令安装依赖并运行两个测试套件。以安装和两个测试命令均成功退出作为这一阶段的结果。
5. 依据 `implementation/hooks.template.json` 合并。逐项检查 `SessionStart`、`PreToolUse`、`PostToolUse`、`SubagentStop`，移除已确认的旧 Toolkit 项，保留其他 Hook。复用已有同版主助手 `Stop`，最终核对 Toolkit 只有一个 `closeout.py stop`。
6. 复制后按包清单核对组件哈希。记录合并后 `hooks.json` 的绝对路径与文件哈希，另记录 Hook 命令原文及解析出的解释器和脚本路径。确认信任后，用接收方日常启动的 Codex 会话验收触发。

```sh
cd "${CODEX_HOME:-$HOME/.codex}/code-guard"
npm ci --omit=optional --ignore-scripts --no-audit --no-fund
CODEX_GUARD_MD_HOME="$(pwd)/../md-governance" python3 -m unittest discover -v
cd ../md-governance
python3 -m unittest discover -v
```

Hook 模板决定注册配置，组件 README 解释运行方式，Skill 提供可复用操作。旧版自动 `PostToolUse` 扫描改为显式 `verify`。主助手的 `Stop` 核对本任务登记工作区内的改动，包括提交及委派写入。默认只注册 `Stop`。

## 操作说明

运行语义与命令见安装后的 `code-guard/README.md`，复用入口见 `skills/local-code-guard/SKILL.md`。首次写入前登记实际工作区，证据过期后重跑验证。归属不清或缺基线的改动按未验证保留。

长期指令的草稿、独立审查和受控写入见 `skills/instruction-maintenance/SKILL.md`。审查诊断和重试见 `md-governance/md_write.py`。

## 激活验收

这些验收用于获授权的安装或升级。用合成 Git 仓库和受信任的实际 Codex Hook 验证。

- 在已有脏文件的合成仓库查看文件或 `git status` 后结束。Toolkit 只触发 `Stop`，直接结束且不启动源码扫描。
- 登记后在合成 JS 文件使用 `new Buffer(1)` 并执行 `verify`。观察废弃接口诊断及 `Stop` 拒绝缺少记录的收尾。
- 修复并验证成功后再修改受检文件。触发 `Stop` 时拒绝过期证据，重跑当前验证后放行。
- 写入但漏登记，且 Codex 日志能识别该写入。报告未验证并保留文件。
- 在合成远端提交含语法错误的 Python 文件。将已登记的干净工作区快进到该提交后，观察诊断和 `Stop` 放行。另一次让扫描不可用，观察扫描失败报告。同步放行只表明拉取任务可结束。
- 在隔离安装里暂时移除 ESLint。收到安装命令并补齐后，在原会话重跑验证并放行。

临时运行结果和旧版本记录均供核对。分别记录接收方的安装、信任、覆盖目录、实际触发与未执行项。
