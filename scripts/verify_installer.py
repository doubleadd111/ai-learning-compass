"""隔离目录安装、覆盖升级与卸载验证；不触碰正式学习数据。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop import VERSION
from desktop.store import Store


def run_hidden(command, **kwargs):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.run(command, startupinfo=startup, check=True, timeout=180, **kwargs)


def main():
    uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{AFC764B4-94E8-4F93-A358-62B12B2794C2}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key):
            raise RuntimeError("已存在正式安装，跳过安装包自动验收，避免覆盖。")
    except FileNotFoundError:
        pass
    work = ROOT / ".learning_compass" / "qa" / ("installer-" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    install = work / "installed"
    data = work / "data"
    setup = ROOT / "release" / f"LearningCompass-{VERSION}-windows-x64-setup.exe"
    command = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
               f"/DIR={install}", "/GROUP=LearningCompass-QA-2026.10", "/TASKS=",
               f"/LOG={work / 'install.log'}"]
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32")
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONHOME"] = str(work / "no-python")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    run_hidden(command)
    executable = install / "LearningCompass.exe"
    assert executable.is_file()
    smoke = [str(executable), "--smoke-test", "--data-dir", str(data)]
    run_hidden(smoke, env=environment, cwd=work)
    task = Store(data).tasks()[0]
    Store(data).update_task(task["id"], task["task"], task["due_date"], task["remind_at"],
                            "已完成", 18, 2, "安装升级卸载保留检查")
    run_hidden(command)
    run_hidden(smoke, env=environment, cwd=work)
    assert Store(data).tasks()[0]["note"] == "安装升级卸载保留检查"
    uninstaller = (install / "unins000.exe").resolve()
    assert uninstaller.parent == install.resolve() and uninstaller.is_file()
    run_hidden([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                f"/LOG={work / 'uninstall.log'}"])
    assert not executable.exists()
    assert Store(data).tasks()[0]["note"] == "安装升级卸载保留检查"
    output = ROOT / "release" / "verification.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    report.update(per_user_install=True, reinstall_upgrade=True, uninstall_preserved_data=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
