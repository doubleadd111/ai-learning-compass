"""Project license choice and future packaging; no real builds or network."""
import hashlib
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_standard_gpl_text_and_project_only_version_choice():
    license_bytes=(ROOT/'LICENSE').read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == 'fb981668c18a279e285fc4d83fba1e836cc84dd4daa73c9697d3cfd2d8aca6e0'
    assert '/LICENSE text eol=lf' in (ROOT/'.gitattributes').read_text(encoding='utf-8')
    notice=(ROOT/'COPYRIGHT').read_text(encoding='utf-8')
    assert 'Copyright (C) 2026 doubleadd111' in notice
    assert 'SPDX-License-Identifier: GPL-3.0-only' in notice
    assert 'or any later version' not in notice
    assert 'Third-party components retain their own copyright' in notice
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    assert '[LICENSE](LICENSE)' in readme and '[COPYRIGHT](COPYRIGHT)' in readme
    assert 'GPL-3.0-only' in readme and '不改写此前已发布标签或安装包' in readme
    assert '不是MIT双许可' in readme


@pytest.mark.parametrize('missing',['LICENSE','COPYRIGHT'])
def test_missing_project_legal_files_stop_before_build(tmp_path,monkeypatch,missing):
    import sys
    from scripts import build_desktop
    version='2026.10.13.1'
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/desktop-guide.md').write_text(f'适用版本：`{version}`',encoding='utf-8')
    for name in {'LICENSE','COPYRIGHT'}-{missing}:
        (tmp_path/name).write_text('test notice',encoding='utf-8')
    monkeypatch.setattr(build_desktop,'ROOT',tmp_path)
    monkeypatch.setattr(build_desktop,'VERSION',version)
    monkeypatch.setattr(build_desktop,'prepare',lambda:pytest.fail('missing notice must reject before preparing'))
    monkeypatch.setattr(sys,'argv',['build_desktop.py','--local-test'])
    with pytest.raises(RuntimeError,match=missing):build_desktop.main()
    assert not (tmp_path/'dist').exists() and not (tmp_path/'release').exists()


@pytest.mark.parametrize('changed',['LICENSE','COPYRIGHT'])
def test_legal_changes_invalidate_source_digest(tmp_path,monkeypatch,changed):
    from scripts import build_desktop
    names=('LICENSE','COPYRIGHT','docs/desktop-guide.md','run_desktop.py','models.py','planner.py',
           'curriculum.py','progress_store.py','weekly_review.py','plan_transfer.py')
    for name in names:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture',encoding='utf-8')
    monkeypatch.setattr(build_desktop,'ROOT',tmp_path)
    before=build_desktop.source_digest()
    (tmp_path/changed).write_text('changed legal notice',encoding='utf-8')
    assert before!=build_desktop.source_digest()
