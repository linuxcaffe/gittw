#!/usr/bin/env python3
"""gittw — git-based sync for Taskwarrior ~/.task/

Several stations (a laptop, a phone) share one ~/.task through a git remote. Every commit
gittw makes says which station made it (a `Station:` trailer); `gittw sync` records when
this station last synced (.stations/<name>), runs Taskwarrior's gc once right after
pulling (so a station with gc=off never moves tasks between files on a mere read), and
pulls with a merge driver for pending.data and completed.data that merges by task UUID,
so two stations that both changed tasks between syncs rarely conflict.
"""

import os
import re
import time
import shlex
import shutil
import socket
import sys
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path

__version__ = '0.3.5'
DATA_FILES = ('pending.data', 'completed.data')
MERGE_DRIVER = 'twdata'


def get_task_dir():
    td = os.environ.get('TW_TASK_DIR')
    if td:
        return Path(td)
    return Path.home() / '.task'


def git(args, task_dir=None, check=True, capture=False):
    td = task_dir or get_task_dir()
    cmd = ['git', '-C', str(td)] + args
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        if capture and result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result


GITIGNORE = """\
# Taskwarrior runtime — local only, do not sync
undo.data
backlog.data
*.lock

# Debug and cache logs
logs/
config/.cmx_stale

# awesome-taskwarrior installed packages — reinstall from registry on each machine
awesome-taskwarrior/

# Uncomment to exclude machine-specific managed dirs:
# hooks/
# scripts/
# themes/
"""


def _handle_taskrc(task_dir):
    """Offer to move ~/.taskrc into task_dir and replace it with a symlink."""
    home    = Path.home()
    taskrc  = home / '.taskrc'
    target  = task_dir / '.taskrc'

    if taskrc.is_symlink():
        try:
            if taskrc.resolve() == target.resolve():
                print(f"~/.taskrc → {target} (already linked — good)")
                return
        except Exception:
            pass
        print(f"~/.taskrc is already a symlink pointing elsewhere — skipping.")
        return

    if not taskrc.exists():
        print(f"No ~/.taskrc found — Taskwarrior will use defaults.")
        print(f"Once you have one, link it in with:")
        print(f"  mv ~/.taskrc {target} && ln -s {target} ~/.taskrc")
        return

    print(f"\n~/.taskrc is not tracked by git.")
    print(f"Moving it to {target} and replacing it with a symlink")
    print(f"will keep your full config in sync across machines.")
    answer = input("Move ~/.taskrc into the repo and symlink it? [Y/n] ").strip().lower()
    if answer not in ('', 'y'):
        print(f"Skipped. Config won't be synced.")
        print(f"To do it manually later:")
        print(f"  mv ~/.taskrc {target} && ln -s {target} ~/.taskrc")
        return

    if target.exists():
        print(f"error: {target} already exists — resolve manually.", file=sys.stderr)
        return

    taskrc.rename(target)
    taskrc.symlink_to(target)
    print(f"Moved and linked: ~/.taskrc → {target}")
    subprocess.run(['git', '-C', str(task_dir), 'add', '.taskrc'], check=True)
    print(f"Staged .taskrc for commit.")


# ── this station ──────────────────────────────────────────────────────────────

def _rc_value(path, key):
    """key's value in one rc file (no includes), or None. Read directly: calling task is slow."""
    try:
        for line in Path(path).read_text().splitlines():
            k, sep, v = line.split('#', 1)[0].partition('=')
            if sep and k.strip() == key:
                return v.strip()
    except OSError:
        pass
    return None


def setting(key, default, task_dir=None):
    """gittw.<key> from config/gittw.rc, then local.rc (this station's word wins)."""
    td = task_dir or get_task_dir()
    value = default
    for path in (td / 'config' / 'gittw.rc', td / 'local.rc'):
        v = _rc_value(path, f'gittw.{key}')
        if v is not None:
            value = v
    return value


def _yes(value):
    return str(value).strip().lower() not in ('no', 'off', 'false', '0', '')


def station(task_dir=None):
    """This station's name: gittw.device in local.rc, else the hostname."""
    td = task_dir or get_task_dir()
    return _rc_value(td / 'local.rc', 'gittw.device') or socket.gethostname().split('.')[0] or 'unknown'


def with_station(message, task_dir=None):
    return f'{message}\n\nStation: {station(task_dir)}'


