"""Run the six-worker reader fixture through the CLI and retain its evidence for inspection."""

from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from test_quality_plan import QualityPlanTest, SCRIPT


def main():
    if not shutil.which("rustc"):
        raise SystemExit("The reader trial requires rustc on PATH.")
    directory = Path(tempfile.mkdtemp(prefix="herdr-quality-reader-"))
    print(f"Trial directory: {directory}", flush=True)
    trial = QualityPlanTest()
    trial.prepare_reader(directory)
    view = trial.run_rust_reader_trial()
    print(f"Six-pack {view['phase']} at {view['final_commit']}")
    for name, execution in view["quality"]["final"]["executions"].items():
        print(f"  {name}: {execution}")
    print("Inspect the retained run with")
    print(shlex.join([sys.executable, "-B", str(SCRIPT), str(trial.store), "six", view["id"], "resume"]))


if __name__ == "__main__":
    main()
