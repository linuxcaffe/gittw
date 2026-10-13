#!/usr/bin/env python3
"""on-exit_gittw.py — auto-commit ~/.task/ after every Taskwarrior write operation.

Install: tw -I gittw   (or: ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/ && chmod +x …)
Off:     gittw.autocommit = no  in ~/.task/config/gittw.rc, or hook-ctrl (chmod -x).
"""

import sys
import json

# Read stdin first — empty means read-only operation (export, next, list, etc.): skip.
# Doing this before any heavy imports keeps passthrough cost minimal (~sys+json only).
tasks = []
for line in sys.stdin:
    line = line.strip()
    if line:
        try:
            tasks.append(json.loads(line))
        except json.JSONDecodeError:
            pass

if not tasks:
    sys.exit(0)

# Write operation confirmed — now import heavier modules
import os
import subprocess
from pathlib import Path

task_dir = Path(os.environ.get('TW_TASK_DIR', Path.home() / '.task'))

if not (task_dir / '.git').exists():
    sys.exit(0)


def autocommit_enabled():
    """gittw.autocommit in config/gittw.rc; read directly, since calling `task` from a hook is slow."""
    try:
        for line in (task_dir / 'config' / 'gittw.rc').read_text().splitlines():
            key, sep, value = line.split('#', 1)[0].partition('=')
            if sep and key.strip() == 'gittw.autocommit':
                return value.strip().lower() not in ('no', 'off', 'false', '0')
    except OSError:
        pass
    return True


if not autocommit_enabled():
    sys.exit(0)

try:
    # Bail if git is mid-operation (merge, rebase, cherry-pick)
    for sentinel in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REBASE_HEAD', 'rebase-merge', 'rebase-apply'):
        if (task_dir / '.git' / sentinel).exists():
            sys.exit(0)

    result = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        sys.exit(0)

    descs = [t.get('description', '')[:50] for t in tasks[:3]]
    message = 'task: ' + '; '.join(d for d in descs if d)

    subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], capture_output=True)
    subprocess.run(['git', '-C', str(task_dir), 'commit', '-m', message], capture_output=True)
except Exception:
    pass  # Never let gittw failures surface to Taskwarrior
