- Project: https://github.com/linuxcaffe/gittw
- Issues:  https://github.com/linuxcaffe/gittw/issues

# gittw

Git-based sync for Taskwarrior — keep your task database backed up and in sync across machines without a TaskServer.

## TL;DR

- `gittw sync` — commit local changes, pull (rebase), push in one command
- **Several stations** (a laptop, a phone) can both change tasks between syncs: a merge driver merges
  `pending.data` and `completed.data` **by task UUID**, so two new tasks, or edits to different tasks,
  never conflict; a task changed on both sides keeps the later change (0.3.0)
- Every commit names its station (`Station: s5`), `gittw status` shows what's incoming from which
  station and when each one last synced; `--json` for clients (0.3.0)
- `gittw sync` runs Taskwarrior's gc once, right after pulling, so a station with `gc=off` keeps stable
  IDs and never moves tasks between files on a mere read (0.3.0)
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

**Stations** — each machine with a clone of `~/.task` is a station, named by `gittw.device` in its
untracked `~/.task/local.rc` (else its hostname). Sync often; when two stations did change tasks between
syncs, the merge driver sorts it out (below), and `gittw status` says what's waiting.

**Merging task data by UUID** — Taskwarrior keeps one task per line, and git merges by line, so two
stations that each added a task (both append to `pending.data`) would conflict. `gittw merge-data` is a
git merge driver for `pending.data` and `completed.data` (named in the tracked `.gitattributes`,
configured per clone by `gittw setup`, or by the first `gittw sync`). It merges three ways, task by task:

- a task only one side changed takes that change; new tasks from both sides are kept;
- a task both sides changed keeps the copy with the later `modified` (last write wins, as taskd did);
- removed on one side and changed on the other: the change is kept, so nothing is lost silently;
- this side's order first, then the other side's new tasks, so IDs here stay put.

Each decision goes to `.git/gittw-merge.log`. After the pull, `sync` drops a task found in both
`pending.data` and `completed.data` (keeping the newer copy) and **reports** a recurring template that
ended up with two pending instances (two stations each made one): which to keep is yours to decide.

**A conflict gittw can't merge** (a file other than the task data, both sides changed it): the pull
is undone, the repo is back on its branch with its own commits, and the error names the files
(0.3.2; before, the repo was left stopped mid-rebase and every later sync failed). A sync refuses to
start while a rebase is stopped, and says how to finish or undo it.

**tw-events' files never stop a sync** (0.3.6): `config/my-events.txt` only ever gets lines added,
so it merges as a union (both stations' new events are kept). The files tw-events generates
(`holidays.rc`, `sky.rc`, `mine.rc`) take either side (the `gittw-keep` driver), and after the pull
`sync` runs `events refresh --no-fetch` (tw-events 0.3.1+, offline), which rebuilds them from the merged
settings and events.

**A dropped connection is tried again** (0.3.3): a phone's network drops connections now and then
("Software caused connection abort" in the middle of a pull). A pull or push that fails that way is
retried twice, after 3 and 10 seconds; any other failure is reported at once.

**The mirror gets only what the main remote took** (0.3.7): with two push URLs on `origin`
(Codeberg + GitHub), gittw pushes to the URL `origin` fetches from first, and to the mirror only once
that succeeded. The mirror is a copy that nothing reads, so its push is forced: a mirror left out of
step (a push that reached only it, before 0.3.7) is put right at the next sync. A refusal at the main
remote is a failed sync and the mirror gets nothing; a mirror that's unreachable only warns.

**What `tw` installed** — `config/.tw_manifest`. tw before 2.18.0 wrote it with each station's own
absolute paths, so a shared copy conflicted at every install, and repos stopped tracking it: every
station keeps its own copy through the pull that drops it (`KEEP_WHEN_UNTRACKED`, 0.3.2). tw 2.18.0
writes `~/.task/…` paths, so the file is the same on every station and a repo can track it again
(0.3.4): a station still holding its own untracked copy takes the shared one, and its copy is kept
in `.git/gittw-kept/`. New repos no longer ignore it.

**gc at sync** — with `gc=off` in `.taskrc`, Taskwarrior never renumbers IDs or moves finished tasks
on its own; `gittw sync` runs gc once, right after pulling, when this station is current, and commits
the move. `gittw.gc_at_sync = no` turns that off.

**Last seen** — every sync writes `.stations/<name>` (tracked) with the time, so the other stations know
when this one last synced: "s5 synced 3 h ago" is the hint that it may have changes not pushed yet.

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

### Option 2 — Android / Termux

gittw runs well in Termux. Taskwarrior 2.6.x must be installed manually — the Termux package manager provides TW3, which is a different codebase.

#### 1. Install Termux prerequisites

Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) (recommended over the Play Store build), then:

```bash
pkg update && pkg upgrade -y
pkg install python git openssh
```

#### 2. Install Taskwarrior 2.6.x

The Termux `taskwarrior` package is TW3 and will conflict. A pre-built TW 2.6.2 `.deb` for `aarch64` is included in this repo. The cleanest way to transfer it is `scp` directly into the Termux home — no Android storage permissions needed:

