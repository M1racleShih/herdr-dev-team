# Dispatch and recover a local run

Use Python 3 on a POSIX host. All three interactive sessions share the same local Herdr Core and filesystem. The helper uses `hc send --json` with a literal terminal ID and returns after the delivery attempt. Legacy runs keep a private state file. Two-pack runs share a project SQLite database. Herdr messages wake the receiving agent.

## Run a two-pack

Use a dedicated local target branch checked out in its own worktree. Keep the project store in an owner-only persistent directory outside the worktree. All three local sessions need access. Choose the installed provider/model for this run. Start separate interactive Coder and Cleaner terminals in the same worktree. For a visible tab with just the two worker panes, launch with `hc run --bg --json --no-parent` and the space/tab labels described below. The CLI addresses its Delivery Lead explicitly. Confirm both terminal IDs, their readiness and the clean branch before starting.

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

### Submit evidence

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

### Inspect and recover

`two RUN_ID resume` returns the full run as JSON without claiming or sending work. `two RUN_ID recover` sends a pending assignment that has no attempt. It leaves a recorded delivery alone. An attempt with no confirmed outcome requires inspection of the original terminal and assignment number. Once confirmed, run `two RUN_ID reconcile ROLE ROUND ATTEMPT delivered --evidence TEXT`. Use `retry` only with positive evidence that the original was never delivered and cannot still arrive. An accepted matching receipt resolves an uncertain delivery.

Register an updated card with `two RUN_ID revise --card CARD`, adding `--checks CHECKS` when checks change. Before start or after a pause or blocker, supply `--approve-evidence TEXT` to activate a revision and retain the previous history. Record actual user approval when acceptance criteria change. Re-run affected checks on the new version.

## Prepare a legacy state.json run

Choose a private, persistent location outside the project's published files. Record its path where the next Leader can find it. Create a unique directory per task. Set `HELPER` to the absolute path of this skill's `scripts/handoff.py` and `PROJECT` to the actual working directory. For example:

```sh
RUN_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-dev-team"
mkdir -p "$RUN_ROOT"
RUN=$(mktemp -d "$RUN_ROOT/run-XXXXXXXX")
NAME=$(basename "$RUN" | LC_ALL=C tr '[:upper:]' '[:lower:]')
```

## Launch the workers

Run these commands from the actual Leader terminal. Use the confirmed role settings and the Ponytail path from [role setup](roles.md). These functions start one interactive Pi terminal per call and attach it to the Leader for Core notifications.

```sh
launch_coder() {
  hc run --bg --json --outlive-parent --parent "${HC_TERMINAL_ID:?Run from the Leader terminal}" \
    --name "$NAME-code" --cwd "$PROJECT" "$@" pi \
    --provider "$PROVIDER" --model gpt-6.2-sol --thinking medium --skill "$PONYTAIL"
}
launch_reviewer() {
  hc run --bg --json --outlive-parent --parent "${HC_TERMINAL_ID:?Run from the Leader terminal}" \
    --name "$NAME-review" --cwd "$PROJECT" "$@" pi \
    --provider "$PROVIDER" --model gpt-6.2-sol --thinking high
}
```

Use only the confirmed launch mode. Save each command's returned `address` as `CODER_ID` or `REVIEWER_ID`. If a launch response is lost, look up the original worker by its unique name before taking any further action.

### Visible in Herdr App

1. Run `herdr-app ls`. In `spaces[].tabs[].terminals[]`, find the entry whose `terminal` equals the Leader's `HC_TERMINAL_ID`. Set `SPACE_ID` to its enclosing space ID. If the App is unavailable or the Leader cannot be located, ask the user whether to open the Leader in the App or use Background.
2. Start the Coder in a new tab in that space.

   ```sh
   launch_coder --label "herdr.space=$SPACE_ID"
   ```

3. In `herdr-app ls`, find the tab in that space containing `CODER_ID` as a terminal. Set `DEV_TAB` to that tab's ID. If projection is delayed, inspect the layout again before proceeding. Rename that tab and launch the Reviewer into it.

   ```sh
   herdr-app do tab.rename --id "$DEV_TAB" --name Development
   launch_reviewer --label "herdr.space=$SPACE_ID" --label "herdr.tab=$DEV_TAB"
   ```

4. Verify the two terminal IDs occupy distinct panes in `Development`, Coder on the left and Reviewer on the right. The Leader keeps its original tab and focus. The launch labels create both panes. Reuse them for handoffs and fixes. To inspect a long diff, the operator can run `herdr-app do pane.zoom --pane PANE_ID` to zoom and run it again to restore the split.

