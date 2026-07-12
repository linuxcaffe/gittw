#!/usr/bin/env python3
"""gittw — git-based sync for Taskwarrior ~/.task/"""

import os
import sys
import subprocess
import argparse
from datetime import datetime
from pathlib import Path


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
    subprocess.run(['git', '-C', str(task_dir), 'commit', '-m', message], check=True)
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

    git(['add', '-A'], task_dir=task_dir)
    result = git(['status', '--porcelain'], task_dir=task_dir, capture=True)
    if result.stdout.strip():
        git(['commit', '-m', 'gittw: initial commit'], task_dir=task_dir)
        print("Initial commit created.")
    else:
        print("Nothing to commit.")

    _set_remote(task_dir, args.remote)


def cmd_status(args):
    task_dir = get_task_dir()
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


def cmd_log(args):
    git(['log', '--oneline', f'-{args.n}'])


def cmd_pull(args):
    task_dir = get_task_dir()
    committed = _commit_pending(task_dir)
    if committed:
        print("Committed local changes before pull.")
    git(['pull', '--rebase'], task_dir=task_dir)


def cmd_push(args):
    task_dir = get_task_dir()
    msg = args.message or 'gittw: commit before push'
    committed = _commit_pending(task_dir, msg)
    if committed:
        print("Committed local changes.")
    extra = ['--set-upstream', 'origin', 'HEAD'] if args.set_upstream else []
    git(['push'] + extra, task_dir=task_dir)


def cmd_sync(args):
    """Commit local changes, pull (rebase), then push."""
    task_dir = get_task_dir()

    msg = args.message or 'gittw: sync commit'
    committed = _commit_pending(task_dir, msg)
    if committed:
        print("Committed local changes.")
    else:
        print("Working tree clean.")

    print("Pulling...")
    git(['pull', '--rebase'], task_dir=task_dir)

    print("Pushing...")
    git(['push'], task_dir=task_dir)

    print("Sync complete.")


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
    sub = parser.add_subparsers(dest='command', required=True)

    p_init = sub.add_parser('init', help='Initialize git repo in ~/.task/')
    p_init.add_argument('remote', nargs='?', metavar='REMOTE_URL',
                        help='Optional remote URL (e.g. git@github.com:user/tasks.git)')
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser('status', help='Show working tree status')
    p_status.set_defaults(func=cmd_status)

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
    args.func(args)


if __name__ == '__main__':
    main()
