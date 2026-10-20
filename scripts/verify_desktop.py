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
    parser.add_argument("--native-guide", action="store_true", help="在隔离数据中检查Windows指南窗口标志与恢复")
    parser.add_argument("--input-scope", action="store_true", help="在隔离数据中检查全部输入控件与滚轮范围")
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
        if args.input_scope:
            command.append('--input-scope-check')
        subprocess.run(command, env=environment, cwd=directory, timeout=90, check=True)
        first = json.loads((data / "smoke-result.json").read_text(encoding="utf-8"))
        assert first["success"] and first["version"] == VERSION and first["pages"] == 5 and first["tasks"] == 6
        assert first["plan_deletion"]["success"]
        assert first["plan_history"]["success"]
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
        report["plan_deletion"] = first["plan_deletion"]
        report["plan_history"] = first["plan_history"]
        if args.input_scope:
            assert first['input_scope']['success']
            report['input_scope'] = first['input_scope']
        if args.native_guide:
            native = Path(directory) / "native"
            native_environment = dict(environment, QT_QPA_PLATFORM="windows")
            native_command = [str(executable), "--smoke-test", "--data-dir", str(native), "--guide-window-check"]
            if args.input_scope:
                native_command.append('--input-scope-check')
            subprocess.run(native_command,
                           env=native_environment, cwd=directory, timeout=120, check=True)
            native_report = json.loads((native / "smoke-result.json").read_text(encoding="utf-8"))
            report["native_guide_window"] = native_report["native_guide_window"]
            report["modal_guide"] = native_report["modal_guide"]
            report["guide_lifecycle"] = native_report["guide_lifecycle"]
            assert native_report["plan_deletion"]["success"]
            report["native_plan_deletion"] = native_report["plan_deletion"]
            assert native_report["plan_history"]["success"]
            report["native_plan_history"] = native_report["plan_history"]
            if args.input_scope:
                assert native_report['input_scope']['success']
                report['native_input_scope'] = native_report['input_scope']
    output = args.report
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
