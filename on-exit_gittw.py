#!/usr/bin/env python3
"""on-exit_gittw.py — auto-commit ~/.task/ after every Taskwarrior write operation.

Install: ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py
         chmod +x ~/.task/hooks/on-exit_gittw.py
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

result = subprocess.run(
    ['git', '-C', str(task_dir), 'status', '--porcelain'],
    capture_output=True, text=True
)
if not result.stdout.strip():
    sys.exit(0)

descs = [t.get('description', '')[:50] for t in tasks[:3]]
message = 'task: ' + '; '.join(d for d in descs if d)

subprocess.run(['git', '-C', str(task_dir), 'add', '-A'], check=True)
subprocess.run(['git', '-C', str(task_dir), 'commit', '-m', message], check=True)
