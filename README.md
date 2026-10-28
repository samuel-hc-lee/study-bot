# Study accountability bot

A Telegram bot that messages you at random times during your study hours and asks whether you're actually studying. Every answer, and every non-answer, is logged, so `/stats` shows your real on-task rate.

## Features

- **Random check-ins** every 30–75 min during study hours (default 09:00–22:00). You never know when the next one is coming.
- **Four buttons:** 📚 Studying / ☕ Break / 📱 Distracted / 📌 Commitment.
  - Break or Distracted → it checks back in 15 min.
  - Commitment (class, work) → asks how long you're busy (1–4h or rest of today), pauses check-ins until then, and doesn't count against your on-task %.
- **Nudge** if you don't reply within 10 min. No reply is logged as a miss.
- **Morning plan → evening review:** at the start of study hours it asks for your plan; at the end it shows your on-task % and quotes the plan back.
- **Exam countdown** on every check-in (`/exam MATH1012 2026-11-09`).
- **Commands:** `/now`, `/snooze 90`, `/stop`, `/resume`, `/stats`, `/exam`, `/exams`, `/delexam`, `/help`.
- **Private:** only the first chat to send `/start` can use it (or set `ALLOWED_CHAT_ID`).

## Setup (about 5 min)

You need Python 3.9+ and your **own** Telegram bot. Each person runs their own copy.

1. **Create a bot:** in Telegram, message **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`. Copy the token it gives you.
2. **Download and install:** on this repo's GitHub page, click the green **Code** button and either copy the clone command or choose **Download ZIP** (then unzip it). Open Terminal in that folder and run:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
3. **Configure:** open `.env`, paste your token after `TELEGRAM_BOT_TOKEN=`, and set `TIMEZONE` to yours (e.g. `Australia/Sydney`, `Australia/Melbourne`, `Europe/London`).
4. **Run it:** `.venv/bin/python bot.py`
5. **Claim it:** open your bot in Telegram and send `/start`. Send `/now` to test a check-in.

Never commit or share `.env`. Anyone with your token controls your bot.

## Keeping it running

The bot only sends messages while it's running.

**macOS (recommended):** run `./install-autostart.sh` once. It:
- starts the bot now and at every login, and restarts it if it crashes
- pauses while the Mac sleeps and catches up on wake
- restarts on the new version within ~1 min whenever you edit `bot.py` or `.env`
- logs to `bot.log`

Undo with `./uninstall-autostart.sh`. If it's not responding, run `./fix.sh` (restarts it and writes a health check to `fix.txt`) or `./diagnose.sh` (writes `diagnose.txt`).

**Linux / Windows / a server:** run `.venv/bin/python bot.py` under whatever keeps processes alive (systemd, `tmux`, Task Scheduler). It uses long polling, so it needs no public URL, webhook or open ports. It's light: about 50 MB RAM and near-zero CPU.

## Config (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | From @BotFather (required) |
| `ALLOWED_CHAT_ID` | blank | Lock to one chat; blank = first `/start` owns it |
| `TIMEZONE` | `Australia/Sydney` | Your IANA timezone |
| `DAY_START` / `DAY_END` | `09:00` / `22:00` | Study hours |
| `MIN_GAP_MINUTES` / `MAX_GAP_MINUTES` | `30` / `75` | Random gap between check-ins |
| `NUDGE_AFTER_MINUTES` | `10` | Re-ping if no reply |
| `FOLLOWUP_MINUTES` | `15` | Re-check after Break/Distracted |

Your data stays in `studybot.db` on your machine. Delete it to reset.
