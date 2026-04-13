- Project: https://github.com/linuxcaffe/gittw
- Issues:  https://github.com/linuxcaffe/gittw/issues

# gittw

Git-based sync for Taskwarrior — keep your task database backed up and in sync across machines without a TaskServer.

## TL;DR

- `gittw sync` — commit local changes, pull (rebase), push in one command
- `gittw init` — set up an existing or new `~/.task/` as a git repo, with a sane `.gitignore`
- Detects an existing `~/.task/` git repo, shows what it found, asks before touching anything
- Auto-commit after every Taskwarrior operation via an on-exit hook
- Respects `TW_TASK_DIR` — works with B+C dev/prod isolation
- No TaskServer required. No third-party sync service. Just git and a remote you control.
- Requires Git, Python 3.6+

## Why this exists

Taskwarrior's built-in `task sync` requires a TaskServer (or a hosted service like Inthe.AM). Setting one up is a project in itself, and hosted options have a history of going away. WingTask, a popular hosted option, shut down in 2024. If your sync relied on it, your tasks stopped syncing that day.

Git already does everything sync needs: history, conflict detection, remote hosting on infrastructure you choose. The only missing piece was a thin wrapper that knows about `~/.task/` specifically — what to track, what to ignore, and how to make the commit-pull-push cycle feel like a single operation.

`gittw` is that wrapper. It adds a `.gitignore` that excludes runtime-only files (`undo.data`, `backlog.data`, lock files), runs `pull --rebase` to keep history linear, and auto-commits after every `task` command via a hook.

## What this means for you

Every Taskwarrior operation is automatically committed. When you sit down at a second machine, `gittw sync` brings it up to date. Your task history is in git — browsable, recoverable, and stored wherever you keep your other private repos.

## Core concepts

**One-at-a-time model** — pull before you work, push when you're done. `gittw sync` does both. Concurrent edits from two machines without a sync step in between will produce a rebase conflict — the same way git works everywhere.

**Auto-commit hook** — `on-exit_gittw.py` runs after every `task` command and commits any changes to `~/.task/`. It uses the modified task description as the commit message. You still push manually (or via `gittw sync`).

## Installation

### Option 1 — Manual (current)

```bash
# Clone the repo
git clone git@github.com:linuxcaffe/gittw.git ~/dev/gittw

# Add to PATH
ln -s ~/dev/gittw/gittw.py ~/.local/bin/gittw

# Install the auto-commit hook
ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py
chmod +x ~/.task/hooks/on-exit_gittw.py

# Verify
gittw --help
```

### Option 2 — Via [awesome-taskwarrior](https://github.com/linuxcaffe/awesome-taskwarrior)

```bash
tw -I gittw
```

*(Coming once the .install script and registry entry are added.)*

## Configuration

`gittw` reads `TW_TASK_DIR` if set; otherwise operates on `~/.task/`. No config file needed.

The `.gitignore` written by `gittw init` excludes runtime-only files by default:

```
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
```

Whether to sync `hooks/` and `scripts/` depends on whether those dirs contain the same content on all your machines. If you manage them with awesome-taskwarrior, exclude them and reinstall per machine.

## Usage

```bash
gittw init                          # set up ~/.task/ as a git repo (fresh or existing)
gittw init git@github.com:u/r.git  # same, and set the remote in one step

gittw status                        # working tree status + ahead/behind remote
gittw log                           # last 10 commits
gittw log -n 20                     # last 20 commits

gittw pull                          # commit local changes, then pull --rebase
gittw push                          # commit local changes, then push
gittw push --set-upstream           # first push to a new remote

gittw sync                          # commit + pull --rebase + push (standard daily use)

gittw remote add git@github.com:u/r.git      # add origin
gittw remote set-url git@github.com:u/r.git  # update origin
gittw remote show                            # list remotes
```

All commands work with `TW_TASK_DIR` set — the `td` alias used in B+C isolation works as-is.

## Example workflow

First-time setup on a new machine:

```
1.  git clone git@github.com:you/tasks-private.git ~/.task   # pull existing task data
2.  gittw init                                                 # write .gitignore, set things up
    # (or: gittw init git@github.com:you/tasks-private.git    # if ~/.task/ already exists)
3.  gittw push --set-upstream                                  # first push from this machine
```

Daily use:

```
1.  gittw sync          # pull latest from hub before starting work
    task next           # work normally — hook auto-commits each change
2.  gittw sync          # push when done (or leaving the machine)
```

## Project status

⚠️  Early release (v0.1.0). Core `init`, `sync`, `push`, `pull`, `status`, and `log` commands work. The following are not yet implemented:

- `.install` script and awesome-taskwarrior registry entry
- `tw sync` wrapper integration
- tw-web `/api/sync` endpoint
- Conflict resolution guidance for the rebase-conflict case
- Android / Termux setup documentation

## Further reading

- [awesome-taskwarrior](https://github.com/linuxcaffe/awesome-taskwarrior) — the ecosystem this tool belongs to
- [Taskwarrior hook documentation](https://taskwarrior.org/docs/hooks/) — on-exit hook API
- [git rebase documentation](https://git-scm.com/docs/git-rebase) — how `pull --rebase` keeps history linear

## Metadata

- License: MIT
- Language: Python 3
- Requires: Git, Python 3.6+
- Platforms: Linux
- Version: 0.1.0
