"""构建不包含 .env、用户数据、测试或开发环境的 Windows 发布产物。"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution, version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop import VERSION


def source_digest():
    paths = sorted((ROOT / "desktop").glob("*.py")) + [ROOT / name for name in ("run_desktop.py", "models.py", "planner.py", "curriculum.py", "progress_store.py", "weekly_review.py", "plan_transfer.py")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare():
    from PySide6.QtWidgets import QApplication
    from desktop.theme import compass_icon
    app = QApplication.instance() or QApplication([])
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    if not compass_icon(256).pixmap(256, 256).save(str(build / "compass.ico"), "ICO"):
        raise RuntimeError("无法生成软件图标")
    (build / "version-info.txt").write_text("""VSVersionInfo(
      ffi=FixedFileInfo(filevers=(2026,10,0,0), prodvers=(2026,10,0,0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
      kids=[StringFileInfo([StringTable('080404b0', [StringStruct('CompanyName','doubleadd111'),
        StringStruct('FileDescription','学习罗盘'), StringStruct('FileVersion','2026.10.0'),
        StringStruct('ProductName','学习罗盘'),StringStruct('ProductVersion','2026.10.0')])]),
        VarFileInfo([VarStruct('Translation',[2052,1200])])])""", encoding="utf-8")
    licenses = build / "licenses"
    licenses.mkdir(exist_ok=True)
    packages = ["PySide6_Essentials", "shiboken6", "openai", "pydantic", "pydantic_core", "keyring", "platformdirs", "httpx", "httpcore", "certifi", "anyio", "typing_extensions", "jiter", "socksio", "jaraco.classes", "jaraco.context", "jaraco.functools", "more-itertools", "pywin32-ctypes"]
    for package in packages:
        dist = distribution(package)
        destination = licenses / package
        destination.mkdir(exist_ok=True)
        for item in dist.files or []:
            if any(part.lower() in {"licenses", "license", "license.txt", "license.md", "copying", "copying.txt"} for part in item.parts):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = destination / str(item).replace("/", "_").replace("\\", "_")
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    return {package: version(package) for package in packages}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path, default=ROOT / ".tools" / "InnoSetup" / "ISCC.exe")
    args = parser.parse_args()
    source_hash = source_digest()
    installed = prepare()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "packaging" / "desktop.spec")], cwd=ROOT, check=True)
    shutil.copy2(ROOT / "docs" / "desktop-guide.md", ROOT / "dist" / "LearningCompass" / "使用说明.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", ROOT / "dist" / "LearningCompass" / "THIRD_PARTY_NOTICES.md")
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    portable = release / f"LearningCompass-{VERSION}-windows-x64-portable.zip"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted((ROOT / "dist" / "LearningCompass").rglob("*")):
            if file.is_file():
                archive.write(file, Path("LearningCompass") / file.relative_to(ROOT / "dist" / "LearningCompass"))
    if not args.iscc.is_file():
        raise RuntimeError("解压即用版已生成。安装包还需要 Inno Setup 的 ISCC.exe，请用 --iscc 指定路径。")
    subprocess.run([str(args.iscc.resolve()), "/Q", str(ROOT / "packaging" / "installer.iss")], cwd=ROOT, check=True)
    if source_digest() != source_hash:
        raise RuntimeError("构建期间源码发生变化，请在代码稳定后重新构建，以保证产物与源码一致。")
    manifest = {"version": VERSION, "source_sha256": source_hash, "python": sys.version.split()[0], "dependencies": installed, "artifacts": {}}
    for file in [portable, release / f"LearningCompass-{VERSION}-windows-x64-setup.exe"]:
        manifest["artifacts"][file.name] = {"size": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    (release / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (release / "SHA256SUMS.txt").write_text("\n".join(f"{data['sha256']}  {name}" for name, data in manifest["artifacts"].items()) + "\n", encoding="utf-8")
    print(f"Build complete: {release}")


if __name__ == "__main__":
    main()
