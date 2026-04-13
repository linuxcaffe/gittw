#!/usr/bin/env python3
"""on-exit_gittw.py — auto-commit ~/.task/ after every Taskwarrior operation.

Install: ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py
         chmod +x ~/.task/hooks/on-exit_gittw.py
"""

import os
import sys
import json
import subprocess
from pathlib import Path


def main():
    task_dir = Path(os.environ.get('TW_TASK_DIR', Path.home() / '.task'))

    # Skip if not a git repo
    if not (task_dir / '.git').exists():
        sys.exit(0)

    # Read modified tasks from stdin.
    # on-exit receives one JSON line per modified task — empty stdin means
    # read-only operation (export, next, list, etc.): skip.
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

    # Check for changes
    result = subprocess.run(
        ['git', '-C', str(task_dir), 'status', '--porcelain'],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        sys.exit(0)

    # Build commit message from task descriptions
    if tasks:
        descs = [t.get('description', '')[:50] for t in tasks[:3]]
        summary = '; '.join(d for d in descs if d)
        message = f"task: {summary}"
    else:
        message = 'task: auto-commit'

    subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(task_dir), 'commit', '-m', message], check=True)


if __name__ == '__main__':
    main()
