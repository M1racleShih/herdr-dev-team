---
name: herdr-dev-team
description: Run a Herdr engineering pack with two roles, four roles and approved examples, or six roles and independent user-entry QA.
metadata:
  version: "0.4.0"
---

# Lead a Herdr development team

Use this workflow in a live, interactive Leader terminal that can receive Herdr messages. Keep the Leader session open between turns. Confirm the working directory, terminal IDs and writable run directory shared by the workers. Use the [worker launch instructions](references/handoffs.md#launch-the-workers) for visible Herdr App panes or background Core terminals. Address messages by Core terminal ID.

## Agree on the run

For a two-pack target, use a dedicated local branch and worktree. Freeze the Markdown task card, project checks and two separate interactive worker addresses in the project SQLite store. Coder owns the behavior and tests. Cleaner independently checks the commit and can change code and tests to improve quality. An accepted report dispatches the next role automatically. Inspect the record with `resume` and use `recover` explicitly after interruption. See [two-pack commands and receipt format](references/two-pack.md). The approved target and checks govern the run. Leave the merge and push decision to the user.

For a four-pack, confirm separate Specifier, Coder, Refactorer and Architect terminals. Start with Specifier, show the human the committed examples, then relay only their approval of that exact version. Architect verifies the final commit and both targeted mutations. See the [four-pack commands and evidence format](references/four-pack.md).

For a six-pack, confirm six distinct worker terminals. Use `--plan FILE` when the project defines its own checks and criteria. Specifier submits behavior examples and user-entry steps together for human approval. After Coder, Cleaner, Architect and Hardener finish, QA independently runs the approved steps. QA can fix a localized defect and replay the original check, return broader defects to Coder, or pause for clarification. See [six-pack setup, receipts and replay](references/six-pack.md).