def _commit_pending(task_dir, message=None):
    """Stage and commit any pending changes. Returns True if a commit was made."""
    result = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        return False
    if message is None:
        n = len(result.stdout.strip().splitlines())
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        message = f'gittw: {now} ({n} file{"s" if n != 1 else ""} changed)'
    subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], check=True)
    r = subprocess.run(['git', '-C', str(task_dir), 'commit', '-q', '-m', with_station(message, task_dir)],
                       capture_output=True, text=True)
    if r.returncode:
        err = (r.stderr or r.stdout).strip()
        if 'user.email' in err or 'user.name' in err or 'who you are' in err:
            sys.exit(f"gittw: git can't commit on this station: it has no name and email. Set them for "
                     f"{task_dir}:\n  git -C {task_dir} config user.name 'Your Name'\n"
                     f"  git -C {task_dir} config user.email you@example.com")
        sys.exit(f"gittw: git commit failed: {err}")
    return True


def _adopt_existing_repo(task_dir, remote):
    """Adopt an existing ~/.task git repo: add .gitignore, clean index, set remote."""
    # Show what we found
    log = subprocess.run(
        ['git', '-C', str(task_dir), 'log', '--oneline', '-3'],
        capture_output=True, text=True
    )
    existing_remote = subprocess.run(
        ['git', '-C', str(task_dir), 'remote', '-v'],
        capture_output=True, text=True
    )
    print(f"Found existing git repo in {task_dir}")
    if log.stdout.strip():
        print(f"Recent commits:")
        for line in log.stdout.strip().splitlines():
            print(f"  {line}")
    if existing_remote.stdout.strip():
        print(f"Current remote:")
        for line in existing_remote.stdout.strip().splitlines():
            print(f"  {line}")
    else:
        print(f"No remote configured.")

    gitignore_path = task_dir / '.gitignore'
    if gitignore_path.exists():
        print(f".gitignore already exists — will not overwrite.")
    else:
        print(f"\nWill create .gitignore (excludes undo.data, backlog.data, logs/, etc.)")

    if remote:
        print(f"Will set remote 'origin' to: {remote}")

    print()
    answer = input("Adopt this repo for gittw? [y/N] ").strip().lower()
    if answer != 'y':
        print("Aborted.")
        sys.exit(0)

    # Write .gitignore
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE)
        print(f"Created .gitignore")

    # Remove now-ignored files from the index (they stay on disk).
    # -i --cached finds tracked files matching ignore patterns (untracked ignored
    # files are irrelevant — git already ignores them without --cached).
    ignored = subprocess.run(
        ['git', '-C', str(task_dir), 'ls-files', '-i', '--cached', '--exclude-standard', '-z'],
        capture_output=True, text=True
    )
    if ignored.stdout.strip():
        files = [f for f in ignored.stdout.split('\0') if f]
        subprocess.run(
            ['git', '-C', str(task_dir), 'rm', '--cached', '--quiet', '-r'] + files,
            check=True
        )
        print(f"Removed {len(files)} now-ignored file(s) from index.")

    # Handle ~/.taskrc
    _handle_taskrc(task_dir)

    # Commit
    result = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], check=True)
        subprocess.run(
            ['git', '-C', str(task_dir), 'commit', '-m', 'gittw: adopt repo, add .gitignore'],
            check=True
        )
        print("Committed.")

    # Set remote
    _set_remote(task_dir, remote)


def _set_remote(task_dir, remote):
    if not remote:
        print(f"\nAdd a remote when ready:  gittw remote add <url>")
        return
    r = subprocess.run(
        ['git', '-C', str(task_dir), 'remote'],
        capture_output=True, text=True
    )
    if 'origin' in r.stdout.split():
        subprocess.run(['git', '-C', str(task_dir), 'remote', 'set-url', 'origin', remote], check=True)
        print(f"Remote 'origin' updated to {remote}")
    else:
        subprocess.run(['git', '-C', str(task_dir), 'remote', 'add', 'origin', remote], check=True)
        print(f"Remote 'origin' set to {remote}")
    print(f"To push for the first time:  gittw push --set-upstream")


