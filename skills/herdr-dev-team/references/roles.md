# Role setup and briefs

Get the Leader's actual core terminal address from its environment and put that literal address in the brief. A worker's own `HC_TERMINAL_ID` identifies the worker. Keep the assignment and run paths reachable from both terminals. Use local interactive workers sharing the run directory for this release.

## Launch interactive Pi

Run `hc --skill` for the installed version and use the confirmed provider, model and thinking level. If Core reports that it cannot find Pi, check the executable in the Leader's shell and in the Core environment. For a Node-managed Pi installation, pass the resolved absolute paths of both Node and the Pi entry point to Core. A launch error saying that the program could not start allows a corrected launch; an ambiguous timeout requires checking the original terminal first.

Keep each role in a separate interactive terminal. When the user asks for visible tabs, consult `herdr-app --skill` to show the intended terminals in the requested space. The run still uses their Core terminal addresses for delivery.

## Select Ponytail

Check the Coder's project and user skill locations first, including Pi's shared Agent Skills directories. For a dskills-managed project, inspect `dskills --json status --project <project>` for the pin and link. In a project without the skill, `dskills --json info ponytail` provides available versions and the current digest. Resolve that digest in the configured pool's `objects/<digest>` directory. Verify that the directory contains the expected Ponytail SKILL.md before passing its absolute path to Pi with `--skill`. The Coder reads it and applies the full level. Some Pi installations expose this as `/skill:ponytail full`.

To persist Ponytail at the user's request, use the project's dskills configuration. For an existing symlink-mode project use `dskills plug --project <project> ponytail --revision <digest>`. A new Pi-only selection can be initialized with `dskills init --project <project> --agent pi --mode symlink`. Check the current mode and avoid changing other selected agents or pins. Refresh the target session if needed.

## Coder brief

Give the exact scope, repository, files the Coder owns, acceptance cases and test command. Include the chosen model and thinking level, the code size limit, and how to use Ponytail full. Ask the Coder to preserve existing edits, run relevant checks and send a saved receipt with its result, changed paths, checks actually run and unresolved issues. After reporting, the Coder ends its turn. It can accept a later repair assignment in the same terminal.

## Reviewer brief

Give the actual starting version or change list, the user-visible outcome, the submitted version and the open finding IDs. Request read-only review in an independent session. Ask for PASS or FAIL. Each blocker identifies the location, realistic trigger, observed and expected behavior, and the smallest relevant correction. For a fix, verify the original case against the delivered version. Save the receipt and notify the Leader.

## Leader QA report

Lead with PASS or FAIL. Identify the version or worktree you tested. For each acceptance condition, give the action, observed result and evidence location. State what was left unverified if it affects the judgment. Keep runtime notes and local paths in the private run directory; select only appropriate, portable evidence for public release notes.
