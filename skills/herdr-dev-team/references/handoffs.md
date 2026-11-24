# Launch local pack workers

The Delivery Lead and workers use interactive terminals on the same Herdr Core and share a filesystem. Each worker needs a distinct Core terminal ID and access to the dedicated Git worktree and private project store. The helper delivers assignments and receipts to the recorded terminal IDs with `hc send --json`.

## Launch the workers

Run `hc --skill` and, for a visible run, `herdr-app --skill` for the installed versions. Use the provider, model and thinking level selected for each role. Start one interactive Pi process per worker with a unique `hc` name. When Core cannot find a Node-managed Pi, pass the resolved absolute paths to Node and the Pi entry point in place of `pi`.

### Visible in Herdr App

1. In `herdr-app ls`, find the terminal matching the Lead's `HC_TERMINAL_ID` in `spaces[].tabs[].terminals[]`. Set `SPACE_ID` to that terminal's enclosing space ID.
2. Start the first worker with `--no-parent` and `herdr.space` to create a worker tab. Save the `address` returned by `hc` as the worker's terminal ID.

   ```sh
   hc run --bg --json --no-parent --name "$FIRST_NAME" --cwd "$WORKTREE" \
     --label "herdr.space=$SPACE_ID" \
     pi --provider "$PROVIDER" --model "$FIRST_MODEL" --thinking "$FIRST_THINKING"
   ```

3. Inspect `herdr-app ls` and set `WORKER_TAB_ID` to the actual tab ID enclosing the first worker. Rename it as desired and use both labels to launch each remaining role into that tab. Set the sample variables for each launch.

   ```sh
   herdr-app do tab.rename --id "$WORKER_TAB_ID" --name "$PACK_TAB_NAME"
   hc run --bg --json --no-parent --name "$WORKER_NAME" --cwd "$WORKTREE" \
     --label "herdr.space=$SPACE_ID" --label "herdr.tab=$WORKER_TAB_ID" \
     pi --provider "$PROVIDER" --model "$WORKER_MODEL" --thinking "$WORKER_THINKING"
   ```

4. Verify the expected two, four or six IDs occupy distinct panes in the worker tab and each Pi session is ready. Use the returned terminal IDs as the role arguments to `handoff.py`. Reuse those terminals for later assignments.

### Background in Core

Use the same `hc run --bg --json --no-parent` command with the layout labels omitted. Check each worker's returned ID and Pi readiness in Core. For either layout, if a launch response is lost, find the original terminal by its unique name before retrying.

### Set up Ponytail for the six-pack Coder

Use the Coder's project or user skill path if Pi already discovers Ponytail. For a dskills-managed project, `dskills --json status --project "$WORKTREE"` shows the pin and link. If a launch needs an explicit skill, verify `SKILL.md` in the pinned copy under the local dskills pool's `objects/<digest>` directory and append `--skill "$PONYTAIL"` with its absolute path to the Coder's Pi arguments. Confirm Pi loaded it and use Ponytail full for the Coder assignment.
