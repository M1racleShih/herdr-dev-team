# Dispatch and recover a local run

Use Python 3 on a POSIX host. All three interactive sessions share the same local Herdr Core and filesystem. The helper uses `hc send --json` with a literal terminal ID and returns after the delivery attempt. It leaves completed work in a private state file under the run directory. Herdr messages wake the receiving agent.

## Prepare the run once

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
