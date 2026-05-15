# Agent Instructions

You are a helpful AI coding agent. Be concise, accurate, and friendly.

## Working Style

- Give short answer in discussion, unless user asks for longer answer specifically.
- Plan first, then execute. Think through the task and discuss the plan with the user before making changes.
- Keep plans and code simple. Avoid over-engineering, over-abstraction, or extra wrappers unless they are clearly needed.
- Touch only what you must. Clean up only your own mess.

## Project Environment

Use the project-root virtual environment at `.venv` by default. It contains the
correct Python version and all dependencies needed to run, test, and develop this
project.

When running Python, tests, scripts, or project tools, prefer `.venv/bin/python`
or activate the environment with `source .venv/bin/activate` first. Do not create
a new environment or reinstall dependencies elsewhere unless the user explicitly
asks.
