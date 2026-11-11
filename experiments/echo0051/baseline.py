"""Run the frozen evaluator and audit regressions against the untouched pre-audit runtime."""

import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = "be28878"
FROZEN = "b2ae6ea"


def archive(folder, revision, *paths):
    data = subprocess.check_output(
        ["git", "archive", "--format=zip", revision, *paths], cwd=ROOT
    )
    # Both revision and paths are fixed project inputs.
    with ZipFile(io.BytesIO(data)) as snapshot:
        snapshot.extractall(folder)


def compare(folder):
    archive(folder, BASE, "echo_memory", "echo_reflect", "experiments", "tests", "data")
    archive(folder, FROZEN, "experiments/echo0051", "tests/test_echo0051.py")
    out = folder / "evaluation.json"
    subprocess.run(
        [sys.executable, "-m", "experiments.echo0051.holdout", "--output", str(out)],
        cwd=folder,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    result = json.loads(out.read_text(encoding="utf8"))
    regression = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_echo0051.py",
            "-q",
        ],
        cwd=folder,
        capture_output=True,
    )
    # Publish names only. Tracebacks include the private host's temporary paths.
    errors = re.findall(
        r"^(FAIL|ERROR): (test_\w+)", regression.stderr.decode(errors="replace"), re.M
    )
    if regression.returncode and not errors:
        raise RuntimeError("Baseline regressions did not run")
    return dict(
        runtime=BASE,
        evaluator=FROZEN,
        semantic=result["semantic"]["totals"],
        p50_ms=result["semantic"]["p50_ms"],
        p95_ms=result["semantic"]["p95_ms"],
        groups=result["semantic"]["groups"],
        expressions={arm: r["correct"] for arm, r in result["expression"].items()},
        development_regressions=errors,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        result = compare(Path(tmp))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    print(result["semantic"], result["development_regressions"])
