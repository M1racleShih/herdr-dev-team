# Six-pack roles with a project quality plan

Use the check IDs, scope, criteria and commands in your assignment. Work from its input commit and approved specification. Commit changes before running project checks on clean HEAD. From your assigned terminal run `check ROLE ROUND ID --key KEY`. Save its execution ID. Reusing the key retrieves the same invocation. Use a fresh key for an intentional rerun.

| Worker | Work to deliver |
| --- | --- |
| Coder | Implement the approved behavior. The check named by `approved_examples_check` must actually exercise the approved examples. Reproduce any prior failure on the supplied commit, using the archived evidence and diagnosis. |
| Cleaner | Review and simplify the changed area. Report the results of any observations you make. An unrun observation is recorded as untested. |
| Architect | Trace the relevant module and dependency boundaries. In `architecture`, identify the reviewer and explain the findings, rationale and verification scope. |
| Hardener | Use the method named by `hardening_check` to verify the candidate independently. Attach the project tool's native evidence through its adapter. |
| QA | Write the private harness for the pinned user-entry command. Run the approved user steps on the candidate and submit the original observations and log. On a local repair, reuse the archived harness bytes. Run any final checks missing eligible evidence on this commit, plan and specification. |

Every downstream receipt carries `input_commit`, `commit`, `specification`, `issues`, `checks` and `quality: {"plan_version": N, "executions": ["exec-…"]}`. Submit `checks: []` for engineering work. QA submits its independent `user-entry` row as well as the `qa` object. The helper verifies project executions by ID. Stage and final checks must pass for their owner's handoff. Observations can be reported with any outcome.

Use `done` for completed engineering work and `pass` for independent QA acceptance. To return a reproduced product failure to Coder, the downstream worker cites its failed execution and describes the input, expected and actual behavior in `failure`. QA also submits the real user-entry observation. QA uses `repair` for a diagnosed local entry defect. Report `blocked` or `paused` with `failure.reason` when progress requires intervention. Report `superseded` if the approved specification changed. See the [operator instructions and receipt examples](six-pack.md).
