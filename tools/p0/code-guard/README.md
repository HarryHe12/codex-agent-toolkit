# Code guard closeout

The installed `hooks.template.json` points the trusted Codex `Stop` event at `code-guard/closeout.py stop`. Install and trust it according to the migration guide, then verify the event in a real Codex task. Read-only tasks do not need a baseline. The previous automatic `PostToolUse` scan moves to the explicit `verify` command, and `SubagentStop` is no longer registered. The main task owns verification of its registered worktrees, including work it delegated there.

## Register the worktree

From each worktree you intend to edit, run:

```sh
python3 /path/to/code-guard/closeout.py begin
```

Use `begin --standalone` for a specific non-Git project. `begin` prints the registered path.

Use the actual registered worktree as the tool `workdir`. Before touching another worktree, register it independently. Keep writes within their declared roots and avoid writing across roots through absolute paths or a shell `cd`.

The pre-edit fingerprint preserves dirty files that were already present. A directory snapshot cannot prove which process wrote a file after registration. Use separate worktrees for concurrent writes when ownership is unclear. Report ambiguous edits as unverified and preserve them for the owning task.

## Verify changes

After editing, run the applicable project verification:

```sh
python3 /path/to/code-guard/closeout.py verify --review 'Behavior checked and relevant documentation' -- npm test
```

Use the project's actual verification command in place of `npm test`. For documentation-only changes, use `verify --docs-only --review 'Documentation and links checked'`. Add `--standalone` when verifying a registered non-Git project. `verify` records the reviewed file state after checks pass. `Stop` rejects missing or stale evidence, including for committed or delegated changes in the registered worktree.

## Closeout results

For a registered clean fast-forward pull, the gate reports incoming diagnostics if the scan completes, and reports the diagnostic failure if the scan stops early. The sync result does not certify the incoming code. If `Stop` identifies writes without a pre-edit baseline, report verification as incomplete and preserve the changes.

## Recover a check

Resolve recorded failures in the original chat and preserve its pre-edit baseline. If a scanner dependency is missing, follow the printed installation steps and resume the same command. A scan timeout identifies the worktree and step. `md_write.py review` reports its own progress on stderr and returns the draft's review status separately.

After a scan failure, recovery is complete when `verify` records the current changed files and `Stop` accepts that record. If review returns `revise`, apply the cited feedback to the draft and rerun review. If it returns `unavailable`, fix the diagnosed problem and use `review --retry` for the same draft. Only `pass` permits the approved draft to be applied.

## Historical audit

Run `closeout.py audit` only for an explicitly requested historical audit with a preserved audit baseline. A code guard result does not establish business correctness.
