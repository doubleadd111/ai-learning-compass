"""Updater integration against fake installation paths, never real programs."""
from pathlib import Path
import hashlib,json,sys
from types import SimpleNamespace
import pytest
from scripts import install_local_test as updater
from desktop import backup_metadata
from desktop.store import Store
from tests.test_desktop_store import create


def fake_install(tmp_path,monkeypatch):
    root=tmp_path/'project';appdata=tmp_path/'appdata'
    target=appdata/'Programs/LearningCompass';target.mkdir(parents=True)
    old=b'old-program';new=b'new-test-program'
    (target/'LearningCompass.exe').write_bytes(old)
    source=root/'dist/LearningCompass';source.mkdir(parents=True)
    (source/'LearningCompass.exe').write_bytes(new)
    (root/'build').mkdir();(root/'build/local-test-manifest.json').write_text(json.dumps(dict(
        version=updater.VERSION,kind='local-test',source_sha256='fixture-source',exe_sha256=hashlib.sha256(new).hexdigest())),encoding='utf-8')
    known=root/'.learning_compass/qa/known-local-builds';known.mkdir(parents=True)
    (known/'old.json').write_text(json.dumps(dict(kind='local-test',exe_sha256=hashlib.sha256(old).hexdigest())),encoding='utf-8')
    s=Store(appdata/'LearningCompass');create(s)
    monkeypatch.setattr(updater,'ROOT',root);monkeypatch.setenv('LOCALAPPDATA',str(appdata))
    monkeypatch.setattr(updater,'source_digest',lambda:'fixture-source')
    def no_registration(*a,**kw):raise FileNotFoundError()
    monkeypatch.setattr(updater.winreg,'OpenKey',no_registration)
    monkeypatch.setattr(updater.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='',returncode=0))
    monkeypatch.setattr(sys,'argv',['install_local_test.py'])
    return s,target,old,new


def test_updater_writes_guard_metadata_before_replacing_fake_program(tmp_path,monkeypatch):
    s,target,old,new=fake_install(tmp_path,monkeypatch)
    before=updater.database_fingerprint(s.path);updater.main()
    p=next(s.backups.glob('pre-update-*.sqlite3'));r=backup_metadata.description(p,10)
    assert r['kind']=='before_update' and r['category']=='guard' and r['version']==updater.VERSION
    assert r['target_version']==updater.VERSION and r['source_app_version'] is None
    assert (target/'LearningCompass.exe').read_bytes()==new
    assert updater.database_fingerprint(s.path)==before


def test_updater_metadata_failure_keeps_fake_program_and_data(tmp_path,monkeypatch):
    s,target,old,new=fake_install(tmp_path,monkeypatch);before=updater.database_fingerprint(s.path)
    def fail(*a,**kw):raise OSError('cannot save description')
    monkeypatch.setattr(backup_metadata,'record',fail)
    with pytest.raises(OSError):updater.main()
    assert (target/'LearningCompass.exe').read_bytes()==old
    assert updater.database_fingerprint(s.path)==before
    assert list(s.backups.glob('pre-update-*.sqlite3'))
