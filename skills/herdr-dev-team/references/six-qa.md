# Six-pack QA

Read the approved `.qa_steps` and author the harness yourself at the script path named in the pinned `user-entry` command in the private store. Start the genuine user entry point from the clean assigned candidate at its exact commit. Capture each approved command in order, including actual stdout, stderr and process exit code. Compare each field verbatim, including empty output and newlines. Keep the harness and logs outside the candidate checkout.

Report `pass` with `user-entry` exit `0` when every observation matches. If the CLI exits `0` with incorrect output, preserve that exit code in the observation and return a nonzero verdict from the harness. Record the mismatch on the failed commit before editing the product. Choose the next step from what you reproduced:

- A localized defect with a known cause under the approved behavior belongs to QA. Report `repair` against `user-entry` with the failed command, expected and actual results, and describe the diagnosis in `issues`. For example, the core returns the correct value but the CLI prints its input. On the next QA assignment, commit the minimal fix, run the listed repair checks on that commit, and replay the archived harness for `user-entry`. Submit the new commit and its check logs with `pass` when all pass.
- A defect requiring broader implementation or architectural work goes to Coder. Report `fail` with the same failure evidence. Include any verified diagnostic observations in `issues` and attach useful traces via `artifacts`.
- If the approved examples and user steps disagree, report `paused` with the conflict for the Lead to take to the user. Report `superseded` when the approved specification changes in Git.

Report the private script path and actual observations in `qa`. Attach the JSON log with the checked commit, exact script SHA-256, observations and harness exit code. Both repair routes reuse the archived script under the same approval. See the [receipt and replay format](six-pack.md#qa-run-and-replay).
