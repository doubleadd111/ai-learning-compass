from pathlib import Path

import pytest

from desktop import DISPLAY_VERSION, VERSION, guide_matches_version, version_parts, windows_version, validate_build_version

BASE_VERSION = '.'.join(VERSION.split('.')[:3])


def test_patch_version_and_installer_are_consistent():
    root = Path(__file__).resolve().parents[1]
    assert VERSION == DISPLAY_VERSION == "2026.10.13"
    assert f'#define AppVersion "{BASE_VERSION}"' in (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    legacy = (root / "CHANGELOG-legacy.md").read_text(encoding="utf-8")
    assert "CHANGELOG-legacy.md" in log and "未发布本机补丁" not in log
    assert all(version in legacy for version in ("2026.10.9", "2026.10.8", "2026.10.7", "2026.10.6", "2026.10.5", "2026.10.4", "2026.10.3", "2026.10.2", "2026.10.1", "2026.10.0"))
    assert f'#define AppNumericVersion "{BASE_VERSION}.0"' in (root / 'packaging/installer.iss').read_text(encoding='utf-8')
    assert 'VersionInfoVersion={#AppNumericVersion}' in (root / 'packaging/installer.iss').read_text(encoding='utf-8')
    assert "更新日志.md" in (root / "scripts" / "build_desktop.py").read_text(encoding="utf-8")


def test_local_build_does_not_create_release_artifacts(tmp_path, monkeypatch):
    import sys
    from scripts import build_desktop
    test_version = BASE_VERSION + '.2'
    monkeypatch.setattr(build_desktop, 'VERSION', test_version)
    for path, content in [("docs/desktop-guide.md", f"适用版本：`{test_version}`"), ("THIRD_PARTY_NOTICES.md", "licenses"),
                          ('CHANGELOG-legacy.md', 'legacy log'), ('docs/versioning.md', 'version rules'),
                          ('docs/desktop-verification.md', 'verification'), ('docs/releases/example.md', 'release notes'),
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
    import json
    manifest = json.loads((tmp_path / 'build/local-test-manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == test_version and manifest['kind'] == 'local-test'
    assert json.loads((tmp_path / 'dist/LearningCompass/build-info.json').read_text(encoding='utf-8'))['version'] == test_version
    for relative in ('CHANGELOG-legacy.md', 'CHANGELOG.md', 'docs/versioning.md', 'docs/desktop-guide.md', 'docs/desktop-verification.md', 'docs/releases/example.md'):
        assert (tmp_path/'dist/LearningCompass'/relative).read_bytes() == (tmp_path/relative).read_bytes()


def test_release_manifest_identifies_actual_executable(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys
    from scripts import build_desktop
    monkeypatch.setattr(build_desktop, 'VERSION', BASE_VERSION)
    exe = tmp_path / "dist/LearningCompass/LearningCompass.exe"
    for path, content in [("docs/desktop-guide.md", f"适用版本：`{BASE_VERSION}`"), ("THIRD_PARTY_NOTICES.md", "licenses"),
                          ('CHANGELOG-legacy.md', 'legacy log'), ('docs/versioning.md', 'version rules'),
                          ('docs/desktop-verification.md', 'verification'),
                          ("CHANGELOG.md", "history"), ("dist/LearningCompass/LearningCompass.exe", "fake exe"),
                          ("packaging/installer.iss", f'#define AppVersion "{BASE_VERSION}"\n#define AppNumericVersion "{BASE_VERSION}.0"'),
                          (".tools/InnoSetup/ISCC.exe", "fake compiler")]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(build_desktop, "ROOT", tmp_path)
    monkeypatch.setattr(build_desktop, "source_digest", lambda: "test-source")
    monkeypatch.setattr(build_desktop, "prepare", lambda: {})
    def run(command, **kwargs):
        if "ISCC.exe" in str(command[0]):
            (tmp_path / f"release/LearningCompass-{BASE_VERSION}-windows-x64-setup.exe").write_bytes(b"fake setup")
    monkeypatch.setattr(build_desktop.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["build_desktop.py", "--iscc", str(tmp_path / ".tools/InnoSetup/ISCC.exe")])
    build_desktop.main()
    manifest = json.loads((tmp_path / "release/build-manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "release" and manifest["version"] == BASE_VERSION
    assert manifest["exe_sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest()
    assert len(manifest["artifacts"]) == 2


def test_installer_qa_has_separate_identity_and_no_real_shortcuts():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "packaging/installer.iss").read_text(encoding="utf-8")
    assert "#ifdef QAOutputDir\n" in installer
    assert "AFC764B4-94E8-4F93-A358-62B12B2794C2" in installer
    assert "DB874C52-324E-4378-92A9-FD395B66E96F" in installer
    assert "#ifndef QAOutputDir\nName: \"{autoprograms}" in installer
    script = (root / "scripts/verify_installer.py").read_text(encoding="utf-8")
    assert "DB874C52-324E-4378-92A9-FD395B66E96F" in script
    assert "AFC764B4-94E8-4F93-A358-62B12B2794C2" not in script


@pytest.mark.parametrize('value,expected', [
    ('2026.10.11', (2026, 10, 11, 0)),
    ('2026.10.11.1', (2026, 10, 11, 1)),
    ('2026.10.11.2', (2026, 10, 11, 2)),
    ('2026.10.12', (2026, 10, 12, 0)),
    ('2026.11.1', (2026, 11, 1, 0)),
    ('2027.1.1.2', (2027, 1, 1, 2)),
    ('2026.12.65535.65535', (2026, 12, 65535, 65535)),
])
def test_full_year_usable_and_test_versions(value, expected):
    assert windows_version(value) == expected
    assert len(windows_version(value)) == 4
    assert version_parts(value) == expected[:len(value.split('.'))]
    assert validate_build_version(value, local_test=len(value.split('.')) == 4) == expected


@pytest.mark.parametrize('value', [
    '26.10.11', '2026.0.1', '2026.13.1', '2026.01.1', '2026.10.0',
    '2026.10.1.0', '2026.10.01', '2026.10.1.02', '2026.10.1.1.1',
    '2026.10.65536', '2026.10.1.65536', '2026.10', '2026.10.1-beta',
    ' 2026.10.1', '2026.10.1\n', '２０２６.10.1',
])
def test_reject_invalid_new_version_numbers(value):
    with pytest.raises(ValueError):
        version_parts(value)


@pytest.mark.parametrize('value,local_test', [('2026.10.12', True), ('2026.10.12.2', False)])
def test_wrong_build_kind_rejected_before_any_files_or_build(tmp_path, monkeypatch, value, local_test):
    import sys
    from scripts import build_desktop
    monkeypatch.setattr(build_desktop, 'ROOT', tmp_path)
    monkeypatch.setattr(build_desktop, 'VERSION', value)
    monkeypatch.setattr(build_desktop, 'prepare', lambda: pytest.fail('must reject before preparing'))
    monkeypatch.setattr(sys, 'argv', ['build_desktop.py'] + (['--local-test'] if local_test else []))
    with pytest.raises(ValueError, match='三段|四段'):
        build_desktop.main()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('numeric,display', [('2026.10.12.1', BASE_VERSION), (BASE_VERSION+'.0', '2026.10.11')])
def test_installer_mismatch_rejected_before_build(tmp_path, monkeypatch, numeric, display):
    import sys
    from scripts import build_desktop
    monkeypatch.setattr(build_desktop, 'VERSION', BASE_VERSION)
    (tmp_path/'packaging').mkdir()
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/desktop-guide.md').write_text(f'适用版本：`{BASE_VERSION}`', encoding='utf-8')
    (tmp_path/'packaging/installer.iss').write_text(f'#define AppVersion "{display}"\n#define AppNumericVersion "{numeric}"', encoding='utf-8')
    monkeypatch.setattr(build_desktop, 'ROOT', tmp_path)
    monkeypatch.setattr(build_desktop, 'prepare', lambda: pytest.fail('must reject before preparing'))
    monkeypatch.setattr(sys, 'argv', ['build_desktop.py'])
    with pytest.raises(RuntimeError, match='安装器'):
        build_desktop.main()


def test_guide_version_mismatch_rejected_before_build(tmp_path, monkeypatch):
    import sys
    from scripts import build_desktop
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/desktop-guide.md').write_text('适用版本：`2026.10.12`\n举例：测试版为`2026.10.12.1`', encoding='utf-8')
    monkeypatch.setattr(build_desktop, 'ROOT', tmp_path)
    monkeypatch.setattr(build_desktop, 'VERSION', '2026.10.12.1')
    monkeypatch.setattr(build_desktop, 'prepare', lambda: pytest.fail('must reject before preparing'))
    monkeypatch.setattr(sys, 'argv', ['build_desktop.py', '--local-test'])
    with pytest.raises(RuntimeError, match='指南'):
        build_desktop.main()


def test_guide_version_must_match_header_not_an_example():
    from desktop.guide import guide_sections
    root = Path(__file__).resolve().parents[1]
    content = (root/'docs/desktop-guide.md').read_text(encoding='utf-8')
    assert guide_matches_version(content, VERSION)
    assert not guide_matches_version(content, VERSION+'.1')
    assert guide_sections()['build'][0] == '从源码运行与构建'


def test_version_docs_and_changelog_links_are_local_and_real():
    import re
    root = Path(__file__).resolve().parents[1]
    doc = (root/'docs/versioning.md').read_text(encoding='utf-8')
    assert '2026.10.11.2' in doc and '2026.11.1' in doc and '完整' in doc
    for name in ('CHANGELOG.md', 'CHANGELOG-legacy.md'):
        content=(root/name).read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)]+)\)', content):
            if not target.startswith(('https://','http://','#')):
                assert (root/target.split('#')[0]).is_file(), target


def test_release_13_has_merged_test_history_and_matching_user_docs():
    root=Path(__file__).resolve().parents[1]
    log=(root/'CHANGELOG.md').read_text(encoding='utf-8')
    assert log.index('## 2026.10.13 ·') < log.index('## 2026.10.12 ·')
    assert '测试迭代（均已合并至2026.10.13）' in log
    assert '2026.10.12.1' in log and '2026.10.12.2' in log
    assert '待合并' not in log and '待验收' not in log
    notes=(root/'docs/releases/2026.10.13.md').read_text(encoding='utf-8')
    assert 'schema8' in notes and '格式7' in notes and '不混入某一天' in notes
    assert '2026.10.13-windows-x64-setup.exe' in notes
    assert '2026.10.13-windows-x64-portable.zip' in notes
    assert '2026.10.13' in (root/'README.md').read_text(encoding='utf-8')
