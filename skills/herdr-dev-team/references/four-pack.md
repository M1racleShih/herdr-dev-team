# Operate a four-pack

Use `handoff.py` from the installed skill. The recorded Delivery Lead and four workers must be distinct interactive terminals on the same Herdr Core. Give the workers the same dedicated local branch and worktree. Only the assigned worker edits that worktree; coordinate any intervention with that worker. Keep the owner-only `STORE` outside the worktree. `four` and `two` share the project's `two-pack.sqlite3` without changing historical runs. No step merges or pushes.

## Set up and start

Write a UTF-8 Markdown `CARD` with scope, baseline, acceptance and intended dependency rules. Pick the exact project-relative `EXAMPLES` path (it need not exist at init). The Specifier commits a regular UTF-8 file using the project's native executable-example format. The acceptance command must read that file at run time, not a copy or hard-coded expectations. For a Python project, for example, pin `python3 -m unittest -v test_acceptance` with the project's module name, or the project's existing `unittest discover` command. Have a named test assert actual implementation output against the file's expected values.

`CHECKS` is a JSON object with four arrays. This example uses the same discover command for unit and acceptance; projects can pin separate commands. Replace the example module names with the actual checks from the card.

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

All downstream `acceptance` commands must match. Architect's `mutation` repeats its `unit` command and `example-mutation` repeats its `acceptance` command. Optionally pin both `test` and `assertion` on either mutation. Extra pinned checks go under the role that runs them.

From the Lead terminal:

```sh
python3 "$HELPER" "$STORE" four init --project "$WORKTREE" --card "$CARD" --checks "$CHECKS" \
  --examples "$EXAMPLES" --specifier "$SPECIFIER_ID" --coder "$CODER_ID" \
  --refactorer "$REFACTORER_ID" --architect "$ARCHITECT_ID"
# Set RUN_ID to the id emitted by init.
python3 "$HELPER" "$STORE" four "$RUN_ID" start
python3 "$HELPER" "$STORE" four "$RUN_ID" resume
```

`start` assigns only Specifier. The prompt contains the pinned card, checks, role brief and exact receipt command. Check out no different branch while a role is active.

## Review and approve the actual examples

After Specifier reports `done`, `resume` shows `phase: waiting-for-human`, no Coder assignment for that version, and `specification` with `path`, `version`, `sha256`, `commit` and `body`. This is the durable, exact UTF-8 content saved from the submitting Git commit in SQLite, not a read of the current worktree. `specification_versions` retains earlier text and approval evidence even after another Specifier is assigned. The `version` is the Specifier's assignment round, so numbers may skip.

Present the human with the actual examples (`body`), path, submitting commit, version, full SHA-256, and the differences from the prior version if any. Ask them to approve that exact version or give changes. Neither another role nor the Lead substitutes their own decision. Only after the human approves, the recorded Lead terminal relays it:

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" approve "$VERSION" "$SHA256" --evidence "$USER_EVIDENCE"
```

Use the user's actual decision text or a durable reference to it in `USER_EVIDENCE`. The terminal check authenticates the assigned Lead in this local workflow; the Lead is responsible for faithfully relaying the human's decision. The first approval requires the submitting commit and a clean matching worktree. Approval and the single Coder assignment are recorded together; an unchanged approved version is idempotent. `resume`, `start`, `recover`, a role report and registering or activating a card never grant example approval. If the human requests changes, run `four RUN_ID rework` while waiting to assign Specifier again.

## Submit receipts and checks

From the assigned terminal, use the command in the prompt, or:

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" report ROLE ROUND STATUS --result "$RESULT"
```

Use full input and checked Git commit IDs. Run the checks at the root of the recorded commit, after committing edits. Check-only work uses the input commit, not an empty commit. Keep untracked check byproducts out of the worktree (for example, set `PYTHONDONTWRITEBYTECODE=1` for Python). Save the actual combined stdout/stderr and exit status to unique files in `STORE`, outside the worktree; every evidence path below can be relative to `STORE`. Successful receipts contain all pinned checks. Specifier's `done` receipt is this shape (replace placeholders with observed values):

