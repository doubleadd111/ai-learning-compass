"""Compare frozen HTTPS paths without credentials or model requests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--report", type=Path, default=ROOT / ".learning_compass/qa/anonymous-2026.10.5.json")
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="learning-anonymous-") as temporary:
        for mode in ("system", "direct"):
            directory = Path(temporary) / mode
            command = [str(args.executable.resolve())] if args.executable else [sys.executable, str(ROOT / "run_desktop.py")]
            command += ["--anonymous-network-check", "--diagnostic-route", mode, "--data-dir", str(directory)]
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            if args.executable:
                environment["PATH"] = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32")
                environment.pop("PYTHONPATH", None)
                environment.pop("VIRTUAL_ENV", None)
            process = subprocess.run(command, env=environment, cwd=ROOT, timeout=90)
            result = json.loads((directory / "anonymous-check.json").read_text(encoding="utf-8"))
            result["exit_code"] = process.returncode
            results.append(result)
    report = dict(frozen=bool(args.executable), credentials_accessed=False, generation_requests=0, results=results)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
