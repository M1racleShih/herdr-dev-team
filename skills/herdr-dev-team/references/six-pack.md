# Operate a six-pack

The Delivery Lead coordinates six distinct interactive workers on the same Herdr Core. The workers take turns in a dedicated Git worktree. QA accepts the final candidate from the public entry point. The helper records acceptance; the user decides how to deliver the commit.

## Set up and start

Use the [worker launch instructions](handoffs.md#launch-the-workers) for Pi and verify each terminal address. Prepare `CARD` with the scope, baseline, observable behavior and applicable dependency rules. Keep `STORE` owner-only and outside the worktree. Choose a project-relative `EXAMPLES` path and a private script destination for QA. QA writes that script when assigned.

Pin check commands in `CHECKS`, a UTF-8 JSON object keyed by the role names below. Each role has an array of `{ "name": "…", "command": "…", "exit_code": 0 }` objects. Use `[]` for Specifier.

| Role | Required pinned checks |
| --- | --- |
| `specifier` | Empty array |
| `coder` | `unit`, `acceptance` |
| `cleaner` | `unit`, `acceptance` |
| `architect` | `unit`, `acceptance`, `properties`, with `dependencies` when a project check applies |
| `hardener` | `unit`, `acceptance`, `mutation` |
| `qa` | `user-entry`. A QA-owned fix also uses the Coder's `unit` and `acceptance`, and the Architect's `properties` and any pinned `dependencies` on the repaired commit. |

Pin exit code `0` for the baseline checks and `1` for `mutation`. Coder through Hardener use the same `acceptance` command. Hardener uses the identical verbose unittest command for `unit` and `mutation`, for example `python3 -B -m unittest -v test_target`. You can pin `test` and `assertion` together on `mutation`. Pin `user-entry` to the harness invocation, for example `python3 -B '/private/run/user-entry.py'`. The approved spec pins the public command that the harness launches.

From the Lead terminal, set `HELPER` to the installed `handoff.py` and run:

```sh
python3 "$HELPER" "$STORE" six init --project "$WORKTREE" --card "$CARD" --checks "$CHECKS" \
  --examples "$EXAMPLES" --specifier "$SPECIFIER_ID" --coder "$CODER_ID" \
  --cleaner "$CLEANER_ID" --architect "$ARCHITECT_ID" --hardener "$HARDENER_ID" --qa "$QA_ID"
# Use the run ID emitted by init.
python3 "$HELPER" "$STORE" six "$RUN_ID" start
python3 "$HELPER" "$STORE" six "$RUN_ID" resume
```

The assignment contains the frozen card, role brief, checks, exact input commit and report command. Workers coordinate worktree access through the active assignment.

## Approve the behavior and user steps

Specifier commits one regular UTF-8 JSON file. The project acceptance test reads `examples` at runtime. QA uses the ordered `qa_steps` array with exact command, stdout, stderr and exit status. Both arrays contain at least one entry. A simple increment CLI could use:

```json
{
  "examples": [{"input": 2, "expected": 3}],
  "qa_steps": [
    {"command": "python3 increment.py 2", "stdout": "3\n", "stderr": "", "exit_code": 0}
  ]
}
```

Specifier reports `done` with `examples` set to the pinned project-relative path. `resume` then shows `waiting-for-human` and the saved specification. Present its exact body, path, version, SHA-256 and submitting commit to the user. Highlight changes from any prior version. Only the recorded Lead relays the actual user's approval:

```sh
python3 "$HELPER" "$STORE" six "$RUN_ID" approve "$VERSION" "$SHA256" --evidence "$USER_EVIDENCE"
```

Approval records the first Coder assignment for that version. A repair under identical approved behavior keeps that specification version and digest.

## Submit receipts and checks

Run the report command from the assigned terminal. Use full Git commit IDs. Every result has `input_commit`, `commit`, an `issues` string and a `checks` array. Specifier's `done` uses `checks: []`. Every downstream result also includes `specification: { "version": 1, "sha256": "<approved full SHA-256>" }` with the assignment's version.

Commit edits and run checks on clean `HEAD` from the project root. Check-only work keeps the input commit. Run each pinned command and record its actual exit status with a log stored outside the worktree, at an absolute path in `STORE` or relative to it.

```json
{"name": "unit", "command": "python3 -B -m unittest -v test_target",
 "commit": "<full checked commit>", "exit_code": 0, "log": "unit.log"}
```

Coder, Cleaner, Architect and Hardener advance with `done` and their complete pinned evidence. Architect also records an `architecture` string covering dependency boundaries, findings and verification scope. `artifacts` can hold extra `{ "path": "…", "commit": "…" }` evidence files. Use the provided role, round and receipt command:

```sh
python3 "$HELPER" "$STORE" six "$RUN_ID" report ROLE ROUND STATUS --result "$RESULT"
```

### Hardener mutation

Run the original unit check. Introduce a targeted code error in a disposable checkout at the same commit and rerun that command. Keep tests and approved JSON intact. Capture the original success and the failing assertion for the same named test. Save the Git diff and mutant log in `STORE`. For the increment case, changing the core to return its input should fail the example assertion with `2 != 3`.

```json
{"name": "mutation", "command": "python3 -B -m unittest -v test_target",
 "commit": "<full original commit>", "exit_code": 1, "log": "mutant.log",
 "patch": "mutation.patch", "test": "test_target.AcceptanceTest.test_increment",
 "assertion": "2 != 3"}
```

The helper replays the patch against that commit in an isolated Git index. For a manual rerun, apply `patch_snapshot` in a disposable checkout of the reported commit and repeat the pinned command. `log_snapshot` holds each original log.

### QA run and replay

QA writes the harness at the private path named in the pinned command. It runs every approved user command in the clean candidate worktree, captures its actual output and exit code, and compares observations exactly. For the first observation, use the assigned input commit as the checked commit. The harness records the exact SHA-256 of its source file and writes this JSON as the `user-entry` check log:

```json
{
  "commit": "<full candidate commit>",
  "script_sha256": "<SHA-256 of the harness bytes>",
  "observations": [
    {"command": "python3 increment.py 2", "stdout": "2\n", "stderr": "", "exit_code": 0}
  ],
  "exit_code": 1
}
```

Use the harness exit code for the pinned `user-entry` check row. Include `qa: { "script": "<private script path>", "observations": […] }` with the same observations as the log. A command may exit `0` with incorrect stdout, as above. The harness then exits nonzero. On a mismatch record the original commit, harness, log, and a `failure` object with `check` (`user-entry`), `input`, `expected` and `actual`. Choose the route before editing the product:

| Finding | Report | What happens next |
| --- | --- | --- |
| QA reproduced and located a local defect under the approved behavior, such as a CLI printing its input after calculating the right answer | `repair` on the failed candidate with the diagnosis in `issues` | The helper archives the failure and assigns QA a new round. QA commits the fix and submits fresh repair checks and the original harness results on the new commit. |
| The failure needs implementation work across modules, a design change or investigation by Coder | `fail` on the failing candidate | Coder receives the failed commit, expected and observed behavior, and the QA receipt. The normal downstream checks run again. |
| The approved steps and examples have conflicting expectations | `paused` with `failure.reason` | The Lead brings the conflict to the user. A revised specification goes back through approval. |

Both routes reuse the identical archived harness under the same approval. After a QA-owned fix, use the repair assignment's `input_commit` and the new checked commit as `commit`. On that commit run the frozen repair checks listed above, the public entry check, and any additional project checks affected by the change. Include the required check rows and logs. An accepted QA `pass` binds `final_commit` to the version that passed.

If the attempted fix exposes a broader defect, QA can report `fail` from the repair assignment with the current clean candidate, the archived harness observations, and a failing pinned check. For example, the user command may now pass while the `properties` check fails. Name that check in `failure` and include its log. Coder takes over at the reported commit.

From `resume`, retain the failed and accepted commits, their observations, `checks[].log_snapshot` and `qa.script_snapshot`. To replay, run the archived harness with the same interpreter and options from a clean disposable checkout at the recorded commit. The above pinned Python invocation becomes `python3 -B "$STORE/$SCRIPT_SNAPSHOT"`. Compare the replay to that candidate's archived log.

## Recover, revise or repair

Use the read-only `six RUN_ID resume` after interruption. `recover` claims an unattempted pending assignment. If delivery is uncertain, inspect the original terminal and attempt, then use `reconcile ROLE ROUND ATTEMPT delivered --evidence OBSERVATION` when found, or `retry` when inspection proves the message cannot arrive. The [delivery procedures](four-pack.md#recover-revise-or-repair) also apply here using `six` in the commands.

- Cleaner, Architect, Hardener and QA can report `fail`. Attach a pinned check whose actual exit code differs from its pinned code and a `failure` with `check`, `input`, `expected` and `actual`. Coder receives the failed commit and finding. Run the downstream roles again on the resulting candidate.
- Any role can report `blocked` or `paused` with `failure.reason` and available checks. A conflict between `examples` and `qa_steps` goes to the user. From a paused or blocked run, the Lead uses `rework --specification` to involve Specifier and present a revised version for approval. For an ordinary blocker resolved within scope, use `rework` to reassign the role.
- For a paused QA assignment, `rework` resumes QA on the candidate delivered by Hardener. If the commit changed during the pause, Coder takes that version back through the engineering checks. An already recorded QA-owned repair stays with QA and keeps its original harness and repair checks.
- A committed edit to the approved JSON requires a fresh submission and approval, including when a later commit restores the original bytes. A worker who received the stale assignment submits `superseded` using the old approval identifiers, clean current commit and `failure.reason`. A rollback uses a new commit so every candidate and finding remains inspectable.

[Original Swarm Forge six-pack](https://github.com/unclebob/swarm-forge/blob/066a62ffa6cbc8c859262536dc579fecf8535ebb/README.md)