```bash
# On your desktop:
scp task2_2.6.2-3_aarch64.deb phone:~/

# On the phone in Termux:
dpkg -i ~/task2_2.6.2-3_aarch64.deb
task --version   # should report 2.6.2
```

#### 3. Install gittw

```bash
git clone https://github.com/linuxcaffe/gittw.git ~/dev/gittw
mkdir -p ~/.local/bin
ln -s ~/dev/gittw/gittw.py ~/.local/bin/gittw
chmod +x ~/dev/gittw/gittw.py
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
gittw --help
```

#### 4. Add your phone's SSH key to GitHub

Generate a key on the phone, then add it from a desktop machine that already has the `gh` CLI authenticated — no GitHub web interface needed:

```bash
# On the phone:
ssh-keygen -t ed25519 -C "termux-phone"
cat ~/.ssh/id_ed25519.pub
# Copy the output

# On your desktop:
gh ssh-key add - --title "termux-phone" <<< "paste-key-here"
```

#### 5. Clone your existing task data

If you already have a gittw remote, clone it directly — your tasks and config arrive immediately:

```bash
# If ~/.task was created by a prior `task` invocation, remove it first:
rmdir ~/.task
git clone git@github.com:you/tasks-private.git ~/.task

# Wire up ~/.taskrc if you tracked it (symlink it from the cloned copy):
ln -s ~/.task/.taskrc ~/.taskrc

gittw init      # writes .gitignore, verifies setup
```

If starting fresh with no existing remote, use `gittw init` to create one and then `gittw push --set-upstream` after adding a remote.

#### 6. Install the auto-commit hook

```bash
ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py
chmod +x ~/.task/hooks/on-exit_gittw.py
```

#### 7. Verify

```bash
task next
gittw status
gittw sync
```

From this point, daily use is identical to any other machine: `gittw sync` before and after a work session.

### Option 3 — Via [awesome-taskwarrior](https://github.com/linuxcaffe/awesome-taskwarrior)

```bash
tw -I gittw
```

Installs `gittw` to `~/.task/scripts/`, the hook to `~/.task/hooks/`, and `config/gittw.rc` (added to
`included.rc`). The hook is on by default.

## Configuration

`gittw` reads `TW_TASK_DIR` if set; otherwise operates on `~/.task/`.

### ~/.task/config/gittw.rc

```ini
gittw.autocommit  = yes   # no: the hook stays installed but doesn't commit
gittw.gc_at_sync  = yes   # no: sync doesn't run Taskwarrior's gc after pulling
```

`gittw.device` (this station's name) goes in `~/.task/local.rc`, which is per station and untracked;
a `gittw.*` setting there also wins over `gittw.rc`.

The hook reads this file directly (calling `task` from inside a hook would be slow). To take the hook out
of the chain entirely, use [hook-ctrl](https://github.com/linuxcaffe/tw-hook-ctrl). Each commit costs
about 30 ms.

### ~/.taskrc

`~/.taskrc` is your Taskwarrior config — UDAs, report definitions, theme, hooks settings. It lives outside `~/.task/` by default, which means it would be missed by a naive git sync.

`gittw init` detects this and offers to move `~/.taskrc` into `~/.task/.taskrc` and replace it with a symlink. Taskwarrior finds it transparently; gittw tracks it automatically.

To do it manually:

```bash
mv ~/.taskrc ~/.task/.taskrc
ln -s ~/.task/.taskrc ~/.taskrc
```

On a new machine cloned from your remote, the file arrives as `~/.task/.taskrc`. Create the symlink once after cloning:

```bash
ln -s ~/.task/.taskrc ~/.taskrc
```

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

gittw status                        # working tree, ahead/behind, incoming by station, stations last seen
gittw status --fetch --json         # the same, fetched first, as JSON
gittw setup                         # configure this clone's merge driver (sync does it too)
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
1.  git clone git@github.com:you/tasks-private.git ~/.task       # pull existing task data
2.  ln -s ~/.task/.taskrc ~/.taskrc                               # wire up config (if you tracked it)
3.  gittw init                                                     # write .gitignore, set things up
4.  git-crypt unlock ~/.git-crypt-task.key                         # decrypt credentials (if using git-crypt)
5.  ln -s ~/dev/gittw/on-exit_gittw.py ~/.task/hooks/on-exit_gittw.py  # install hook
6.  gittw push --set-upstream                                      # first push from this machine
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

- Conflict resolution guidance for a rebase conflict in a file other than the task data

## Further reading

- [awesome-taskwarrior](https://github.com/linuxcaffe/awesome-taskwarrior) — the ecosystem this tool belongs to
- [Taskwarrior hook documentation](https://taskwarrior.org/docs/hooks/) — on-exit hook API
- [git rebase documentation](https://git-scm.com/docs/git-rebase) — how `pull --rebase` keeps history linear

## Metadata

- License: MIT
- Language: Python 3
- Requires: Git, Python 3.6+
- Platforms: Linux, Android (Termux)
- Version: 0.3.5