```json
{
  "input_commit": "<assigned full commit>",
  "commit": "<full commit with the examples>",
  "issues": "<what changed, or empty>",
  "examples": "<the pinned project-relative path>",
  "checks": []
}
```

Coder and Refactorer report `done`; Architect reports `pass`. All three omit `examples` and include `specification` with the approved identifiers. Ordinary check rows and optional attachments use this format; include `architecture` only as needed (required for Architect `pass`):

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

Architect runs the checks independently on the final commit, including the dependency check. For each mutation, start with a **separate disposable checkout** at that exact commit. For `mutation`, change implementation code, without editing the approved examples or the tests. For `example-mutation`, change only a key expected value on one line of the pinned file. Run the same pinned command as on the original. A named test that passed on the original must fail with the intended assertion; a syntax error, missing file or unrelated failure does not count. Keep the replayable `git diff`, complete output, qualified unittest ID and exact assertion text for each. Use a row like this for each mutation, with its actual name and results:

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

The helper checks the commit, command and evidence, replays the patches against that commit in a temporary Git index, and snapshots submitted files in `STORE/evidence`. The workers run the commands and the Lead can inspect and replay them; the CLI does not run project tests itself. `resume` shows snapshot paths and the accepted `final_commit`. Later work does not reopen a completed run or change that commit.

## Recover, revise or repair

Use `python3 "$HELPER" "$STORE" four "$RUN_ID" resume` to read without claiming work, inspecting Git or writing. From the recorded Lead terminal:

```sh
python3 "$HELPER" "$STORE" four "$RUN_ID" recover
python3 "$HELPER" "$STORE" four "$RUN_ID" reconcile ROLE ROUND ATTEMPT delivered --evidence "$OBSERVATION"
python3 "$HELPER" "$STORE" four "$RUN_ID" reconcile ROLE ROUND ATTEMPT retry --evidence "$OBSERVATION"
python3 "$HELPER" "$STORE" four "$RUN_ID" revise --card "$CARD" --checks "$CHECKS"
python3 "$HELPER" "$STORE" four "$RUN_ID" rework
```

- `recover` can claim the original pending assignment. An uncertain send is not retried automatically. Inspect the original terminal and matching assignment and attempt, then record `delivered` if found. Use `retry` only with positive evidence that the send did not and cannot arrive. An accepted matching receipt also resolves uncertainty.
- A committed change to examples on an open run (including a later revert) prevents normal downstream receipts and requires new Specifier review. An unclaimed assignment can be retired without sending. If it may have arrived, do not send a replacement yet. Confirmed delivery is **not** a worker yield. Have the assigned worker stop and report `superseded` with the old specification identifiers, current clean commit, `checks` and `failure: {"reason": "..."}`. This releases the worktree directly to Specifier. If the worker must stop dirty, use `paused` or `blocked`, then clean the worktree and run `recover`. If inspection proves that an uncertain send never arrived, `reconcile ... retry` retires it instead of re-sending it when the examples changed. The replacement requires a clean HEAD descending from the active assignment's input (or its accepted paused/blocked commit); while waiting for approval the anchor is the submitting commit.
- Refactorer and Architect can report `fail` with an unexpectedly failing pinned check and `failure` containing `check`, `input`, `expected` and `actual`. This returns the failed commit and finding to Coder for a new round. Missing/invalid evidence rejects the receipt and leaves the assignment available for correction. Any role may report `blocked` or `paused` with `failure.reason` and available checks. Use `rework` to reassign a paused or blocked role after resolution, or `rework --specification` to obtain a new reviewed version. Keep prior commits; make any rollback a new commit so the history and previous checks remain replayable.
- `revise` registers a card draft; omit `--checks` when unchanged. To activate a new card add `--approve-evidence "$USER_EVIDENCE"` while ready, waiting for the human, paused or blocked. Use actual user approval if acceptance criteria change. Activation before start leaves the run ready; otherwise it returns to Specifier. Approving card scope is not approval of the examples.
