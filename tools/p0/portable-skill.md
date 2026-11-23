---
name: local-code-guard
description: Apply global local quality gates when creating, modifying, refactoring or generating code in any project. Fix executable-check failures before completion; ordinary prose does not require code linting.
---

# Local code guard

- The executable Stop check is `code-guard/closeout.py`. Confirm the trusted Stop hook runs in a real Codex task before claiming activation.
- Register each actual tool `workdir` before editing. Follow [registration](../../code-guard/README.md#register-the-worktree) and keep writes within the declared roots. Read-only tasks need no baseline.
- Rule values live in `scan.mjs`, `python_metrics.py` and `policies.json` beside the gate. Read the relevant implementation as needed; do not copy numeric limits into instructions.
- Check this task's new or worsened diagnostics against each registered pre-edit baseline. Leave other chats and unchanged history out. Preserve ambiguous changes and report them as unverified.
- Follow [verification](../../code-guard/README.md#verify-changes). Stop must accept the current record, including committed and delegated changes in the main task's registered worktrees.
- If a write lacks a required baseline, preserve the edits and report verification incomplete.
- For a registered clean fast-forward pull, follow [closeout results](../../code-guard/README.md#closeout-results). Report incoming diagnostics or scan failure. The sync result does not certify the incoming code.
- Follow [same-chat recovery](../../code-guard/README.md#recover-a-check) for unresolved checks.
- Baseline evidence persists; never reset state, suppress checks or raise limits to pass. Checker maintenance requires explicit authorization and regression validation.
- Use the [historical audit](../../code-guard/README.md#historical-audit) only when explicitly requested and a preserved audit baseline exists. A lint pass does not establish business correctness.
- JS/TS, JSX/TSX and Python have adapters. Python uses AST length and branch checks rather than ESLint's identical metric. Recognized unsupported source types block when changed; unknown extensions are not certified.
- Project retired-module contracts belong in `policies.json` under `retiredModules`, with exact root, old module and replacement. Static require/import references are supported; computed imports and mere code age are not proof of retirement.
- Source exclusions follow the [scanner](../../code-guard/scan.mjs). Report missing checkers or trust as verification blockers. Respect user interruptions. Report only checks actually executed.

For persistent instruction changes, use [instruction maintenance](../instruction-maintenance/SKILL.md).
