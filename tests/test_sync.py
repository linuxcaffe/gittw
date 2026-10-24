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


# ── a pull that conflicts outside the task data (0.3.2) ───────────────────────

def in_rebase(st):
    return any((st.path / '.git' / d).exists() for d in ('rebase-merge', 'rebase-apply'))


def test_a_conflict_outside_the_task_data_is_undone_and_named(stations):
    a, b = stations
    (a.path / 'notes.txt').write_text('base\n')
    a.git('add', 'notes.txt')
    a.git('commit', '-qm', 'notes')
    a.gittw('sync')
    b.gittw('sync')
    (a.path / 'notes.txt').write_text('from a\n')
    a.gittw('sync')
    (b.path / 'notes.txt').write_text('from b\n')
    b.task('add', '--', 'b work')
    r = b.gittw('sync', ok=False)
    assert r.returncode != 0
    assert 'notes.txt' in r.stderr and 'undone' in r.stderr, r.stderr
    assert not in_rebase(b), 'left stopped mid-rebase'
    assert b.git('symbolic-ref', '--short', 'HEAD').strip() == 'master'
    assert (b.path / 'notes.txt').read_text() == 'from b\n', "b's own change is still there"
    assert 'b work' in b.descriptions(), "b's task is still there"


def test_sync_refuses_while_a_rebase_is_stopped(stations):
    a, b = stations
    (b.path / '.git' / 'rebase-merge').mkdir()
    r = b.gittw('sync', ok=False)
    assert r.returncode != 0 and 'middle of a rebase' in r.stderr and 'rebase --abort' in r.stderr
    assert (b.path / '.git' / 'rebase-merge').exists(), 'a stopped rebase is yours to finish or undo'


@pytest.mark.parametrize('b_changed_it', [True, False])
def test_a_station_keeps_its_manifest_when_the_repo_stops_tracking_it(stations, b_changed_it):
    a, b = stations
    (a.path / 'config').mkdir()
    (a.path / 'config' / '.tw_manifest').write_text('app|1.0|/home/a/x||t\n')
    a.git('add', 'config/.tw_manifest')
    a.git('commit', '-qm', 'manifest')
    a.gittw('sync')
    b.gittw('sync')
    mine = 'app|1.0|/data/b/x||t\n' if b_changed_it else 'app|1.0|/home/a/x||t\n'
    (b.path / 'config' / '.tw_manifest').write_text(mine)   # b's own record (or the shared one, unchanged)
    a.git('rm', '-q', '--cached', 'config/.tw_manifest')
    (a.path / '.gitignore').write_text((a.path / '.gitignore').read_text() + 'config/.tw_manifest\n')
    a.git('add', '.gitignore')
    a.git('commit', '-qm', 'untrack the manifest')
    a.gittw('sync')
    r = b.gittw('sync')
    if b_changed_it:
        assert 'stopped tracking config/.tw_manifest' in r.stdout, 'not the modify/delete path: ' + r.stdout
    assert (b.path / 'config' / '.tw_manifest').read_text() == mine
    assert 'config/.tw_manifest' not in b.git('ls-files')


