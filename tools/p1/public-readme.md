# Agent Engineering Toolkit

版本 `2026.11.23-p1`。从 [安装与接入指南](guides/Codex-P1-安装与接入.md)开始，可选择手工 CLI、Codex、Claude Code 或 Gemini CLI。

- [安装程序](install.py)提供预检、变更预览、安装、验证与回滚。
- [共享检查 CLI](implementation/toolkit/guard.py)供 Agent 和 CI 使用。
- [GitHub Actions 模板](examples/github-actions.yml)从基准提交读取检查器，检查候选 Git 对象。
- [P0 执行与恢复说明](implementation/code-guard/README.md)说明工作区基线和收尾证据。

接收方确认原生 Hook 加载后才能验收自动触发。独立指令审查可选，使用接收方自己的 Codex CLI 登录。
