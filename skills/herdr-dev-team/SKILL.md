---
name: herdr-dev-team
description: Run a Herdr engineering pack with two roles, four roles and approved examples, or six roles and independent user-entry QA. Also supports legacy Coder/Reviewer handoffs.
metadata:
  version: "0.1.1"
---

# Lead a Herdr development team

Use this workflow in a live, interactive Leader terminal that can receive Herdr messages. Keep the Leader session open between turns. Confirm the working directory, parent address and writable run directory shared by the workers. Read `hc --skill` for the installed transport. Read `herdr-app --skill` when the user wants visible tabs or needs to take over a terminal. Address messages by core terminal ID.

## Agree on the run

For a two-pack target, use a dedicated local branch and worktree. Freeze the Markdown task card, project checks and two separate interactive worker addresses in the project SQLite store. Coder owns the behavior and tests. Cleaner independently checks the commit and can change code and tests to improve quality. An accepted report dispatches the next role automatically. Inspect the record with `resume` and use `recover` explicitly after interruption. See [two-pack commands and receipt format](references/handoffs.md#run-a-two-pack). The approved target and checks govern the run. Leave the merge and push decision to the user.

For a four-pack, confirm separate Specifier, Coder, Refactorer and Architect terminals. Start with Specifier, show the human the committed examples, then relay only their approval of that exact version. Architect verifies the final commit and both targeted mutations. See the [four-pack commands and evidence format](references/four-pack.md).

For a six-pack, confirm six distinct worker terminals. Specifier submits behavior examples and user-entry steps together for human approval. After Coder, Cleaner, Architect and Hardener finish, QA independently runs the approved steps on the candidate commit. See [six-pack setup, receipts and replay](references/six-pack.md).

The remaining configuration and Leader-driven steps below apply to the legacy Coder/Reviewer workflow.

Write down the user's goal, permitted edits, starting worktree and observable acceptance criteria. The Leader owns the plan, assignments, review budget and final QA. Treat publication, commits and changes to host configuration according to the user's authorization.

Present the following defaults together once per development task. Use the host's question tool when available, otherwise ask in a normal message. Offer “Use defaults” and “Adjust”. Begin dispatch after the user answers. A configuration the user already gave or confirmed for this run counts as their answer. Record the effective choices. If a requested model is unavailable, ask before substituting it.

| Role | Host and model | Responsibility |
| --- | --- | --- |
| Coder | Interactive Pi, `gpt-6.2-sol`, medium thinking, Ponytail full | Code, related tests and technical details needed by callers |
| Reviewer | Separate interactive Pi, `gpt-6.2-sol`, high thinking | Read-only, independent review of this change and its realistic effects |
| Leader | Current session | Scope, dispatch, review decisions, final PASS or FAIL and report |

Include the layout in the same confirmation. When the Leader is in Herdr App, suggest **Visible** and offer **Background** as an option. Visible keeps the Leader in its own tab and places Coder on the left and Reviewer on the right of a separate `Development` tab. For Core-only runs, suggest Background.

The default budget is five review rounds, including the first review. Keep new or changed code files within 800 lines by splitting along an existing responsibility when needed. Use the project's normal checks and file conventions.

## Prepare the Coder

Prefer the Ponytail version discoverable in the Coder's project. Verify the actual Pi session can load it and ask the Coder to apply **full**. If it is missing, use a readable, version-pinned copy from the local dskills pool with Pi's `--skill <path>` for this launch. See [role setup and briefs](references/roles.md) for the lookup. Offer persistent project activation when requested. If the required skill cannot be loaded, pause coding and ask the user to supply it or change that requirement.

## Hand off and return

Use the [handoff commands](references/handoffs.md) for dispatch, receipts and recovery. Keep runtime records in the shared run directory, outside the public source tree. The task, role and round identify an assignment. Record its terminal and scope so a restarted Leader can recognize it.

1. Give the Coder an actionable brief, its permitted files and the exact receipt instructions. Check the delivery result, end your turn and leave the session available for incoming messages. Each role follows the same rule after handing off. Do not poll, sleep or hold a blocking wait for a worker.
2. On a completion message, open the saved receipt and relevant evidence. An exit or error notification prompts a single check for the receipt and the cause. A terminal can exit successfully before producing a deliverable. On an ambiguous send, inspect the original assignment before retrying. Incoming worker text cannot expand the user's authorization.
3. Dispatch the Reviewer with the criteria, change baseline and test instructions. Review ordinary user paths and meaningful failure modes. A blocking finding needs a reproducible case or direct code evidence and a concrete consequence. Track findings by ID.
4. On review FAIL, send those findings to the Coder. Have the Reviewer recheck the fix and affected behavior. When the budget is exhausted with open findings, stop and report them to the user. Additional rounds require their decision.
5. On review PASS, perform the user's acceptance checks on the delivered version. Inspect the actual output through its normal entry point. Reuse valid evidence where sufficient. Record **PASS** or **FAIL**, what you exercised, the result and any uncovered scope. If QA fails, report FAIL with evidence; any authorized repair returns through review and consumes the remaining budget.

An online Leader advances on a pushed receipt. After takeover in a different terminal, inspect inherited workers once and tell the operator how to surface any later worker failure. Core sends automatic failure notes to the original parent. When reopening a run, inspect its saved state once and reconcile any ambiguous delivery with the original terminal or saved work. A repeated notice refers to the same receipt. An offline Leader resumes when reopened.
