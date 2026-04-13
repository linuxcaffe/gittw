#!/usr/bin/env python3
"""gittw — git-based sync for Taskwarrior ~/.task/"""

import os
import sys
import subprocess
import argparse
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

# awesome-taskwarrior installed packages — reinstall from registry on each machine
awesome-taskwarrior/

# Uncomment to exclude machine-specific managed dirs:
# hooks/
# scripts/
# themes/
"""


def _commit_pending(task_dir, message='auto-commit'):
    """Stage and commit any pending changes. Returns True if a commit was made."""
    result = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        return False
    subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(task_dir), 'commit', '-m', message], check=True)
    return True


def cmd_init(args):
    task_dir = get_task_dir()
    if not task_dir.exists():
        print(f"error: {task_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    git_dir = task_dir / '.git'
    if git_dir.exists():
        print(f"Git repo already exists in {task_dir}")
    else:
        git(['init'], task_dir=task_dir)
        print(f"Initialized git repo in {task_dir}")

    gitignore_path = task_dir / '.gitignore'
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE)
        print(f"Created {gitignore_path}")
    else:
        print(f".gitignore already exists — not overwriting")

    git(['add', '-A'], task_dir=task_dir)
    result = git(['status', '--porcelain'], task_dir=task_dir, capture=True)
    if result.stdout.strip():
        git(['commit', '-m', 'gittw: initial commit'], task_dir=task_dir)
        print("Initial commit created.")
    else:
        print("Nothing to commit.")

    if args.remote:
        r = git(['remote'], task_dir=task_dir, capture=True, check=False)
        if 'origin' in r.stdout.split():
            git(['remote', 'set-url', 'origin', args.remote], task_dir=task_dir)
            print(f"Remote 'origin' updated to {args.remote}")
        else:
            git(['remote', 'add', 'origin', args.remote], task_dir=task_dir)
            print(f"Remote 'origin' set to {args.remote}")
        print(f"\nTo push for the first time:")
        print(f"  gittw push --set-upstream")
    else:
        print(f"\nAdd a remote when ready:")
        print(f"  gittw remote add <url>")


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
    committed = _commit_pending(task_dir, 'gittw: commit before pull')
    if committed:
        print("Committed local changes before pull.")
    git(['pull', '--rebase'], task_dir=task_dir)


def cmd_push(args):
    task_dir = get_task_dir()
    committed = _commit_pending(task_dir, 'gittw: commit before push')
    if committed:
        print("Committed local changes.")
    extra = ['--set-upstream', 'origin', 'HEAD'] if args.set_upstream else []
    git(['push'] + extra, task_dir=task_dir)


def cmd_sync(args):
    """Commit local changes, pull (rebase), then push."""
    task_dir = get_task_dir()

    committed = _commit_pending(task_dir, 'gittw: sync commit')
    if committed:
        print("Committed local changes.")
    else:
        print("Working tree clean.")

    print("Pulling...")
    git(['pull', '--rebase'], task_dir=task_dir)

    print("Pushing...")
    git(['push'], task_dir=task_dir)

    print("Sync complete.")


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
    p_push.set_defaults(func=cmd_push)

    p_sync = sub.add_parser('sync', help='Commit, pull, push (standard workflow)')
    p_sync.set_defaults(func=cmd_sync)

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
