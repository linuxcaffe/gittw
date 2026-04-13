- Project: https://github.com/linuxcaffe/gittw
- Issues:  https://github.com/linuxcaffe/gittw/issues

# gittw

Git-based sync for Taskwarrior — keep your task database backed up and in sync across machines without a TaskServer.

## TL;DR

- `gittw sync` — commit local changes, pull (rebase), push in one command
- `gittw init` — set up an existing or new `~/.task/` as a git repo, with a sane `.gitignore`
- Detects an existing `~/.task/` git repo, shows what it found, asks before touching anything
- Auto-commit after every Taskwarrior operation via an on-exit hook
- `tw sync` routes to `gittw sync` via the awesome-taskwarrior wrapper system
- Sync button in [tw-web](https://github.com/linuxcaffe/WebTaskManager) calls `/api/sync` → `gittw sync`
- Optional `git-crypt` integration encrypts credential files transparently — safe to push to GitHub
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

The hook is designed to be safe by default:
- Skips silently on read-only operations (export, next, list) — no empty commits
- Skips if git is mid-operation (merge, rebase, cherry-pick in progress)
- All git errors are caught silently — a git failure never blocks a `task` command

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

# Debug and cache logs
logs/
**/logs/

# Runtime-written state files (change on every task command)
config/nag-state.rc
config/tod.rc

# Editor artifacts — never commit
*.swp
*.history

# awesome-taskwarrior installed packages — reinstall from registry on each machine
awesome-taskwarrior/

# Uncomment to exclude machine-specific managed dirs:
# hooks/
# scripts/
# themes/
```

Whether to sync `hooks/` and `scripts/` depends on whether those dirs contain the same content on all your machines. If you manage them with awesome-taskwarrior, exclude them and reinstall per machine.

### Encrypting credential files with git-crypt

Config files like `*.rc` are useful to sync across machines. Some of those files may contain credentials (API keys, bot tokens, etc.). To sync them safely without storing plaintext secrets on GitHub:

```bash
# Install git-crypt
sudo apt install git-crypt          # Debian/Ubuntu
brew install git-crypt              # macOS

# Initialise in your ~/.task repo
cd ~/.task
git-crypt init

# Mark credential files for encryption
cat >> .gitattributes << 'EOF'
*.creds filter=git-crypt diff=git-crypt
EOF

# Add and commit — git-crypt encrypts them automatically
git add .gitattributes config/*.creds
git commit -m "git-crypt: encrypt credential files"

# Export the key — keep this safe, outside the repo
git-crypt export-key ~/.git-crypt-task.key
```

To unlock on another device (one-time setup after cloning):

```bash
git-crypt unlock /path/to/.git-crypt-task.key
```

**The key must travel via a secure channel** (local network copy, USB, Signal self-message) — never through the git repo or email.

After unlocking, `gittw sync` works normally: credentials are encrypted at rest in the repo and decrypted transparently on unlocked machines.

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
3.  git-crypt unlock ~/.git-crypt-task.key                     # decrypt credentials (if using git-crypt)
4.  ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py  # install hook
5.  gittw push --set-upstream                                  # first push from this machine
```

Daily use:

```
1.  gittw sync          # pull latest from hub before starting work
    task next           # work normally — hook auto-commits each change
2.  gittw sync          # push when done (or leaving the machine)
```

### tw sync integration

If you use the [awesome-taskwarrior](https://github.com/linuxcaffe/awesome-taskwarrior) `tw` wrapper, `tw sync` routes to `gittw sync` automatically. Register the shim:

```bash
# Install the shim
cp ~/dev/gittw/tw-sync.sh ~/.task/scripts/tw-sync
chmod +x ~/.task/scripts/tw-sync

# Register with the tw wrapper
echo 'sync|tw-sync|Git-based sync (gittw)|command' >> ~/.task/config/.tw_wrappers
```

### tw-web Sync button

If you run [tw-web](https://github.com/linuxcaffe/WebTaskManager), the hamburger menu Sync button calls `POST /api/sync` on the Flask backend, which runs `gittw sync`. No additional configuration needed once gittw is in `$PATH`.

## Project status

Core `init`, `sync`, `push`, `pull`, `status`, and `log` commands work. `tw sync` wrapper and tw-web `/api/sync` endpoint are implemented. The following are not yet implemented:

- `.install` script and awesome-taskwarrior registry entry
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
