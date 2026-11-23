# Six-pack QA

Read the approved `.qa_steps` and author the harness yourself at the script path named in the pinned `user-entry` command in the private store. Start the genuine user entry point from the clean assigned candidate at its exact commit. Capture each approved command in order, including actual stdout, stderr and process exit code. Compare each field verbatim, including empty output and newlines. Keep the harness and logs outside the candidate checkout.

Report `pass` with `user-entry` exit `0` if every observation matches. On any mismatch, return a nonzero harness exit, report `fail` against `user-entry` and describe the command, expected result and actual result. If the CLI itself exits `0` with incorrect output, preserve `0` in that observation and return the nonzero verdict from the harness. If `.examples` and `.qa_steps` disagree, report `paused` and describe the conflict for the Lead to take back to the user. Report `superseded` when an approved specification has changed in Git.

Report the private script path and actual observations in `qa`, and attach the JSON log with the checked commit, exact script SHA-256, observations and harness exit code. After `fail`, reuse the identical archived script bytes when QA returns under the same approval. See the [QA receipt and replay format](six-pack.md#qa-run-and-replay) beside this brief.
