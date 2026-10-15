from pathlib import Path

from desktop import DISPLAY_VERSION, VERSION


def test_patch_version_and_installer_are_consistent():
    root = Path(__file__).resolve().parents[1]
    assert VERSION == DISPLAY_VERSION == "2026.10.7"
    assert f'#define AppVersion "{VERSION}"' in (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert all(version in log for version in ("2026.10.7", "2026.10.6", "2026.10.5", "2026.10.4", "2026.10.3", "2026.10.2", "2026.10.1", "2026.10.0"))
    assert "更新日志.md" in (root / "scripts" / "build_desktop.py").read_text(encoding="utf-8")


def test_local_build_does_not_create_release_artifacts(tmp_path, monkeypatch):
    import sys
    from scripts import build_desktop
    for path, content in [("docs/desktop-guide.md", "guide"), ("THIRD_PARTY_NOTICES.md", "licenses"),
                          ("CHANGELOG.md", "history"), ("dist/LearningCompass/LearningCompass.exe", "test exe")]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    monkeypatch.setattr(build_desktop, "source_digest", lambda: "test-hash")
    monkeypatch.setattr(build_desktop, "prepare", lambda: {})
    calls = []
    monkeypatch.setattr(build_desktop.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(sys, "argv", ["build_desktop.py", "--local-test"])
    build_desktop.main()
    assert len(calls) == 1 and "PyInstaller" in calls[0]
    assert not (tmp_path / "release").exists()
    assert (tmp_path / "dist/LearningCompass/更新日志.md").is_file()
    assert (tmp_path / "build/local-test-manifest.json").is_file()
