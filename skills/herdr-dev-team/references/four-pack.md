# Operate a four-pack

Run `handoff.py` from the installed skill. Give the Delivery Lead and four workers distinct interactive terminals on the same Herdr Core. The workers share a dedicated branch and worktree. Coordinate edits with the worker who holds the assignment. Keep the owner-only `STORE` outside the worktree. All packs use the project's existing `two-pack.sqlite3`. No workflow step merges or pushes; the user decides what to do with the accepted commit.

## Set up and start

Use the [worker launch instructions](handoffs.md#launch-the-workers) for Pi and verify each terminal address.

Write a UTF-8 Markdown `CARD` with the scope, baseline, acceptance checks and applicable dependency rules. At init, choose the project-relative `EXAMPLES` path. Specifier commits a regular UTF-8 file there. A named acceptance test must read its expectations from that file at run time and compare them with actual implementation output. For Python, pin the project's unittest command with verbose output so each case is identifiable.

`CHECKS` lists the commands for each role. Replace the sample module names with the checks for this target. Unit and acceptance checks can use the same command or separate commands.

```json
{
  "specifier": [],
  "coder": [
    {"name": "unit", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "acceptance", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0}
  ],
  "refactorer": [
    {"name": "unit", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "acceptance", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "properties", "command": "python3 -m unittest -v test_properties", "exit_code": 0}
  ],
  "architect": [
    {"name": "unit", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "acceptance", "command": "python3 -m unittest discover -s tests -v", "exit_code": 0},
    {"name": "dependencies", "command": "python3 -m unittest -v test_dependencies", "exit_code": 0},
    {"name": "mutation", "command": "python3 -m unittest discover -s tests -v", "exit_code": 1},
    {"name": "example-mutation", "command": "python3 -m unittest discover -s tests -v", "exit_code": 1}
  ]
}
```

All downstream `acceptance` commands must match. Architect's `mutation` repeats its `unit` command and `example-mutation` repeats its `acceptance` command. Optionally pin both `test` and `assertion` on either mutation. Put extra pinned checks under the role that runs them. Pin `dependencies` with exit code 0 when the project has a dependency check to run; otherwise omit that command. Architect records boundaries and findings either way.

Start the run from the recorded Lead terminal.

```sh
python3 "$HELPER" "$STORE" four init --project "$WORKTREE" --card "$CARD" --checks "$CHECKS" \
  --examples "$EXAMPLES" --specifier "$SPECIFIER_ID" --coder "$CODER_ID" \
  --refactorer "$REFACTORER_ID" --architect "$ARCHITECT_ID"
# Set RUN_ID to the id emitted by init.
python3 "$HELPER" "$STORE" four "$RUN_ID" start
python3 "$HELPER" "$STORE" four "$RUN_ID" resume
```

`start` assigns Specifier. The prompt contains the card, checks, role brief and receipt command. Keep the dedicated branch checked out while the run is active.

## Review and approve the actual examples

After Specifier reports `done`, `resume` shows `waiting-for-human`. The `specification` object contains the path, version, SHA-256, submitting commit and exact UTF-8 body saved in SQLite. Earlier submissions and approval evidence appear in `specification_versions`. Version numbers follow Specifier assignment rounds and can skip a number after rework.

Show the user the saved examples, path, submitting commit, version and full SHA-256. Highlight changes from the previous version. Ask whether they approve that version or want a change. Relay their approval from the recorded Lead terminal.

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" approve "$VERSION" "$SHA256" --evidence "$USER_EVIDENCE"
```

Put the user's decision text or a durable reference in `USER_EVIDENCE`. The CLI checks the Lead terminal ID and, for first approval, the clean submitting commit. It records approval and the single Coder assignment together. Repeating approval for the unchanged version returns that assignment. No Coder assignment exists for this version before approval; starting, recovering, a role report or a card revision cannot grant it. If the user requests changes while approval is pending, run `four RUN_ID rework` to send the specification back.

## Submit receipts and checks

Submit the receipt from the assigned terminal using the command in the prompt.

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" report ROLE ROUND STATUS --result "$RESULT"
```

Record the full input and checked Git commit IDs. Commit edits, then run the checks at the worktree root. For check-only work, report the input commit. For Python, `PYTHONDONTWRITEBYTECODE=1` suppresses bytecode files. Save combined stdout/stderr in a unique log for each check and record its actual exit status. Evidence paths can be relative to `STORE`. A passing receipt includes all the role's pinned checks. Specifier supplies the committed examples path in its `done` receipt.

```json
{
  "input_commit": "<assigned full commit>",
  "commit": "<full commit with the examples>",
  "issues": "<what changed, or empty>",
  "examples": "<the pinned project-relative path>",
  "checks": []
}
```

Coder and Refactorer report `done`. Architect reports `pass`. Each downstream role includes the approved identifiers in `specification`. Add an entry for each pinned check. Architect also records the dependency findings in `architecture`.

```json
{
  "input_commit": "<assigned full commit>",
  "commit": "<checked full commit>",
  "issues": "<known issues, or empty>",
  "specification": {"version": 1, "sha256": "<full approved SHA-256>"},
  "architecture": "<boundaries, dependency rules, findings and verification scope>",
  "checks": [
    {"name": "unit", "command": "<exact pinned string>", "commit": "<checked full commit>",
     "exit_code": 0, "log": "<path to actual unit output>"}
  ],
  "artifacts": [{"path": "<extra evidence file>", "commit": "<checked full commit>"}]
}
```

Architect runs the final checks independently. Start each mutation in a separate disposable checkout at the checked commit. For `mutation`, change implementation code, not the tests or specification. For `example-mutation`, change only one key expected value in the approved file; it may span multiple lines. Repeat the corresponding baseline command. Identify the named test that passed on the original and failed at the intended assertion. Syntax and missing-file errors do not count. Save its qualified unittest ID, assertion text, complete output and replayable Git patch.

```json
{
  "name": "example-mutation",
  "command": "<exact pinned acceptance command>",
  "commit": "<checked full commit before mutation>",
  "exit_code": 1,
  "log": "<mutant output file>",
  "patch": "<git diff file>",
  "test": "test_acceptance.AcceptanceTest.test_observable_behavior",
  "assertion": "<text of the intended AssertionError>"
}
```

The helper checks the commit, command and evidence, replays each patch in a temporary Git index and snapshots the submitted files in `STORE/evidence`. The workers run the project checks. The Lead can replay them using the snapshots and `final_commit` shown in `resume`. The completed record stays bound to the commit Architect accepted.

## Recover, revise or repair

Inspect the stored state with the read-only `four RUN_ID resume`. The recorded Lead can take the following actions when the state calls for them.

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" recover
python3 "$HELPER" "$STORE" four "$RUN_ID" reconcile ROLE ROUND ATTEMPT delivered --evidence "$OBSERVATION"
python3 "$HELPER" "$STORE" four "$RUN_ID" reconcile ROLE ROUND ATTEMPT retry --evidence "$OBSERVATION"
python3 "$HELPER" "$STORE" four "$RUN_ID" revise --card "$CARD" --checks "$CHECKS"
python3 "$HELPER" "$STORE" four "$RUN_ID" rework
```

- `recover` claims an unattempted pending assignment. For uncertain delivery, inspect the original terminal, assignment ID and attempt. Record `delivered` when found. Use `retry` only with positive evidence that the original message never arrived and can no longer arrive. A matching accepted receipt also resolves uncertainty.
- A committed change to the examples on an open run requires fresh approval, including when a later commit reverts the change. The helper can retire an unclaimed assignment immediately. If the task reached the worker, have the worker stop and report `superseded` with the old specification identifiers, clean current commit, checks and a failure reason. This hands the worktree to Specifier. A worker with uncommitted edits can report `paused` or `blocked`. Clean the worktree before running `recover`. If inspection proves the original message could never arrive, reconciling with `retry` retires the stale task. The replacement commit must descend from the active assignment input or its accepted paused or blocked output. While approval is pending, it must descend from the submitting commit.
- Refactorer and Architect can report `fail` with a failing pinned check and a `failure` object containing `check`, `input`, `expected` and `actual`. Coder receives the failed commit and finding. For missing or invalid evidence, correct the receipt and resubmit it. Any role can report `blocked` or `paused` with `failure.reason` and available checks. Use `rework` after resolving the problem, or `rework --specification` to send the examples back for review. Record a rollback in a new commit to preserve the history and evidence.
- `revise` registers a card draft. Add `--checks` for updated checks. To activate the draft while ready, waiting for approval, paused or blocked, include `--approve-evidence "$USER_EVIDENCE"`. Obtain the user's approval for changes to acceptance criteria. Before start, activation leaves the run ready. Later activation returns it to Specifier for a separate approval of the executable examples.
