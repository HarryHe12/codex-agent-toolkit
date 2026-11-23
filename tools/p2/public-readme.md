# Agent Engineering Toolkit

版本 `2026.11.23-p2`。安装、主机接入和 CI 沿用 [P1 安装指南](guides/Codex-P1-安装与接入.md)。项目可以选择 [P2 行为验收](guides/Codex-P2-行为验收.md)，用可执行文档示例和代表性错误核对验证结果。

- [安装程序](install.py)提供预检、安装、验证和回滚。
- [行为验收配置示例](examples/acceptance.json)展示如何描述业务范围、实际入口和独立断言。
- [GitHub Actions 模板](examples/github-actions.yml)检查候选 Git 源码对象。

真实宿主仍需要验证 Hook 的信任和触发。正式指令的独立审查与项目行为验收分别执行。
