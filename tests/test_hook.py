"""on-exit_gittw.py against a throwaway task dir (TW_TASK_DIR); never touches ~/.task."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / 'on-exit_gittw.py'
TASK = json.dumps({'uuid': 'a', 'description': 'Buy milk', 'status': 'pending'})


@pytest.fixture
def task_dir(tmp_path, monkeypatch):
    d = tmp_path / 'task'
    (d / 'config').mkdir(parents=True)
    env = {'GIT_CONFIG_GLOBAL': str(tmp_path / 'gitconfig'), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@x.invalid',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@x.invalid', 'TW_TASK_DIR': str(d)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    subprocess.run(['git', 'init', '-q', str(d)], check=True)
    (d / 'pending.data').write_text('one\n')
    subprocess.run(['git', '-C', str(d), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(d), 'commit', '-qm', 'init'], check=True)
    return d


def commits(d):
    return int(subprocess.run(['git', '-C', str(d), 'rev-list', '--count', 'HEAD'],
                              capture_output=True, text=True).stdout)


def run_hook(stdin):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True)


def change(d):
    (d / 'pending.data').write_text((d / 'pending.data').read_text() + 'more\n')


def test_write_is_committed_by_default(task_dir):
    change(task_dir)
    r = run_hook(TASK + '\n')
    assert r.returncode == 0 and r.stdout == '' and commits(task_dir) == 2
    msg = subprocess.run(['git', '-C', str(task_dir), 'log', '-1', '--format=%s'],
                         capture_output=True, text=True).stdout.strip()
    assert msg == 'task: Buy milk'


def test_read_only_command_does_nothing(task_dir):
    change(task_dir)
    run_hook('')
    assert commits(task_dir) == 1


@pytest.mark.parametrize('value', ['no', 'off', 'false', '0', 'No  # paused for now'])
def test_autocommit_off_in_config(task_dir, value):
    (task_dir / 'config' / 'gittw.rc').write_text(f'# gittw\ngittw.autocommit = {value}\n')
    change(task_dir)
    r = run_hook(TASK + '\n')
    assert r.returncode == 0 and commits(task_dir) == 1


def test_autocommit_yes_in_config(task_dir):
    (task_dir / 'config' / 'gittw.rc').write_text('gittw.autocommit=yes\n')
    change(task_dir)
    run_hook(TASK + '\n')
    assert commits(task_dir) == 2


def test_skips_mid_merge(task_dir):
    (task_dir / '.git' / 'MERGE_HEAD').write_text('x')
    change(task_dir)
    run_hook(TASK + '\n')
    assert commits(task_dir) == 1


def test_never_fails_taskwarrior(tmp_path, monkeypatch):
    monkeypatch.setenv('TW_TASK_DIR', str(tmp_path / 'missing'))
    r = run_hook(TASK + '\n')
    assert r.returncode == 0 and r.stdout == ''
