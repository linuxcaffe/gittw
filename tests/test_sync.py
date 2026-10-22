"""gittw sync between two stations: clones of a throwaway bare remote, real `task` (hooks off,
gc off, as on a phone) and real `gittw sync`. Never touches ~/.task or the network."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITTW = ROOT / 'gittw.py'
spec = importlib.util.spec_from_file_location('gittw', GITTW)
gittw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gittw)

pytestmark = pytest.mark.skipif(not shutil.which('task'), reason='needs task')


def run(argv, env, cwd=None):
    r = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, stdin=subprocess.DEVNULL)
    return r


class Station:
    def __init__(self, name, path, env):
        self.name, self.path = name, path
        self.env = dict(env, TW_TASK_DIR=str(path))

    def task(self, *args):
        r = run(['task', f'rc.data.location={self.path}', *args], self.env)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def gittw(self, *args, ok=True):
        r = run([sys.executable, str(GITTW), *args], self.env)
        if ok:
            assert r.returncode == 0, r.stdout + r.stderr
        return r

    def git(self, *args):
        return run(['git', '-C', str(self.path), *args], self.env).stdout

    def tasks(self, *filt):
        out = self.task('rc.gc=off', *filt, 'export')
        return json.loads(out) if out.strip() else []

    def descriptions(self, *filt):
        return sorted(t['description'] for t in self.tasks(*filt))

    def uuids_in(self, name):
        return [gittw.UUID_RE.search(l).group(1) for l in (self.path / name).read_text().splitlines()
                if gittw.UUID_RE.search(l)] if (self.path / name).exists() else []


@pytest.fixture
def stations(tmp_path):
    rc = tmp_path / 'taskrc'
    rc.write_text('hooks=off\nconfirmation=no\ngc=off\nverbose=nothing\n')
    env = dict(os.environ, TASKRC=str(rc), HOME=str(tmp_path),
               GIT_CONFIG_GLOBAL=str(tmp_path / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
               GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@x.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@x.invalid')
    env.pop('TASKDATA', None)
    remote = tmp_path / 'remote.git'
    run(['git', 'init', '-q', '--bare', '-b', 'master', str(remote)], env)
    a = Station('a', tmp_path / 'a', env)
    run(['git', 'clone', '-q', str(remote), str(a.path)], env)
    (a.path / '.gitignore').write_text('local.rc\nundo.data\nbacklog.data\n*.lock\n')
    (a.path / 'local.rc').write_text('gittw.device = a\n')
    a.task('add', '--', 'seed task')
    a.gittw('setup')
    run(['git', '-C', str(a.path), 'add', '-A'], env)
    run(['git', '-C', str(a.path), 'commit', '-qm', 'seed'], env)
    run(['git', '-C', str(a.path), 'push', '-q', '-u', 'origin', 'master'], env)
    b = Station('b', tmp_path / 'b', env)
    run(['git', 'clone', '-q', str(remote), str(b.path)], env)
    (b.path / 'local.rc').write_text('gittw.device = b\n')
    return a, b


def no_conflict_markers(st):
    for name in gittw.DATA_FILES:
        p = st.path / name
        if p.exists():
            assert '<<<<<<<' not in p.read_text(), f'{st.name}:{name} has conflict markers'


def test_both_stations_add_a_task_and_both_end_up_with_both(stations):
    a, b = stations
    a.task('add', '--', 'added on a')
    b.task('add', '--', 'added on b')
    a.gittw('sync')
    r = b.gittw('sync')
    a.gittw('sync')
    for st in (a, b):
        no_conflict_markers(st)
        assert st.descriptions() == ['added on a', 'added on b', 'seed task']
    assert a.task('rc.gc=off', '_get', '1.description').strip() == 'seed task', 'ids on a stay put'


def test_the_same_task_changed_on_both_keeps_the_later(stations):
    a, b = stations
    a.task('1', 'modify', '--', 'seed, changed on a')
    time.sleep(1.1)
    b.task('1', 'modify', '--', 'seed, changed on b (later)')
    a.gittw('sync')
    r = b.gittw('sync')
    a.gittw('sync')
    for st in (a, b):
        no_conflict_markers(st)
        assert st.descriptions() == ['seed, changed on b (later)']
    assert 'changed on both sides' in (b.path / '.git' / 'gittw-merge.log').read_text()


def test_completed_on_one_edited_later_on_the_other_is_not_in_both_files(stations):
    a, b = stations
    a.task('1', 'done')
    a.gittw('sync')                                   # gc at sync moves it to completed.data
    assert a.uuids_in('pending.data') == []
    time.sleep(1.1)
    b.task('1', 'modify', '+later')                   # b hasn't seen the completion
    b.gittw('sync')
    a.gittw('sync')
    for st in (a, b):
        no_conflict_markers(st)
        pending, completed = set(st.uuids_in('pending.data')), set(st.uuids_in('completed.data'))
        assert not pending & completed, f'{st.name}: a task in both files'
        [t] = st.tasks()
        assert t['status'] == 'pending' and 'later' in t.get('tags', []), 'the later edit wins'


def test_gc_runs_at_sync_on_a_station_with_gc_off(stations):
    a, b = stations
    a.task('1', 'done')
    assert len(a.uuids_in('pending.data')) == 1, 'gc=off: the done task stays in pending.data'
    r = a.gittw('sync')
    assert 'gc moved 1 finished task' in r.stdout
    assert a.uuids_in('pending.data') == [] and len(a.uuids_in('completed.data')) == 1


def test_gc_at_sync_can_be_turned_off(stations):
    a, b = stations
    (a.path / 'local.rc').write_text('gittw.device = a\ngittw.gc_at_sync = no\n')
    a.task('1', 'done')
    a.gittw('sync')
    assert len(a.uuids_in('pending.data')) == 1


def test_commits_name_their_station_and_sync_records_last_seen(stations):
    a, b = stations
    b.task('add', '--', 'from b')
    b.gittw('sync')
    assert 'Station: b' in b.git('log', '-2', '--format=%B')
    assert gittw._rc_value(b.path / '.stations' / 'b', 'last_sync')
    a.gittw('status', '--fetch')
    data = json.loads(a.gittw('status', '--json').stdout)
    assert data['station'] == 'a'
    assert data['incoming'].get('b', 0) >= 1 and data['behind'] >= 1
    a.gittw('sync')
    data = json.loads(a.gittw('status', '--json').stdout)
    assert set(data['last_seen']) == {'a', 'b'} and data['merge_driver'] is True


def test_sync_configures_the_merge_driver_in_a_new_clone(stations):
    a, b = stations
    assert not gittw.merge_driver_configured(b.path)
    b.gittw('sync')
    assert gittw.merge_driver_configured(b.path)


# ── the merge itself ──────────────────────────────────────────────────────────

def line(uuid, desc, modified, status='pending', **extra):
    more = ''.join(f' {k}:"{v}"' for k, v in extra.items())
    return f'[description:"{desc}" entry:"1700000000" modified:"{modified}" status:"{status}"{more} uuid:"{uuid}"]'


U1, U2, U3 = ('11111111-0000-0000-0000-000000000000', '22222222-0000-0000-0000-000000000000',
              '33333333-0000-0000-0000-000000000000')


def test_merge_takes_the_side_that_changed():
    base = [line(U1, 'one', 1)]
    ours = [line(U1, 'one', 1)]
    theirs = [line(U1, 'one, theirs', 2)]
    assert gittw.merge_data(base, ours, theirs)[0] == theirs


def test_merge_both_changed_keeps_the_later_and_says_so():
    base = [line(U1, 'one', 1)]
    out, notes = gittw.merge_data(base, [line(U1, 'ours', 5)], [line(U1, 'theirs', 3)])
    assert out == [line(U1, 'ours', 5)] and 'changed on both sides' in notes[0]


def test_merge_removed_on_one_side_changed_on_the_other_keeps_the_change():
    base = [line(U1, 'one', 1), line(U2, 'two', 1)]
    out, notes = gittw.merge_data(base, [line(U2, 'two', 1)], [line(U1, 'one, edited', 2), line(U2, 'two', 1)])
    assert line(U1, 'one, edited', 2) in out and 'kept the change' in notes[0]


def test_merge_removed_on_one_side_unchanged_on_the_other_is_removed():
    base = [line(U1, 'one', 1), line(U2, 'two', 1)]
    out, _ = gittw.merge_data(base, [line(U2, 'two', 1)], base)
    assert out == [line(U2, 'two', 1)]


def test_merge_keeps_ours_order_and_appends_theirs_new():
    base = [line(U1, 'one', 1)]
    out, _ = gittw.merge_data(base, [line(U1, 'one', 1), line(U2, 'two', 1)], [line(U1, 'one', 1), line(U3, 'three', 1)])
    assert [gittw.UUID_RE.search(l).group(1) for l in out] == [U1, U2, U3]


def test_recurrence_report_names_a_template_with_two_pending_instances(tmp_path):
    t = '99999999-0000-0000-0000-000000000000'
    (tmp_path / 'pending.data').write_text('\n'.join([
        line(U1, 'water', 1, rtemplate=t, rindex='4'), line(U2, 'water', 1, rtemplate=t, rindex='4'),
        line(U3, 'other', 1)]) + '\n')
    [note] = gittw.recurrence_report(tmp_path)
    assert '99999999' in note and '2 pending instances' in note


def test_sync_without_a_git_identity_says_how_to_set_one(stations, tmp_path):
    a, b = stations
    env = {k: v for k, v in b.env.items() if not k.startswith(('GIT_AUTHOR', 'GIT_COMMITTER'))}
    env['EMAIL'] = ''
    b.env = env
    b.task('add', '--', 'from b')
    r = b.gittw('sync', ok=False)
    assert r.returncode != 0
    assert 'no name and email' in r.stderr and 'config user.email' in r.stderr
    assert 'Traceback' not in r.stderr