@pytest.mark.parametrize('b_changed_it', [True, False])
def test_a_station_takes_the_manifest_when_the_repo_tracks_it_again(stations, b_changed_it):
    """tw 2.18.0 writes portable paths, so the manifest is shared again (gittw 0.3.4). A station
    still holding its own untracked copy takes the shared one; its copy is kept under .git."""
    a, b = stations
    for st in (a, b):
        (st.path / 'config').mkdir(exist_ok=True)
    (a.path / '.gitignore').write_text((a.path / '.gitignore').read_text() + 'config/.tw_manifest\n')
    a.git('add', '.gitignore')
    a.git('commit', '-qm', 'ignore the manifest')
    a.gittw('sync')
    b.gittw('sync')
    mine = 'app|1.0|/data/b/.task/x||t\n' if b_changed_it else 'app|1.0|~/.task/x||t\n'
    (b.path / 'config' / '.tw_manifest').write_text(mine)
    b.task('add', '--', 'b work')
    (a.path / 'config' / '.tw_manifest').write_text('app|1.0|~/.task/x||t\n')
    (a.path / '.gitignore').write_text((a.path / '.gitignore').read_text().replace('config/.tw_manifest\n', ''))
    a.git('add', '.gitignore', 'config/.tw_manifest')
    a.git('commit', '-qm', 'track the manifest again')
    a.gittw('sync')
    r = b.gittw('sync')
    assert not in_rebase(b)
    assert (b.path / 'config' / '.tw_manifest').read_text() == 'app|1.0|~/.task/x||t\n'
    assert 'config/.tw_manifest' in b.git('ls-files')
    assert 'b work' in a.descriptions() or (a.gittw('sync') and 'b work' in a.descriptions())
    if b_changed_it:
        assert (b.path / '.git' / 'gittw-kept' / 'config' / '.tw_manifest').read_text() == mine
        assert 'track config/.tw_manifest again' in r.stdout, r.stdout


# ── a dropped network is retried (0.3.3) ──────────────────────────────────────
# s5, 2026-10-22: "Read from remote host codeberg.org: Software caused connection abort" mid-pull

ABORT = subprocess.CompletedProcess([], 128, '', 'Read from remote host codeberg.org: Software caused connection abort\n'
                                                 'fatal: Could not read from remote repository.\n')
OK = subprocess.CompletedProcess([], 0, '', '')


def test_network_trouble_is_retried_then_succeeds():
    results, waits = [ABORT, ABORT, OK], []
    r = gittw._network_retry(lambda: results.pop(0), sleep=waits.append)
    assert r.returncode == 0 and waits == [3, 10]


def test_network_trouble_gives_up_after_three_tries():
    calls, waits = [], []
    r = gittw._network_retry(lambda: (calls.append(1), ABORT)[1], sleep=waits.append)
    assert r.returncode == 128 and len(calls) == 3


def test_other_failures_are_not_retried():
    other = subprocess.CompletedProcess([], 1, '', "error: cannot pull with rebase: You have unstaged changes.\n")
    calls = []
    r = gittw._network_retry(lambda: (calls.append(1), other)[1], sleep=lambda s: None)
    assert r is other and len(calls) == 1


# ── a mirror (a second push URL) that refuses doesn't fail the sync (2026-10-23) ──

def with_mirror(a, tmp_path):
    """origin pushes to the remote and a mirror, as yoga's and s5's do (Codeberg + GitHub)."""
    remote, mirror = tmp_path / 'remote.git', tmp_path / 'mirror.git'
    run(['git', 'clone', '-q', '--bare', str(remote), str(mirror)], a.env)
    for url in (remote, mirror):
        run(['git', '-C', str(a.path), 'remote', 'set-url', '--add', '--push', 'origin', str(url)], a.env)
    return remote, mirror


def diverge(mirror, a, tmp_path):
    """The mirror got a commit the remote never did (a push that reached only the mirror)."""
    other = tmp_path / 'other'
    run(['git', 'clone', '-q', str(mirror), str(other)], a.env)
    (other / 'stray').write_text('x\n')
    run(['git', '-C', str(other), 'add', 'stray'], a.env)
    run(['git', '-C', str(other), 'commit', '-qm', 'stray'], a.env)
    run(['git', '-C', str(other), 'push', '-q'], a.env)


def test_a_refusing_mirror_warns_and_the_sync_succeeds(stations, tmp_path):
    a, _ = stations
    remote, mirror = with_mirror(a, tmp_path)
    diverge(mirror, a, tmp_path)
    a.task('add', '--', 'after the stray')
    r = a.gittw('sync')                                           # exit 0
    head = a.git('rev-parse', 'HEAD').strip()
    assert run(['git', '-C', str(remote), 'rev-parse', 'master'], a.env).stdout.strip() == head
    assert f'gittw: the mirror {mirror} refused the push' in r.stderr
    assert ('(fetch first)' in r.stderr or '(non-fast-forward)' in r.stderr)          # git's reason
    assert f'push --force {mirror} HEAD:master' in r.stderr
    assert 'Sync complete.' in r.stdout


