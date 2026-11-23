# Agent Engineering Toolkit

版本 `2026.11.23-p0`。包含本地代码门禁、独立指令审查、项目运行验证、重复错误预防和 Skill 评估。

- [迁移和验收指南](guides/Codex-P0-异机安装与验收.md)
- [本地代码门禁](implementation/skills/local-code-guard/SKILL.md)
- [指令维护](implementation/skills/instruction-maintenance/SKILL.md)
- [项目运行验证](implementation/skills/project-verification/SKILL.md)
- [重复错误预防](implementation/skills/prevent-repeat-errors/SKILL.md)
- [Skill 效果评估](implementation/skills/evaluate-agent-skills/SKILL.md)

安装使用 `implementation/` 中的源码。默认仅注册主助手的 `Stop`。安装后在接收环境确认信任并验收实际触发。独立审查使用接收方已有的 Codex CLI 登录。
