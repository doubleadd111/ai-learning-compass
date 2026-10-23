"""只更新当前用户的已安装程序；先备份，不卸载、不读取系统凭据。"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
import winreg
from contextlib import closing
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop import VERSION
from scripts.build_desktop import source_digest


def database_fingerprint(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        tables = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for name, sql in tables:
            digest.update(repr((name, sql)).encode("utf-8"))
            quoted = name.replace('"', '""')
            for row in connection.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid'):
                digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="使用经过验证的正式发布构建清单")
    args = parser.parse_args()
    manifest_path = ROOT / ("release/build-manifest.json" if args.release else "build/local-test-manifest.json")
    build_kind = "release" if args.release else "local-test"
    target = (Path(os.environ["LOCALAPPDATA"]) / "Programs" / "LearningCompass").resolve()
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{AFC764B4-94E8-4F93-A358-62B12B2794C2}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as registry:
            registered = Path(winreg.QueryValueEx(registry, "InstallLocation")[0]).resolve()
    except FileNotFoundError:
        # 缺少卸载登记时，只接受在本地验证记录或原发布包中可核对的程序摘要。
        actual = hashlib.sha256((target / "LearningCompass.exe").read_bytes()).hexdigest()
        known = []
        for record in (ROOT / ".learning_compass" / "qa" / "known-local-builds").glob("*.json"):
            data = json.loads(record.read_text(encoding="utf-8"))
            if data.get("kind") in {"local-test", "release"}:
                known.append(data.get("exe_sha256"))
        legacy = ROOT / "release" / "LearningCompass-2026.10.0-windows-x64-portable.zip"
        if legacy.is_file():
            with zipfile.ZipFile(legacy) as archive:
                known.append(hashlib.sha256(archive.read("LearningCompass/LearningCompass.exe")).hexdigest())
        if actual not in known:
            raise RuntimeError("缺少安装登记且程序与原发布包不符，停止自动覆盖。") from None
        registered = target
    if registered != target or not (target / "LearningCompass.exe").is_file():
        raise RuntimeError("安装位置与预期不一致，停止自动覆盖。")
    running = subprocess.run(["pwsh", "-NoProfile", "-Command",
                              "Get-Process LearningCompass -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
                             capture_output=True, text=True, check=False)
    if running.stdout.strip():
        raise RuntimeError("请先从托盘完全退出学习罗盘，再更新；不强制终止以免丢失记录。")
    source = (ROOT / "dist" / "LearningCompass").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("kind") != build_kind or manifest["version"] != VERSION or manifest["source_sha256"] != source_digest():
        raise RuntimeError("测试构建与当前源码不一致，停止更新。")
    if hashlib.sha256((source / "LearningCompass.exe").read_bytes()).hexdigest() != manifest["exe_sha256"]:
        raise RuntimeError("测试程序校验不通过，停止更新。")
    database = (Path(os.environ["LOCALAPPDATA"]) / "LearningCompass" / "learning-compass.sqlite3").resolve()
    before = database_fingerprint(database)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    recovery = ROOT / ".learning_compass" / "qa" / f"update-{VERSION}-{timestamp}"
    recovery.mkdir(parents=True)
    shutil.copytree(target, recovery / "previous-program")
    if before is not None:
        backup = database.parent / "backups" / f"pre-update-{VERSION}-{timestamp}.sqlite3"
        backup.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(backup)) as copy:
                original.backup(copy)
        if database_fingerprint(backup) != before:
            raise RuntimeError("数据库备份校验失败，停止更新。")
        from desktop.backup_metadata import record
        record(backup, 'before_update', app_version=VERSION, target_version=VERSION)
    # 仅复制到核对过的确切安装目录，不删除学习目录，不改变凭据或卸载登记。
    shutil.copytree(source, target, dirs_exist_ok=True)
    if hashlib.sha256((target / "LearningCompass.exe").read_bytes()).hexdigest() != manifest["exe_sha256"]:
        raise RuntimeError("覆盖后校验失败，旧程序保存在恢复目录中。")
    if database_fingerprint(database) != before:
        raise RuntimeError("学习数据在更新期间发生变化，请先检查记录，不自动覆盖用户数据。")
    known_directory = ROOT / ".learning_compass" / "qa" / "known-local-builds"
    known_directory.mkdir(exist_ok=True)
    shutil.copy2(manifest_path, known_directory / f"{VERSION}-{manifest['exe_sha256'][:12]}.json")
    report = dict(version=VERSION, kind=build_kind, target=str(target), recovery=str(recovery),
                  data_unchanged=True, credentials_untouched=True, exe_sha256=manifest["exe_sha256"],
                  source_sha256=manifest["source_sha256"], updated_at=datetime.now().isoformat())
    (recovery / "update-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
