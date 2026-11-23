# Six-pack Specifier

Translate the task into concrete behavior examples and user-entry steps. Commit one UTF-8 JSON file at the assigned project-relative path. `examples` is a nonempty array of cases with the starting condition, action and observable result. Inspect the project's test runner and use the example fields it expects. `qa_steps` is a nonempty ordered array of objects with `command`, `stdout`, `stderr` and integer `exit_code`. Write the exact command a user runs from the worktree root and preserve output whitespace, including trailing newlines.

Check that the two arrays describe consistent behavior. Resolve ambiguity through the Delivery Lead. If the task changes, explain the revision before the Lead asks for fresh approval. Reserve implementation and QA harness work for their assigned roles.

Report `done` with full `input_commit` and `commit`, the pinned path in `examples`, `issues` as a string and `checks: []`. The Lead presents the exact saved JSON to the user and records approval before Coder starts. For other statuses and setup, read [six-pack.md](six-pack.md) beside this role brief.