def cmd_init(args):
    task_dir = get_task_dir()
    if not task_dir.exists():
        print(f"error: {task_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Existing repo — adopt flow
    if (task_dir / '.git').exists():
        _adopt_existing_repo(task_dir, args.remote)
        return

    # Fresh init
    git(['init'], task_dir=task_dir)
    print(f"Initialized git repo in {task_dir}")

    gitignore_path = task_dir / '.gitignore'
    gitignore_path.write_text(GITIGNORE)
    print(f"Created .gitignore")

    # Handle ~/.taskrc before initial commit so it's included
    _handle_taskrc(task_dir)

    ensure_merge_driver(task_dir)
    git(['add', '-A'], task_dir=task_dir)
    result = git(['status', '--porcelain'], task_dir=task_dir, capture=True)
    if result.stdout.strip():
        git(['commit', '-m', with_station('gittw: initial commit', task_dir)], task_dir=task_dir)
        print("Initial commit created.")
    else:
        print("Nothing to commit.")

    _set_remote(task_dir, args.remote)


def _ago(iso):
    try:
        then = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return iso
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    for unit, n in (('d', 86400), ('h', 3600), ('min', 60)):
        if secs >= n:
            return f'{int(secs // n)} {unit} ago'
    return 'just now'


def last_seen(task_dir=None):
    """{station: last_sync ISO time} from .stations/*."""
    td = task_dir or get_task_dir()
    out = {}
    for f in sorted((td / '.stations').glob('*')):
        when = _rc_value(f, 'last_sync')
        if when:
            out[f.name] = when
    return out


def incoming(task_dir=None):
    """{station: commits on the remote not here yet}, by each commit's Station: trailer."""
    td = task_dir or get_task_dir()
    r = git(['log', '--format=%(trailers:key=Station,valueonly,separator=%x2C)', 'HEAD..@{u}'],
            task_dir=td, capture=True, check=False)
    out = {}
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            name = line.strip().split(',')[0].strip() or '(untagged)'
            out[name] = out.get(name, 0) + 1
    return out


def status_data(task_dir=None, fetch=False):
    td = task_dir or get_task_dir()
    if fetch:
        git(['fetch', '-q'], task_dir=td, check=False)
    dirty = git(['status', '--porcelain'], task_dir=td, capture=True, check=False).stdout.splitlines()
    ab = git(['rev-list', '--left-right', '--count', 'HEAD...@{u}'], task_dir=td, capture=True, check=False)
    ahead = behind = 0
    if ab.returncode == 0 and ab.stdout.split():
        ahead, behind = map(int, ab.stdout.split())
    return {'station': station(td), 'uncommitted': len(dirty), 'unpushed': ahead, 'behind': behind,
            'incoming': incoming(td), 'last_seen': last_seen(td),
            'merge_driver': merge_driver_configured(td)}


def cmd_status(args):
    task_dir = get_task_dir()
    if getattr(args, 'json', False):
        import json
        print(json.dumps(status_data(task_dir, fetch=args.fetch), indent=2))
        return
    if getattr(args, 'fetch', False):
        git(['fetch', '-q'], task_dir=task_dir, check=False)
    result = git(['status', '--short'], task_dir=task_dir, capture=True)
    if result.stdout.strip():
        print(result.stdout, end='')
    else:
        print("Clean — nothing to commit.")

    head = git(['log', '--oneline', '-1'], task_dir=task_dir, capture=True, check=False)
    if head.returncode == 0 and head.stdout.strip():
        print(f"HEAD: {head.stdout.strip()}")

    # Show ahead/behind if tracking a remote
    tracking = git(
        ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'],
        task_dir=task_dir, capture=True, check=False
    )
    if tracking.returncode == 0:
        ab = git(['rev-list', '--left-right', '--count', 'HEAD...@{u}'],
                 task_dir=task_dir, capture=True, check=False)
        if ab.returncode == 0:
            ahead, behind = ab.stdout.strip().split()
            remote = tracking.stdout.strip()
            print(f"{remote}: {ahead} ahead, {behind} behind")
            inc = incoming(task_dir)
            if inc:
                print("Incoming: " + ', '.join(f"{n} from {s}" for s, n in sorted(inc.items())))
    me = station(task_dir)
    seen = last_seen(task_dir)
    if seen:
        print("Stations: " + '; '.join(f"{s}{' (this one)' if s == me else ''} synced {_ago(t)}"
                                       for s, t in sorted(seen.items())))


def cmd_log(args):
    git(['log', '--oneline', f'-{args.n}'])


# Each station's own record, which a repo may stop tracking: a station keeps its copy when it does.
# tw before 2.18.0 wrote .tw_manifest with this station's absolute paths, so a shared copy conflicted
# at every install, and repos stopped tracking it (gittw 0.3.2). tw 2.18.0 writes ~/.task paths, and a
# repo may track it again: then a station's untracked copy gives way to the shared one, and is kept
# in .git/gittw-kept/ (0.3.4).
KEEP_WHEN_UNTRACKED = ('config/.tw_manifest',)


def _rebase_stopped(task_dir):
    for name in ('rebase-merge', 'rebase-apply'):
        r = git(['rev-parse', '--git-path', name], task_dir=task_dir, capture=True, check=False)
        path = Path(r.stdout.strip())
        if not path.is_absolute():
            path = task_dir / path
        if r.returncode == 0 and path.exists():
            return True
    return False


def _refuse_if_rebase_stopped(task_dir):
    if _rebase_stopped(task_dir):
        print(f"gittw: {task_dir} is stopped in the middle of a rebase (an earlier pull conflicted).\n"
              f"  Finish it: fix the files `git -C {task_dir} status` names, git add them, git rebase --continue\n"
              f"  Or undo it: git -C {task_dir} rebase --abort   (your local commits stay)", file=sys.stderr)
        sys.exit(1)


# A phone's network drops connections now and then: "Software caused connection abort" mid-pull
# (s5, 2026-10-22). Those are tried again, twice; any other failure is reported at once.
NETWORK_TROUBLE = re.compile(r'connection abort|connection reset|connection closed|timed out|could not resolve|'
                             r'network is unreachable|no route to host|read from remote host|broken pipe|'
                             r'early eof|unexpected disconnect', re.I)


def _network_retry(run, waits=(3, 10), sleep=time.sleep):
    """run() → CompletedProcess; again after each wait while it fails with network trouble."""
    r = run()
    for wait in waits:
        if r.returncode == 0 or not NETWORK_TROUBLE.search((r.stderr or '') + (r.stdout or '')):
            return r
        print(f"The network dropped the connection; trying again in {wait} s.")
        sleep(wait)
        r = run()
    return r


def _pull_rebase(task_dir, quiet=True):
    """pull --rebase. A conflict over a KEEP_WHEN_UNTRACKED file the other stations stopped tracking
    is settled their way (untracked), and this station's copy put back. Any other conflict the merge
    driver can't settle is undone, so the repo is back on its branch with its own commits, and the
    files are named: never left stopped mid-rebase."""
    keep = {rel: (task_dir / rel).read_bytes() for rel in KEEP_WHEN_UNTRACKED if (task_dir / rel).is_file()}
    tracked_before = {rel for rel in keep if git(['ls-files', '--error-unmatch', rel], task_dir=task_dir,
                                                 check=False, capture=True).returncode == 0}
    r = _network_retry(lambda: git(['pull', '--rebase'] + (['-q'] if quiet else []), task_dir=task_dir,
                                   check=False, capture=True))
    conflicted = []
    while r.returncode != 0 and _rebase_stopped(task_dir):
        conflicted = git(['diff', '--name-only', '--diff-filter=U'], task_dir=task_dir,
                         capture=True, check=False).stdout.split()
        # mid-rebase, HEAD is the upstream side: a file it lacks is one the others stopped tracking
        upstream_has = lambda rel: git(['cat-file', '-e', f'HEAD:{rel}'], task_dir=task_dir,
                                       check=False, capture=True).returncode == 0
        if not conflicted or any(rel not in keep or upstream_has(rel) for rel in conflicted):
            break
        git(['rm', '-q', '--cached', '--'] + conflicted, task_dir=task_dir, check=False, capture=True)
        staged = git(['diff', '--cached', '--quiet'], task_dir=task_dir, check=False).returncode != 0
        r = subprocess.run(['git', '-C', str(task_dir), 'rebase', '--continue' if staged else '--skip'],
                           capture_output=True, text=True, env={**os.environ, 'GIT_EDITOR': 'true'})
        print(f"The other stations stopped tracking {', '.join(conflicted)}; so does this one, keeping its copy.")
    for rel, data in keep.items():
        tracked = git(['ls-files', '--error-unmatch', rel], task_dir=task_dir, check=False, capture=True).returncode == 0
        if tracked and rel not in tracked_before and (task_dir / rel).is_file() \
                and (task_dir / rel).read_bytes() != data:
            kept = Path(git(['rev-parse', '--git-dir'], task_dir=task_dir, capture=True).stdout.strip())
            kept = (kept if kept.is_absolute() else task_dir / kept) / 'gittw-kept' / rel
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(data)
            print(f"The other stations track {rel} again; this station takes theirs "
                  f"(its own copy is in {kept}).")
        if not tracked and not (task_dir / rel).exists():
            (task_dir / rel).parent.mkdir(parents=True, exist_ok=True)   # git removes a folder it emptied
            (task_dir / rel).write_bytes(data)
    if r.returncode == 0:
        return
    if _rebase_stopped(task_dir):
        git(['rebase', '--abort'], task_dir=task_dir, check=False, capture=True)
        print(f"gittw: the pull conflicted in {', '.join(conflicted) or 'a file'}, which gittw can't merge; "
              f"undone, nothing here changed (your local commits stay).\n"
              f"  To settle it by hand: git -C {task_dir} pull --rebase, fix those files, git add them, "
              f"git rebase --continue", file=sys.stderr)
        sys.exit(1)
    print((r.stderr or r.stdout).strip(), file=sys.stderr)
    sys.exit(r.returncode)


def cmd_pull(args):
    task_dir = get_task_dir()
    _refuse_if_rebase_stopped(task_dir)
    committed = _commit_pending(task_dir)
    if committed:
        print("Committed local changes before pull.")
    _pull_rebase(task_dir, quiet=False)


def cmd_push(args):
    task_dir = get_task_dir()
    msg = args.message or 'gittw: commit before push'
    committed = _commit_pending(task_dir, msg)
    if committed:
        print("Committed local changes.")
    extra = ['--set-upstream', 'origin', 'HEAD'] if args.set_upstream else []
    git(['push'] + extra, task_dir=task_dir)


def _mirrors_refused(task_dir, output):
    """[(url, why, branch)] when a push failed only at a mirror: the upstream remote has a second push
    URL (Codeberg + GitHub), and the URL it fetches from has this HEAD. Else [] (a real failure).
    2026-10-23: a push that reached only GitHub left it diverged, and every sync after failed there."""
    q = lambda *a: git(list(a), task_dir=task_dir, check=False, capture=True).stdout.strip()
    upstream = q('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    if '/' not in upstream:
        return []
    remote, branch = upstream.split('/', 1)
    main = q('remote', 'get-url', remote)
    mirrors = [u for u in q('remote', 'get-url', '--push', '--all', remote).splitlines() if u and u != main]
    if not main or not mirrors:
        return []
    there = _network_retry(lambda: git(['ls-remote', main, f'refs/heads/{branch}'], task_dir=task_dir,
                                       check=False, capture=True))
    if there.returncode != 0 or there.stdout.split()[:1] != [q('rev-parse', 'HEAD')]:
        return []
    refused, url = [], None
    for line in output.splitlines():
        if line.startswith('To '):
            url = line[3:].strip()
        elif line.lstrip().startswith('!') and url in mirrors:
            why = line[line.rfind('(') + 1:line.rfind(')')] if '(' in line else line.strip()
            refused.append((url, why, branch))
            url = None
    return refused or [(u, 'push failed', branch) for u in mirrors]


def cmd_sync(args):
    """Commit local changes, pull (rebase, merging task data by UUID), tidy up, record this
    station's sync, then push."""
    task_dir = get_task_dir()
    _refuse_if_rebase_stopped(task_dir)
    ensure_merge_driver(task_dir, quiet=True)

    msg = args.message or 'gittw: sync commit'
    committed = _commit_pending(task_dir, msg)
    if committed:
        print("Committed local changes.")
    else:
        print("Working tree clean.")

    print("Pulling...")
    _pull_rebase(task_dir)

    for note in after_pull(task_dir):
        print(note)
    write_last_seen(task_dir)
    _commit_pending(task_dir, f'gittw: {station(task_dir)} synced')

    print("Pushing...")
    r = _network_retry(lambda: git(['push', '-q'], task_dir=task_dir, check=False, capture=True))
    if r.returncode != 0:
        output = (r.stderr or r.stdout).strip()
        refused = _mirrors_refused(task_dir, output)
        if not refused:
            print(output, file=sys.stderr)
            sys.exit(r.returncode)
        for url, why, branch in refused:
            print(f"gittw: the mirror {url} refused the push ({why}); the main remote has everything.\n"
                  f"  To make the mirror match it: git -C {task_dir} push --force {url} HEAD:{branch}",
                  file=sys.stderr)

    print("Sync complete.")


# ── after a pull: duplicates across files, recurrence, gc ─────────────────────

UUID_RE = re.compile(r'(?:^\[| )uuid:"([0-9a-fA-F-]+)"')


def _field(line, key):
    m = re.search(r'(?:^\[| )%s:"([^"]*)"' % re.escape(key), line)
    return m.group(1) if m else ''


def _stamp(line):
    """When a task line last changed: modified, else entry (epoch seconds; 0 if neither)."""
    v = _field(line, 'modified') or _field(line, 'entry')
    return int(v) if v.isdigit() else 0


def _read_lines(path):
    try:
        return [l for l in Path(path).read_text().splitlines() if l.strip()]
    except OSError:
        return []


def _write_lines(path, lines):
    Path(path).write_text(''.join(l + '\n' for l in lines))


def dedupe_across_files(task_dir):
    """A task in both pending.data and completed.data (two stations, one completed it, one
    edited it): keep the newer copy, where it is, and drop the other. Returns notes."""
    pending_p, completed_p = task_dir / 'pending.data', task_dir / 'completed.data'
    pending, completed = _read_lines(pending_p), _read_lines(completed_p)
    in_completed = {}
    for l in completed:
        m = UUID_RE.search(l)
        if m:
            in_completed[m.group(1)] = l
    notes, drop_p, drop_c = [], set(), set()
    for l in pending:
        m = UUID_RE.search(l)
        if m and m.group(1) in in_completed:
            u, other = m.group(1), in_completed[m.group(1)]
            if _stamp(l) >= _stamp(other):
                drop_c.add(u)
                notes.append(f"gittw: task {u[:8]} was in both files: kept the newer, pending copy")
            else:
                drop_p.add(u)
                notes.append(f"gittw: task {u[:8]} was in both files: kept the newer, completed copy")
    if drop_p:
        _write_lines(pending_p, [l for l in pending if not (UUID_RE.search(l) and UUID_RE.search(l).group(1) in drop_p)])
    if drop_c:
        _write_lines(completed_p, [l for l in completed if not (UUID_RE.search(l) and UUID_RE.search(l).group(1) in drop_c)])
    return notes


def recurrence_report(task_dir):
    """Templates with more than one pending instance (two stations each spawned one). Reported,
    never fixed: which to keep is the user's call."""
    by_template = {}
    for l in _read_lines(task_dir / 'pending.data'):
        t = _field(l, 'rtemplate')
        if t and _field(l, 'status') in ('pending', 'waiting'):
            by_template.setdefault(t, []).append(_field(l, 'uuid'))
    return [f"gittw: recurring template {t[:8]} has {len(us)} pending instances "
            f"({', '.join(u[:8] for u in us)}): keep one, `task <uuid> delete` the rest"
            for t, us in sorted(by_template.items()) if len(us) > 1]


def gc_at_sync(task_dir):
    """Run Taskwarrior's gc once, now that this station is current (for stations with gc=off)."""
    if not _yes(setting('gc_at_sync', 'yes', task_dir)) or not shutil.which('task'):
        return []
    before = [_read_lines(task_dir / f) for f in DATA_FILES]
    subprocess.run(['task', f'rc.data.location={task_dir}', 'rc.gc=on', 'rc.hooks=off', 'rc.context=none',
                    'rc.confirmation=no', 'rc.verbose=nothing', 'count'],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
    after = [_read_lines(task_dir / f) for f in DATA_FILES]
    moved = len(before[0]) - len(after[0])
    return [f"gittw: gc moved {moved} finished task{'s' if moved != 1 else ''} to completed.data"] if moved > 0 else []


def after_pull(task_dir):
    notes = dedupe_across_files(task_dir)
    notes += gc_at_sync(task_dir)
    notes += recurrence_report(task_dir)
    _commit_pending(task_dir, 'gittw: after pull (gc, duplicates)')
    return notes


def write_last_seen(task_dir):
    d = task_dir / '.stations'
    d.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    (d / station(task_dir)).write_text(
        f"# gittw: when this station last synced (written by every gittw sync)\n"
        f"last_sync = {now}\ngittw = {__version__}\n")


# ── the merge driver ──────────────────────────────────────────────────────────

def merge_data(base, ours, theirs):
    """Three-way merge of Taskwarrior data lines by task UUID. Returns (lines, notes).

    A task only one side changed takes that side; both changed it: the later `modified` wins
    (last write wins per task, as taskd sync did); removed on one side and changed on the
    other: the change is kept (nothing is lost silently). Ours' order first, then tasks new on
    their side, so IDs on the ours side stay put."""
    def index(lines):
        out = {}
        for l in lines:
            m = UUID_RE.search(l)
            if m:
                out[m.group(1)] = l
        return out
    b, o, t = index(base), index(ours), index(theirs)
    order = list(o) + [u for u in t if u not in o]
    result, notes = [], []
    for u in order:
        bo, oo, to = b.get(u), o.get(u), t.get(u)
        if oo == to:
            pick = oo
        elif oo == bo:
            pick = to
        elif to == bo:
            pick = oo
        elif oo and to:
            pick = oo if _stamp(oo) >= _stamp(to) else to
            notes.append(f"{u[:8]}: changed on both sides; kept the later ({'ours' if pick is oo else 'theirs'})")
        else:
            pick = oo or to
            notes.append(f"{u[:8]}: removed on one side, changed on the other; kept the change")
        if pick:
            result.append(pick)
    return result, notes


def cmd_merge_data(args):
    """git merge driver: gittw merge-data %O %A %B %P. Writes the result to %A; exit 0 = resolved."""
    result, notes = merge_data(_read_lines(args.base), _read_lines(args.ours), _read_lines(args.theirs))
    _write_lines(args.ours, result)
    if notes:
        try:
            gd = subprocess.run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True).stdout.strip()
            with open(Path(gd or '.git') / 'gittw-merge.log', 'a') as f:
                stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for n in notes:
                    f.write(f"{stamp} {args.path}: {n}\n")
        except OSError:
            pass
        print(f"gittw: merged {args.path} by task: " + '; '.join(notes), file=sys.stderr)
    return 0


def merge_driver_configured(task_dir=None):
    td = task_dir or get_task_dir()
    r = git(['config', f'merge.{MERGE_DRIVER}.driver'], task_dir=td, capture=True, check=False)
    return r.returncode == 0 and 'merge-data' in r.stdout


def ensure_merge_driver(task_dir=None, quiet=False):
    """Configure the driver in this clone (.git/config: per clone) and name it in .gitattributes
    (tracked: shared). Idempotent."""
    td = task_dir or get_task_dir()
    me = Path(__file__).resolve()
    driver = f'{shlex.quote(sys.executable)} {shlex.quote(str(me))} merge-data %O %A %B %P'
    current = git(['config', f'merge.{MERGE_DRIVER}.driver'], task_dir=td, capture=True, check=False).stdout.strip()
    if current != driver:
        git(['config', f'merge.{MERGE_DRIVER}.name', 'gittw: Taskwarrior data, merged by task UUID'], task_dir=td)
        git(['config', f'merge.{MERGE_DRIVER}.driver', driver], task_dir=td)
        if not quiet:
            print(f"Merge driver configured in this clone: {driver}")
    attrs = td / '.gitattributes'
    text = attrs.read_text() if attrs.exists() else ''
    missing = [f for f in DATA_FILES if not re.search(r'^%s\s+merge=%s' % (re.escape(f), MERGE_DRIVER), text, re.M)]
    if missing:
        add = ''.join(f'{f} merge={MERGE_DRIVER}\n' for f in missing)
        sep = '' if not text or text.endswith('\n') else '\n'
        attrs.write_text(text + sep + '# Taskwarrior data: merged by task UUID (gittw merge-data)\n' + add)
        if not quiet:
            print(f"Named the merge driver in .gitattributes for {', '.join(missing)}")


def cmd_setup(args):
    ensure_merge_driver(get_task_dir())
    print(f"This station is {station()}; merge driver {'ok' if merge_driver_configured() else 'NOT configured'}.")


def cmd_undo(args):
    """Restore ~/.task/ to a previous commit state (creates a new commit, no history rewrite)."""
    task_dir = get_task_dir()

    # Commit any dirty state first so nothing is silently discarded
    committed = _commit_pending(task_dir, 'gittw: commit before undo')
    if committed:
        print("Committed local changes before undo.")

    # Fetch recent commits for display / selection
    log = git(
        ['log', '--oneline', f'-{args.n}'],
        task_dir=task_dir, capture=True
    )
    lines = log.stdout.strip().splitlines()
    if not lines:
        print("No commits found.", file=sys.stderr)
        sys.exit(1)

    if args.to:
        if args.to.isdigit():
            # Accept a 1-based index from the displayed list
            idx = int(args.to) - 1
            if not (0 <= idx < len(lines)):
                print(f"Index out of range (1–{len(lines)}).", file=sys.stderr)
                sys.exit(1)
            chosen = lines[idx]
            target_hash, target_subject = chosen.split(' ', 1)
        else:
            # Full or abbreviated commit hash
            target_hash = args.to
            info = git(['log', '--oneline', '-1', target_hash],
                       task_dir=task_dir, capture=True, check=False)
            target_subject = info.stdout.strip().split(' ', 1)[-1] if info.stdout.strip() else target_hash
    else:
        # Interactive: show numbered list and prompt
        print("Recent commits:\n")
        for i, line in enumerate(lines, 1):
            print(f"  {i:2d}  {line}")
        print()
        try:
            raw = input(f"Restore to which commit? [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        idx = int(raw) - 1 if raw.isdigit() else 0
        if not (0 <= idx < len(lines)):
            print("Invalid selection.", file=sys.stderr)
            sys.exit(1)
        chosen = lines[idx]
        target_hash, target_subject = chosen.split(' ', 1)

    print(f"Restoring to {target_hash}...")
    git(['checkout', target_hash, '--', '.'], task_dir=task_dir)

    status = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if not status.stdout.strip():
        print("Already at that state — nothing to restore.")
        return

    commit_msg = f'gittw: undo to {target_hash} ("{target_subject}")'
    subprocess.run(
        ['git', '-C', str(task_dir), 'commit', '-m', commit_msg], check=True
    )
    print(f"Done. Restored state committed locally.")
    print(f"Run 'gittw sync' to push when ready.")


def cmd_remote(args):
    task_dir = get_task_dir()
    if args.remote_cmd == 'add':
        git(['remote', 'add', args.name, args.url], task_dir=task_dir)
        print(f"Remote '{args.name}' → {args.url}")
    elif args.remote_cmd == 'set-url':
        git(['remote', 'set-url', args.name, args.url], task_dir=task_dir)
        print(f"Remote '{args.name}' → {args.url}")
    elif args.remote_cmd == 'show':
        git(['remote', '-v'], task_dir=task_dir)


def main():
    parser = argparse.ArgumentParser(
        prog='gittw',
        description='git-based sync for Taskwarrior'
    )
    parser.add_argument('--version', action='version', version=f'gittw {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)

    p_init = sub.add_parser('init', help='Initialize git repo in ~/.task/')
    p_init.add_argument('remote', nargs='?', metavar='REMOTE_URL',
                        help='Optional remote URL (e.g. git@github.com:user/tasks.git)')
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser('status', help='Working tree, ahead/behind, incoming by station, stations last seen')
    p_status.add_argument('--fetch', action='store_true', help='fetch first, so incoming is current')
    p_status.add_argument('--json', action='store_true', help='as JSON (for clients such as tw-web)')
    p_status.set_defaults(func=cmd_status)

    p_setup = sub.add_parser('setup', help="Configure this clone's merge driver for the task data")
    p_setup.set_defaults(func=cmd_setup)

    p_merge = sub.add_parser('merge-data', help='(git calls this) merge task data by UUID: %%O %%A %%B %%P')
    p_merge.add_argument('base')
    p_merge.add_argument('ours')
    p_merge.add_argument('theirs')
    p_merge.add_argument('path', nargs='?', default='')
    p_merge.set_defaults(func=cmd_merge_data)

    p_log = sub.add_parser('log', help='Show recent commits')
    p_log.add_argument('-n', type=int, default=10, metavar='N',
                       help='Number of commits to show (default: 10)')
    p_log.set_defaults(func=cmd_log)

    p_pull = sub.add_parser('pull', help='Commit local changes then pull (rebase)')
    p_pull.set_defaults(func=cmd_pull)

    p_push = sub.add_parser('push', help='Commit local changes then push')
    p_push.add_argument('--set-upstream', action='store_true',
                        help='Set upstream on first push')
    p_push.add_argument('-m', '--message', metavar='MSG',
                        help='Commit message (default: auto-generated)')
    p_push.set_defaults(func=cmd_push)

    p_sync = sub.add_parser('sync', help='Commit, pull, push (standard workflow)')
    p_sync.add_argument('-m', '--message', metavar='MSG',
                        help='Commit message (default: auto-generated)')
    p_sync.set_defaults(func=cmd_sync)

    p_undo = sub.add_parser('undo', help='Restore to a previous commit (safe: new commit, no rewrite)')
    p_undo.add_argument('--to', metavar='N|HASH',
                        help='List number (1-based) or commit hash to restore to')
    p_undo.add_argument('-n', type=int, default=10, metavar='N',
                        help='Commits to show in interactive list (default: 10)')
    p_undo.set_defaults(func=cmd_undo)

    p_remote = sub.add_parser('remote', help='Manage remotes')
    remote_sub = p_remote.add_subparsers(dest='remote_cmd', required=True)
    r_add = remote_sub.add_parser('add')
    r_add.add_argument('name')
    r_add.add_argument('url')
    r_set = remote_sub.add_parser('set-url')
    r_set.add_argument('name')
    r_set.add_argument('url')
    remote_sub.add_parser('show')
    p_remote.set_defaults(func=cmd_remote)

    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()
