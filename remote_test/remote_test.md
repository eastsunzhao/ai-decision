# Remote Test Flow

This document records the lightweight remote test flow we used on `2026-04-21`.

Goal:
- Do not use Jenkins
- Do not require `git push`
- Sync the current local workspace directly to a remote test directory
- Create a virtual environment on the server
- Start the Flask app in the foreground for manual testing
- Stop it with `Ctrl+C` after testing
- Mind that if some steps are already done (such as mkdir and install venv), skip them.

## Remote Target

- Host: `192.168.0.13`
- User: `u9000`, passcode `jq123`
- Remote directory: `/home/u9000/tavily_search_nanobot-dev-5006-lite`
- Port: `5006`

## Overview

The flow has four steps:

1. Create the remote test directory.
2. Sync the current local code to the remote directory with `rsync`.
3. Create a remote `.venv` and install dependencies.
4. Start the app in the foreground with the remote `.venv`.

This keeps the process simple and avoids the heavier `scripts/deploy/` release flow.

## Step 1: Create the Remote Directory

Run:

```bash
ssh u9000@192.168.0.13 'mkdir -p /home/u9000/tavily_search_nanobot-dev-5006-lite'
```

This creates an isolated remote directory for the lightweight test instance.

## Step 2: Sync Local Code to the Remote Directory

Run this from the repo root:

```bash
rsync -az \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.DS_Store' \
  ./ \
  u9000@192.168.0.13:/home/u9000/tavily_search_nanobot-dev-5006-lite/
```

Notes:

- This syncs the current local workspace directly to the server.
- It includes local changes that exist on disk, even if they are not committed.
- It excludes obvious local noise such as `.git`, local virtualenvs, caches, and `.DS_Store`.

## Step 3: Create a Remote Virtual Environment and Install Dependencies

The remote machine rejected system-wide `pip install` because Python is externally managed, so we created a project-local `.venv` instead.

Run:

```bash
ssh u9000@192.168.0.13 '
  cd /home/u9000/tavily_search_nanobot-dev-5006-lite &&
  python3 -m venv .venv &&
  .venv/bin/python -m pip install -r requirements.txt
'
```

Notes:

- `requirements.txt` includes `-e ./nanobot`, so the local `nanobot` package is installed as an editable package inside the remote `.venv`.
- This step only needs to be repeated when dependencies change or the `.venv` is removed.

## Step 4: Start the App in the Foreground

Run:

```bash
ssh -tt u9000@192.168.0.13 '
  cd /home/u9000/tavily_search_nanobot-dev-5006-lite &&
  PYTHONPATH=/home/u9000/tavily_search_nanobot-dev-5006-lite/nanobot \
  WEB_HOST=0.0.0.0 \
  WEB_PORT=5006 \
  .venv/bin/python web/app.py
'
```

Why this command is written this way:

- `ssh -tt`: forces an interactive remote TTY so the foreground app behaves like a normal terminal process.
- `cd ...`: starts from the project root on the remote server.
- `PYTHONPATH=.../nanobot`: makes imports stable for `web/app.py`.
- `WEB_HOST=0.0.0.0`: exposes the app on the LAN.
- `WEB_PORT=5006`: runs the test instance on port `5006`.
- `.venv/bin/python`: uses the project-local Python environment instead of the system Python.

When it starts successfully, Flask prints URLs similar to:

```text
 * Running on http://127.0.0.1:5006
 * Running on http://192.168.0.13:5006
```

Then open:

```text
http://192.168.0.13:5006
```

## How to Stop It

This process runs in the foreground inside the SSH session.

When testing is finished, press:

```text
Ctrl+C
```

Because the app is attached to the interactive SSH session, `Ctrl+C` is delivered to the remote foreground process and stops:

```bash
.venv/bin/python web/app.py
```

After that, the SSH session exits and port `5006` is released.

## Why We Did Not Use `scripts/deploy/`

The existing `scripts/deploy/` flow is more suitable for repeatable deployment and rollback:

- release archives
- `current` / `previous` symlinks
- shared runtime directories
- health checks
- restart scripts

For this test cycle, the goal was much simpler:

- upload current code quickly
- run it once
- inspect behavior
- stop it manually

So the lightweight sync + `.venv` + foreground run approach was a better fit.

## Repeatable Minimal Workflow

From the repo root:

```bash
ssh u9000@192.168.0.13 'mkdir -p /home/u9000/tavily_search_nanobot-dev-5006-lite'
```

```bash
rsync -az \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.DS_Store' \
  ./ \
  u9000@192.168.0.13:/home/u9000/tavily_search_nanobot-dev-5006-lite/
```

```bash
ssh u9000@192.168.0.13 '
  cd /home/u9000/tavily_search_nanobot-dev-5006-lite &&
  python3 -m venv .venv &&
  .venv/bin/python -m pip install -r requirements.txt
'
```

```bash
ssh -tt u9000@192.168.0.13 '
  cd /home/u9000/tavily_search_nanobot-dev-5006-lite &&
  PYTHONPATH=/home/u9000/tavily_search_nanobot-dev-5006-lite/nanobot \
  WEB_HOST=0.0.0.0 \
  WEB_PORT=5006 \
  .venv/bin/python web/app.py
'
```

## Suggested Next Step

If this becomes a frequent workflow, the next improvement is to wrap these commands into one small script, for example:

- `remote_test/run_remote_test.sh`
- or `remote_test/run_remote_test.py`

That script can:

- sync code
- create `.venv` only if missing
- optionally reinstall dependencies
- start the app in the foreground

while still keeping the workflow simple.