def test_a_refusing_main_remote_still_fails(stations, tmp_path):
    a, _ = stations
    remote, mirror = with_mirror(a, tmp_path)
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "remote says no" >&2\nexit 1\n')
    hook.chmod(0o755)
    a.task('add', '--', 'cannot land')
    r = a.gittw('sync', ok=False)
    assert r.returncode != 0 and 'refused the push' not in r.stderr
    assert 'remote says no' in r.stderr


# ── tw-events' files: my-events.txt merges as a union, the generated ones never stop a sync ──

def _commit(st, msg):
    st.git('add', '-A')
    st.git('commit', '-qm', msg)


def test_my_events_from_both_stations_are_both_kept(stations):
    a, b = stations
    b.gittw('sync')
    for st in (a, b):
        (st.path / 'config').mkdir(exist_ok=True)
    (a.path / 'config' / 'my-events.txt').write_text('# mine\n')
    _commit(a, 'my events'); a.gittw('sync'); b.gittw('sync')
    with open(a.path / 'config' / 'my-events.txt', 'a') as f:
        f.write('2026-10-29  From a\n')
    with open(b.path / 'config' / 'my-events.txt', 'a') as f:
        f.write('2026-10-30  From b\n')
    a.gittw('sync'); b.gittw('sync'); a.gittw('sync')
    for st in (a, b):
        text = (st.path / 'config' / 'my-events.txt').read_text()
        assert 'From a' in text and 'From b' in text and '<<<<' not in text
        assert not gittw._rebase_stopped(st.path)


def test_generated_events_files_that_differ_do_not_stop_a_sync(stations):
    a, b = stations
    b.gittw('sync')
    for st, n in ((a, 1), (b, 2)):          # both new on each side: an add/add
        (st.path / 'config').mkdir(exist_ok=True)
        for f in ('holidays.rc', 'sky.rc', 'mine.rc'):
            (st.path / 'config' / f).write_text(f'# events: {st.name}\nholiday.me2026102{n}a.name=x\n')
    a.gittw('sync')
    r = b.gittw('sync')
    assert 'Sync complete' in r.stdout
    assert not gittw._rebase_stopped(b.path)
    assert '<<<<' not in (b.path / 'config' / 'mine.rc').read_text()


def test_after_a_pull_events_rebuilds_its_files_offline(stations):
    a, b = stations
    for st in (a, b):
        (st.path / 'scripts').mkdir(exist_ok=True)
        (st.path / 'config').mkdir(exist_ok=True)
        (st.path / 'config' / 'events.rc').write_text('events.locale = en-CA\n')
        ev = st.path / 'scripts' / 'events'
        ev.write_text('#!/bin/sh\n# supports --no-fetch\n'
                      'echo "$TW_TASK_DIR $*" >> "$TW_TASK_DIR/events-calls"\n')
        ev.chmod(0o755)
    (a.path / '.gitignore').write_text((a.path / '.gitignore').read_text() + 'events-calls\n')
    _commit(a, 'events'); a.gittw('sync')
    b.gittw('sync')
    calls = (b.path / 'events-calls').read_text().splitlines()
    assert calls and calls[-1] == f'{b.path} refresh --no-fetch'


def test_an_events_without_no_fetch_is_left_alone(stations):
    a, b = stations
    (b.path / 'scripts').mkdir(exist_ok=True)
    (b.path / 'config').mkdir(exist_ok=True)
    (b.path / 'config' / 'events.rc').write_text('')
    ev = b.path / 'scripts' / 'events'
    ev.write_text('#!/bin/sh\ntouch "$TW_TASK_DIR/ran"\nexit 2\n')     # tw-events 0.3.0 and older
    ev.chmod(0o755)
    r = b.gittw('sync')
    assert not (b.path / 'ran').exists() and 'events' not in r.stderr
