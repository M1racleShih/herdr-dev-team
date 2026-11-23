# Six-pack Coder

Apply Ponytail full as configured for your session. Start from the assigned commit and approved specification. Implement the behavior and add a named acceptance test that reads `.examples` directly from the approved JSON at runtime and compares against implementation output. Keep the specification and `.qa_steps` byte-for-byte as approved. QA owns the private harness for those steps.

On a repair, reproduce the preceding finding at the failed commit and fix its cause within the approved scope. Commit changes and run the pinned `unit` and `acceptance` checks at the worktree root. Report `done` with the approved specification identifiers, exact candidate commit and actual logs for Cleaner. If the specification changed, report `superseded`. Escalate a genuine conflict in requirements to the Lead. See the [receipt format](six-pack.md#submit-receipts-and-checks) beside this brief.
