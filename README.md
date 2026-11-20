# Herdr Dev Team

An Agent Skill for leading development with Herdr. An interactive Leader sends work to a Pi Coder, requests independent review and performs final acceptance. Workers save their results and notify the Leader. An interrupted Leader can reopen its run and pick up the next handoff.

## Use it

Load `herdr-dev-team` in the Leader's agent and ask it to use Herdr for a concrete change. For example:

> Use Herdr Dev Team to fix the CSV export. Preserve quoted fields and empty rows. Verify the exported file by importing it again.

The Leader offers the role configuration once. The defaults are Pi with `gpt-6.2-sol` at medium thinking for coding, the same model at high thinking for review, and up to five review rounds. The Coder uses Ponytail full. The Leader reports final QA as PASS or FAIL.

Choose the layout in the same confirmation. In **Visible** mode, the Leader keeps its tab and focus. A separate `Development` tab shows the Coder on the left and the Reviewer on the right. In **Background** mode, both workers run through Core and send receipts to the Leader.

Run the Leader inside a live Herdr terminal that accepts incoming messages. The participants need a shared local run directory, Python 3.8 or later on a POSIX host, and access to `hc`. Pi and the selected models must be available to the workers. The visible layout was exercised with Herdr Core 0.1.21 and Herdr App 0.1.16 on Linux.

## Install 0.1.1

The installable skill is [skills/herdr-dev-team](skills/herdr-dev-team/SKILL.md). Place that directory or a symlink in the project's skill discovery path. For a first import, add the skill to the dskills pool and activate it in the current project.

```sh
dskills install https://github.com/M1racleShih/herdr-dev-team.git --ref v0.1.1 --skill skills/herdr-dev-team
dskills plug --project . herdr-dev-team
```

Use the project's existing activation mode. Refresh or restart the Leader session if it has already loaded its skill catalog.

## Handoffs and acceptance

The [skill](skills/herdr-dev-team/SKILL.md) defines the workflow. [Role setup](skills/herdr-dev-team/references/roles.md) covers Ponytail and the briefs; the [handoff guide](skills/herdr-dev-team/references/handoffs.md) covers the state helper and crash recovery. Normal delivery runs without polling or a background service. If a notification is lost while the Leader is offline, recovery starts the next time the run is opened.

Run the regression suite from the repository root:

```sh
python3 -m unittest discover -s tests
```
