# Herdr Dev Team

An Agent Skill for delivering changes through a team of local Herdr agents. Choose a pack for each task. The handoff helper records which role owns the next step, the Git commit under review and the evidence for each result.

| Pack | When to use it | Who signs off |
| --- | --- | --- |
| [Two-pack](skills/herdr-dev-team/references/handoffs.md#run-a-two-pack) | The behavior is clear. A Coder implements it and a Cleaner independently checks the code and tests. | Cleaner |
| [Four-pack](skills/herdr-dev-team/references/four-pack.md) | You want to approve executable behavior examples before coding. A Refactorer and an Architect check the implementation. | Architect |
| [Six-pack](skills/herdr-dev-team/references/six-pack.md) | You also need proof that the user's actual workflow works. After implementation, cleanup, architecture checks and test hardening, QA runs the approved steps through the user entry point. | Independent QA |

## Install 0.4.0

From the project where you want to use the skill, import the release into your dskills pool.
If the pool already contains an earlier release, follow [the upgrade steps](#upgrade-from-an-earlier-release).

```sh
dskills install https://github.com/M1racleShih/herdr-dev-team.git --ref v0.4.0 --skill skills/herdr-dev-team
```

For a project new to dskills, initialize it for the agent that will lead the team. This example uses Codex. In a project already managed by dskills, keep the existing configuration.

```sh
dskills init --project . --agent codex
```

Activate the skill for the project.

```sh
dskills plug --project . herdr-dev-team
```

You can also place [the skill directory](skills/herdr-dev-team) or a symlink in your agent's skill discovery path. Refresh or restart the Leader session if it has already loaded its skill catalog.

### Upgrade from an earlier release

Finish active runs before switching. Import the new release under a separate pool ID. A Git tag is part of the dskills source, so a different tag needs a different ID when the old import is still in the pool.

```sh
dskills install https://github.com/M1racleShih/herdr-dev-team.git --ref v0.4.0 --skill skills/herdr-dev-team --id herdr-dev-team-v0-4-0
```

In each project, run `dskills status --project .` to find the active skill ID. Use that ID in the `unplug` command below. For example, a fresh 0.3.0 install uses `herdr-dev-team`, while a project upgraded from 0.2.0 may use `herdr-dev-team-v0-3-0`.

```sh
dskills unplug --project . herdr-dev-team
dskills plug --project . herdr-dev-team-v0-4-0
dskills status --project .
```

If the old skill is only in the pool, skip `unplug`. Follow the initialization step above if the project is new to dskills. Refresh or restart the Leader session, then start a new six-pack run to load the updated role instructions.

## Use it

Run the Leader in a live Herdr terminal that accepts incoming messages. The team needs a shared local filesystem, Git, Python 3.8 or later on a POSIX host, and access to `hc`. For the documented Pi worker setup, install Pi and configure the models the workers will use.

Give the Leader a target, a pack and the behavior you expect. For example:

> Use Herdr Dev Team six-pack to fix the CSV export. Preserve quoted fields and empty rows. Have QA run the export command and import the resulting file again.

For four-pack and six-pack, the Specifier submits the examples for your approval before the Coder starts. Each role reports its checked commit and evidence. Use `resume` to inspect a run and `recover` to resolve the next handoff after an interruption. Ordinary handoffs can proceed while the Lead is offline.

Six-pack also accepts a project quality plan with `--plan FILE`. Projects choose their tools, define the criteria for each role and select the checks QA must verify on the final commit. Try the Rust historical-file reader from the repository root with `python3 -B tests/try_quality_reader.py`. [Quality plan setup and recovery](skills/herdr-dev-team/references/six-pack.md#use-a-project-quality-plan)

The [legacy Coder/Reviewer workflow](skills/herdr-dev-team/references/handoffs.md#prepare-a-legacy-statejson-run) is also available. Its default Pi setup uses `gpt-6.2-sol` at medium thinking for the Coder and high for the Reviewer. The Coder uses Ponytail full, and the Leader handles final QA with up to five review rounds. Visible mode keeps the Leader's focus and opens a separate `Development` tab with the Coder on the left and Reviewer on the right. Background mode uses Herdr Core. See the [role setup](skills/herdr-dev-team/references/roles.md) and [layout instructions](skills/herdr-dev-team/references/handoffs.md#launch-the-workers).

## Regression tests

Run the same command that GitHub Actions runs on pull requests.

```sh
python3 -B -m unittest discover -s tests -v
```