### Background in Core

Run `launch_coder` and `launch_reviewer` with no labels. Both workers receive messages by Core terminal ID. This mode needs only Core.

## Dispatch the first assignment

Check worker readiness during setup. The Reviewer receives its first assignment after the Coder delivers. In `$RUN/config.md`, record the task, permissions, confirmed role settings, layout, both worker terminal IDs and acceptance criteria. Write the Coder's owned files, checks, Ponytail full requirement and boundaries in `$RUN/code-1.md`. Initialize the helper with the saved Core terminal IDs.

```sh
python3 "$HELPER" "$RUN" init --confirm --project "$PROJECT" --config "$RUN/config.md" \
  --coder "$CODER_ID" --reviewer "$REVIEWER_ID" --rounds 5
python3 "$HELPER" "$RUN" start --brief "$RUN/code-1.md"
```

Check the returned delivery status. After delivery, end the Leader's turn. The assignment contains the exact worker command, with the absolute paths and role/round already filled in.

## Save a result and advance once

Each role writes its result in a UTF-8 file and executes the report command **from its own assigned terminal**. The helper saves the result text in `state.json` before sending a notice. Typical commands for the first round:

```sh
# Coder after implementing and checking
python3 "$HELPER" "$RUN" report coder 1 done --result "$RUN/code-1-result.md"

# Leader on receipt: read the saved result, inspect the actual work, write the review brief
python3 "$HELPER" "$RUN" resume
python3 "$HELPER" "$RUN" advance coder 1 --brief "$RUN/review-1.md"

# Reviewer after independent review; use fail for blocking findings
python3 "$HELPER" "$RUN" report reviewer 1 pass --result "$RUN/review-1-result.md"

# Leader after checking a passing review
python3 "$HELPER" "$RUN" advance reviewer 1
```

Advance the role and round named by the verified receipt. A duplicate notification or repeated advance reuses the recorded decision. Reuse the original brief if repeating that command. A delayed receipt cannot replace the result for another assignment.

On review `fail` with budget left, run `advance reviewer 1 --brief "$RUN/code-2.md"`. The Coder reports on round 2. Advance that receipt with a round-2 review brief. On a final-round failure use `advance reviewer ROUND` without a brief, record the open issues, and ask the user how to proceed. Both workers can report `blocked`; consuming that receipt stops dispatch. A passing review transitions to `qa-ready`. The Leader writes the final QA verdict and evidence in `$RUN/qa.md`, so reopening the run also reveals whether QA was completed. If a closed run needs an authorized continuation, link the previous run and findings in a new configuration, and use only the remaining or newly authorized review budget.

## Recover when a notice or send is lost

- **Saved receipt, missing notification.** On reopening the Leader, run `resume` once. Read the current assignment and its saved receipt, verify the evidence and advance from its role and round. A worker may repeat the same report while its assignment is current. This can send another notice without altering the saved result.
- **New Leader terminal.** If possible, reopen the original interactive Leader terminal to keep both kinds of notification. If it was retired, use `takeover --from OLD_LEADER_ID` in the new interactive Leader terminal on the same Core, then `resume`. Explicit worker receipts now target the new address. Core fixes each worker's parent at launch, so its automatic blocked, error and exit notes still target the old address. Inspect each inherited terminal once with `hc get --json WORKER_ID` and reconcile it with the saved receipt. Tell the operator that a later inherited-worker failure without a receipt needs their alert: reopen the run and inspect that worker. For continued unattended failure notifications, resolve outstanding assignments before starting a continuation with workers parented to the new Leader. Keep completed evidence and remaining review budget in that continuation.
- **Dispatch shows intent or ambiguous delivery.** Inspect the recorded target once. Match the assignment ID and attempt number. If you positively identify that prompt, record the observation with `reconcile reviewer 1 1 delivered --evidence '...'`. To retry, require positive evidence that the original send failed before delivery and can no longer arrive. Record that evidence with `reconcile reviewer 1 1 retry --evidence '...'`. Substitute the actual role, round and attempt. A timeout, incomplete history or an exit code alone leaves the outcome unresolved.
- **Terminal exits or authentication blocks.** Inspect the receipt and original terminal. Ask for human intervention if login or permission input is needed. Worker terminal IDs stay fixed for the run. If a worker was lost permanently, preserve the original record and establish the remaining scope before continuing with a replacement.

`resume` inspects the run once. No watcher or timer advances an offline Leader. The user can reopen the run, verify the saved work and continue. After final acceptance, close only the worker terminals created for that run when they are no longer needed.
