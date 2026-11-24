# Operate a two-pack

Use a dedicated local target branch checked out in its own worktree. Keep `STORE` in an owner-only persistent directory outside the worktree, accessible to the Delivery Lead and both workers. Use the [worker launch instructions](handoffs.md#launch-the-workers) to prepare separate Coder and Cleaner terminals. Confirm both terminal IDs, their readiness and the clean branch before starting. Set `HELPER` to the absolute path of the installed skill's `scripts/handoff.py`.

Write a UTF-8 Markdown card with goal, scope, pack, baseline and observable acceptance cases. Supply a checks JSON file such as the following for a Python target. Include any additional required check in its owner's list. Pin the Cleaner mutation command to the same command as the original suite.

```json
{
  "coder": [{"name": "unit", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0}],
  "cleaner": [
    {"name": "unit", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "mutation", "command": "python3 -m unittest discover -s tests -v", "exit_code": 1}
  ]
}
```

```sh
python3 "$HELPER" "$STORE" two init --project "$WORKTREE" --card "$CARD" --checks "$CHECKS" \
  --coder "$CODER_ID" --cleaner "$CLEANER_ID"
python3 "$HELPER" "$STORE" two "$RUN_ID" resume
python3 "$HELPER" "$STORE" two "$RUN_ID" start
```

Copy the run ID from init. Each init creates a distinct history in the same project database. Start from the recorded Lead terminal. The assignment carries the card and its version, the [Coder](two-coder.md) or [Cleaner](two-cleaner.md) instructions, the exact input commit, the predecessor's receipt and the report command.

## Submit evidence

Run checks from the assigned worktree root. Write combined stdout/stderr to unique paths inside the private store and record the actual exit status. Commit changed files before the final check. For check-only work, use the input commit. Use full Git commit IDs throughout. A Coder receipt has this shape (replace placeholders with observed values).

```json
{
  "input_commit": "<assigned commit>",
  "commit": "<checked commit>",
  "issues": "",
  "checks": [{
    "name": "unit",
    "command": "python3 -m unittest discover -s tests -v",
    "commit": "<checked commit>",
    "exit_code": 0,
    "log": "run-123/coder-1.log"
  }]
}
```

Cleaner pass includes an independently produced unit row and a mutation row with the same command, base commit, observed nonzero status, mutant log, `patch` (a git diff path in the store), `test` (qualified unittest ID), and `assertion` (text of the intended AssertionError). The patch must apply to the checked commit. Include each additional pinned check. The script snapshots the submitted files in the private store. Optional `artifacts` entries identify other proof with `path` and `commit`.

Report from the assigned terminal with `two RUN_ID report ROLE ROUND STATUS --result FILE`. On a Cleaner `fail`, include the failing pinned check and a `failure` object with `check`, `input`, `expected` and `actual`. Coder receives that version and finding. Either role can report `blocked` or `paused` with `failure.reason` and available checks. The Lead resolves a blocker within scope or obtains the user's decision on changed requirements. Identical accepted reports reuse the existing decision and dispatch. Keep the same result file for retries.

## Inspect and recover

`two RUN_ID resume` returns the full run as JSON without claiming or sending work. `two RUN_ID recover` sends a pending assignment that has no attempt. It leaves a recorded delivery alone. An attempt with no confirmed outcome requires inspection of the original terminal and assignment number. Once confirmed, run `two RUN_ID reconcile ROLE ROUND ATTEMPT delivered --evidence TEXT`. Use `retry` only with positive evidence that the original was never delivered and cannot still arrive. An accepted matching receipt resolves an uncertain delivery.

Register an updated card with `two RUN_ID revise --card CARD`, adding `--checks CHECKS` when checks change. Before start or after a pause or blocker, supply `--approve-evidence TEXT` to activate a revision and retain the previous history. Record actual user approval when acceptance criteria change. Re-run affected checks on the new version.
