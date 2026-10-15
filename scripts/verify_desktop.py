"""验证冻结程序独立于外部 Python，并测试重启保留和单实例。"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop.store import Store
from desktop import VERSION
from desktop.guide import CHAPTERS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "dist" / "LearningCompass")
    parser.add_argument("--report", type=Path, default=ROOT / "release" / "verification.json")
    args = parser.parse_args()
    executable = args.directory.resolve() / "LearningCompass.exe"
    bundled_guide = (executable.parent / "_internal" / "docs" / "desktop-guide.md").read_text(encoding="utf-8")
    assert f"`{VERSION}`" in bundled_guide
    assert all(f"## {chapter}" in bundled_guide for chapter in CHAPTERS.values())
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32")
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONHOME"] = str(ROOT / "nonexistent-python-home")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="learning-compass-verify-") as directory:
        data = Path(directory) / "data"
        command = [str(executable), "--smoke-test", "--data-dir", str(data)]
        subprocess.run(command, env=environment, cwd=directory, timeout=90, check=True)
        first = json.loads((data / "smoke-result.json").read_text(encoding="utf-8"))
        assert first["success"] and first["version"] == VERSION and first["pages"] == 5 and first["tasks"] == 6
        store = Store(data)
        row = store.tasks()[0]
        store.update_task(row["id"], row["task"], row["due_date"], row["remind_at"], "已完成", 12, 2, "冻结程序重启验证")
        subprocess.run(command, env=environment, cwd=directory, timeout=90, check=True)
        assert Store(data).tasks()[0]["note"] == "冻结程序重启验证"
        single_data = Path(directory) / "single"
        process = subprocess.Popen([str(executable), "--data-dir", str(single_data), "--minimized"], env=environment, cwd=directory)
        try:
            deadline = time.monotonic() + 30
            while not (single_data / "instance.lock").exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("首个实例启动失败")
                time.sleep(.1)
            started = time.monotonic()
            subprocess.run([str(executable), "--data-dir", str(single_data), "--minimized"], env=environment, cwd=directory, timeout=15, check=True)
            assert process.poll() is None
            assert time.monotonic() - started < 15
        finally:
            process.terminate()
            process.wait(timeout=15)
        report = dict(version=first["version"], frozen_start=True, external_python_removed=True,
                      restart_preserved=True, single_instance=True, credential_store=first.get("credential_store"),
                      offline_guide_packaged=True, clean_windows_vm=False)
    output = args.report
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
