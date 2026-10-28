#!/bin/bash
# One-command start: sets up the virtualenv on first run, then runs the bot
# with caffeinate so the Mac doesn't idle-sleep while it's on.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec caffeinate -i .venv/bin/python bot.py
