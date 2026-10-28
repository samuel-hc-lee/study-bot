"""Study accountability bot (MVP).

Messages you on Telegram at random times during study hours and asks whether
you're actually studying. Every answer (or non-answer) is logged to SQLite so
/stats can show how you're really tracking.

Run:  python bot.py   (needs TELEGRAM_BOT_TOKEN in .env)
"""
from __future__ import annotations

import functools
import logging
import os
import random
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ---------------------------------------------------------------- config ----
TZ = ZoneInfo(os.getenv("TIMEZONE", "Australia/Sydney"))
DAY_START = time.fromisoformat(os.getenv("DAY_START", "09:00"))
DAY_END = time.fromisoformat(os.getenv("DAY_END", "22:00"))
MIN_GAP = float(os.getenv("MIN_GAP_MINUTES", "30"))     # random gap between check-ins
MAX_GAP = float(os.getenv("MAX_GAP_MINUTES", "75"))
NUDGE_AFTER = float(os.getenv("NUDGE_AFTER_MINUTES", "10"))  # follow-up if no reply
FOLLOWUP = float(os.getenv("FOLLOWUP_MINUTES", "15"))   # re-check after break/distracted
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")          # optional; else first /start owns the bot
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).with_name("studybot.db")))

CHECKIN_JOB = "checkin"

# ----------------------------------------------------------------- copy -----
PROMPTS = [
    "Check-in. Are you studying right now?",
    "Honest answer: what are you doing right now?",
    "Random check. Studying?",
    "Is what's on your screen the thing you should be studying?",
    "Where's your attention right now?",
]
NUDGES = [
    "No reply. If you were studying, tapping a button takes two seconds.",
    "Still waiting. Silence gets logged as a miss.",
]
RESPONSES = {
    "studying": ["Good. Keep going.", "Logged. Back to it.", "Nice. Don't break the streak."],
    "break": ["Fine. Keep it short.", "Take it, then get straight back to it."],
    "distracted": [
        "Thanks for being honest. Close the tab, phone face-down, start the next problem.",
        "Logged. Pick the smallest next task and start it now.",
    ],
}
HELP = (
    "I'll message you at random times between {start} and {end} to check you're studying.\n\n"
    "/now – check in right now\n"
    "/snooze 90 – no check-ins for 90 min (lecture, gym, dinner)\n"
    "/stop – pause until /resume\n"
    "/resume – start again\n"
    "/stats – today and the last 7 days\n"
    "/exam MATH1012 2026-11-09 – add an exam (countdown shows in check-ins)\n"
    "/exams – list exams · /delexam MATH1012 – remove one\n\n"
    "Check-in buttons: 📚 Studying · ☕ Break · 📱 Distracted · 📌 Commitment.\n"
    "Commitment is for unavoidable things (class, work). It asks how long you're busy, pauses check-ins until then, "
    "and doesn't count against your on-task %.\n\n"
    "Reply with text after a check-in to log what you're working on."
)

log = logging.getLogger("studybot")

# ------------------------------------------------------------------- db -----

def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        return conn.execute(sql, params).lastrowid


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_ts REAL NOT NULL,       -- unix time
                answered_ts REAL,
                status TEXT,                 -- studying | break | distracted | NULL = no reply
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS exams (name TEXT PRIMARY KEY, exam_date TEXT NOT NULL);
            """
        )


def get_setting(key: str, default: str | None = None) -> str | None:
    rows = query("SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


def set_setting(key: str, value: str | None) -> None:
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def owner_id() -> str | None:
    return ALLOWED_CHAT_ID or get_setting("chat_id")


def is_owner(chat_id: int) -> bool:
    owner = owner_id()
    return owner is not None and str(chat_id) == str(owner)


def is_stopped() -> bool:
    return get_setting("stopped", "0") == "1"


# ----------------------------------------------------------------- time -----

def now() -> datetime:
    return datetime.now(TZ)


def push_into_window(t: datetime) -> datetime:
    """Move t into study hours. Outside them, jump to the next DAY_START (+ jitter)."""
    t = t.astimezone(TZ)
    start = datetime.combine(t.date(), DAY_START, TZ)
    end = datetime.combine(t.date(), DAY_END, TZ)
    jitter = timedelta(minutes=random.uniform(0, 20))
    if t < start:
        return start + jitter
    if t >= end:
        return start + timedelta(days=1) + jitter
    return t


def in_hours(t: datetime) -> bool:
    return DAY_START <= t.astimezone(TZ).time() < DAY_END


def random_gap() -> timedelta:
    return timedelta(minutes=random.uniform(MIN_GAP, MAX_GAP))


def fmt_when(t: datetime) -> str:
    return f"{t:%H:%M}" + ("" if t.date() == now().date() else f" {t:%a}")


def quiet_until() -> datetime | None:
    """End of the current snooze / commitment pause, if one is active."""
    v = get_setting("quiet_until")
    if not v:
        return None
    t = datetime.fromtimestamp(float(v), TZ)
    return t if t > now() else None


def next_regular_time() -> datetime:
    t = now() + random_gap()
    q = quiet_until()
    return max(t, q) if q else t


# ---------------------------------------------------------------- exams -----

def days_until(d: date) -> int:
    return (d - now().date()).days


def upcoming_exams() -> list[tuple[str, date]]:
    rows = query("SELECT name, exam_date FROM exams ORDER BY exam_date")
    exams = [(r["name"], date.fromisoformat(r["exam_date"])) for r in rows]
    return [(n, d) for n, d in exams if days_until(d) >= 0]


def countdown(name: str, d: date) -> str:
    n = days_until(d)
    when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
    return f"{name} {when}"


def exam_line() -> str:
    exams = upcoming_exams()
    return f"\n\n⏳ {countdown(*exams[0])}." if exams else ""


# ---------------------------------------------------------------- stats -----

def day_counts(d: date) -> dict[str, int]:
    start = datetime.combine(d, time.min, TZ)
    end = start + timedelta(days=1)
    rows = query(
        "SELECT status FROM checkins WHERE sent_ts >= ? AND sent_ts < ?",
        (start.timestamp(), end.timestamp()),
    )
    counts = {"studying": 0, "break": 0, "distracted": 0, "commitment": 0, "missed": 0}
    for r in rows:
        counts[r["status"] or "missed"] += 1
    counts["total"] = len(rows)
    counts["counted"] = len(rows) - counts["commitment"]  # commitments don't count against you
    return counts


def pct(c: dict[str, int]) -> str:
    return f"{round(100 * c['studying'] / c['counted'])}%" if c["counted"] else "–"


def stats_text() -> str:
    today = now().date()
    c = day_counts(today)
    lines = [
        f"Today: {c['total']} check-ins. {c['studying']} studying, {c['break']} break, "
        f"{c['distracted']} distracted, {c['missed']} no reply, {c['commitment']} commitment.",
        f"On task: {pct(c)} (commitments excluded)",
        "",
        "Last 7 days (studying / check-ins, excl. commitments):",
    ]
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        dc = day_counts(d)
        lines.append(f"{d:%a %d %b}  {dc['studying']}/{dc['counted']}  {pct(dc)}")
    return "\n".join(lines)


# ------------------------------------------------------------ scheduling ----

def cancel_jobs(job_queue, name: str) -> None:
    for job in job_queue.get_jobs_by_name(name):
        try:
            job.schedule_removal()
        except Exception:  # job already fired/removed
            pass


def schedule_checkin(job_queue, chat_id: int, when: datetime) -> datetime:
    """Replace any pending check-in with one at `when` (pushed into study hours)."""
    cancel_jobs(job_queue, CHECKIN_JOB)
    when = push_into_window(when)
    # misfire_grace_time=None: if the Mac was asleep when this was due, run it on
    # wake instead of silently dropping it (which would break the check-in chain).
    job_queue.run_once(
        checkin_job, when=when, chat_id=chat_id, name=CHECKIN_JOB,
        job_kwargs={"misfire_grace_time": None},
    )
    log.info("Next check-in at %s", when.isoformat(timespec="minutes"))
    return when


def pause_until(job_queue, chat_id: int, until: datetime) -> datetime:
    """Silence check-ins until `until` (persists across restarts)."""
    set_setting("quiet_until", str(until.timestamp()))
    return schedule_checkin(job_queue, chat_id, until)


def keyboard(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Studying", callback_data=f"studying:{cid}"),
            InlineKeyboardButton("☕ Break", callback_data=f"break:{cid}"),
        ],
        [
            InlineKeyboardButton("📱 Distracted", callback_data=f"distracted:{cid}"),
            InlineKeyboardButton("📌 Commitment", callback_data=f"commitment:{cid}"),
        ],
    ])


def busy_keyboard(cid: int) -> InlineKeyboardMarkup:
    hours = [1, 2, 3, 4]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{h}h", callback_data=f"busy:{h * 60}:{cid}") for h in hours],
        [InlineKeyboardButton("Rest of today", callback_data=f"busy:eod:{cid}")],
    ])


async def checkin_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    if is_stopped():
        return
    if context.job.data != "manual" and not in_hours(now()):
        # Fired late (e.g. Mac woke up at 7am after an overnight sleep): don't
        # message outside study hours, just book the next one.
        schedule_checkin(context.job_queue, chat_id, now())
        return
    cid = execute("INSERT INTO checkins (sent_ts) VALUES (?)", (now().timestamp(),))
    await context.bot.send_message(chat_id, random.choice(PROMPTS) + exam_line(), reply_markup=keyboard(cid))
    context.job_queue.run_once(
        nudge_job, when=timedelta(minutes=NUDGE_AFTER), chat_id=chat_id, name=f"nudge-{cid}", data=cid
    )
    schedule_checkin(context.job_queue, chat_id, now() + random_gap())


async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = context.job.data
    rows = query("SELECT status FROM checkins WHERE id = ?", (cid,))
    if rows and rows[0]["status"] is None and not is_stopped():
        await context.bot.send_message(context.job.chat_id, random.choice(NUDGES), reply_markup=keyboard(cid))


async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    owner = owner_id()
    if not owner or is_stopped():
        return
    set_setting("awaiting_plan", now().date().isoformat())
    await context.bot.send_message(
        int(owner), "Morning. What are you getting through today? Reply with your plan." + exam_line()
    )


async def evening_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    owner = owner_id()
    if not owner or is_stopped():
        return
    today = now().date().isoformat()
    plan = get_setting(f"plan:{today}")
    c = day_counts(now().date())
    text = f"End of day. {c['studying']}/{c['counted']} check-ins on task ({pct(c)})."
    if c["commitment"]:
        text += f" ({c['commitment']} commitment not counted.)"
    if plan:
        text += f"\n\nThis morning you said:\n“{plan}”\n\nDid you get through it?"
    await context.bot.send_message(int(owner), text)


# -------------------------------------------------------------- handlers ----

def owner_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and is_owner(update.effective_chat.id):
            return await fn(update, context)
    return wrapper


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    owner = owner_id()
    if owner and str(owner) != str(chat_id):
        await update.message.reply_text("This bot is private.")
        return
    set_setting("chat_id", str(chat_id))
    set_setting("stopped", "0")
    set_setting("quiet_until", None)
    schedule_checkin(context.job_queue, chat_id, now() + random_gap())
    await update.message.reply_text(
        HELP.format(start=DAY_START.strftime("%H:%M"), end=DAY_END.strftime("%H:%M"))
        + "\n\nYou're set up. First check-in is coming; you won't know when."
    )


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP.format(start=DAY_START.strftime("%H:%M"), end=DAY_END.strftime("%H:%M")))


@owner_only
async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_setting("stopped", "0")
    set_setting("quiet_until", None)
    cancel_jobs(context.job_queue, CHECKIN_JOB)
    context.job_queue.run_once(
        checkin_job, when=1, chat_id=update.effective_chat.id, name=CHECKIN_JOB, data="manual"
    )


@owner_only
async def cmd_snooze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        mins = int(context.args[0]) if context.args else 60
    except ValueError:
        await update.message.reply_text("Usage: /snooze 90")
        return
    mins = max(1, min(mins, 12 * 60))
    when = pause_until(context.job_queue, update.effective_chat.id, now() + timedelta(minutes=mins))
    await update.message.reply_text(f"Snoozed. No check-ins before {fmt_when(when)}.")


@owner_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_setting("stopped", "1")
    cancel_jobs(context.job_queue, CHECKIN_JOB)
    await update.message.reply_text("Stopped. /resume when you're back.")


@owner_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_setting("stopped", "0")
    set_setting("quiet_until", None)
    schedule_checkin(context.job_queue, update.effective_chat.id, now() + random_gap())
    await update.message.reply_text("Back on. Next check-in is coming.")


@owner_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(stats_text())


@owner_only
async def cmd_exam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        name, d = context.args[0], date.fromisoformat(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /exam MATH1012 2026-11-09")
        return
    execute("INSERT OR REPLACE INTO exams (name, exam_date) VALUES (?, ?)", (name, d.isoformat()))
    await update.message.reply_text(f"Added. {countdown(name, d)}.")


@owner_only
async def cmd_exams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    exams = upcoming_exams()
    text = "\n".join(f"{d:%a %d %b}: {countdown(n, d)}" for n, d in exams) or "No exams added. /exam NAME YYYY-MM-DD"
    await update.message.reply_text(text)


@owner_only
async def cmd_delexam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /delexam MATH1012")
        return
    execute("DELETE FROM exams WHERE name = ?", (context.args[0],))
    await update.message.reply_text("Removed.")


@owner_only
async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    status, cid = q.data.split(":")
    cid = int(cid)
    execute(
        "UPDATE checkins SET status = ?, answered_ts = ? WHERE id = ?",
        (status, now().timestamp(), cid),
    )
    cancel_jobs(context.job_queue, f"nudge-{cid}")
    await q.answer()
    if status == "commitment":
        await q.edit_message_text(f"{q.message.text}\n\n📌 How long are you busy?", reply_markup=busy_keyboard(cid))
        return
    await q.edit_message_reply_markup(reply_markup=None)
    reply = random.choice(RESPONSES[status])
    if status in ("break", "distracted"):
        schedule_checkin(context.job_queue, q.message.chat_id, now() + timedelta(minutes=FOLLOWUP))
        reply += f" I'll check again in ~{int(FOLLOWUP)} min."
    await q.message.reply_text(reply)


@owner_only
async def on_busy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, length, _cid = q.data.split(":")
    await q.answer()
    await q.edit_message_reply_markup(reply_markup=None)
    if length == "eod":
        until = datetime.combine(now().date(), DAY_END, TZ)  # pushed to tomorrow's start
    else:
        until = now() + timedelta(minutes=int(length))
    when = pause_until(context.job_queue, q.message.chat_id, until)
    await q.message.reply_text(f"Got it. No check-ins until {fmt_when(when)}. This one doesn't count against you.")


@owner_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    today = now().date().isoformat()
    if get_setting("awaiting_plan") == today:
        set_setting(f"plan:{today}", text)
        set_setting("awaiting_plan", None)
        await update.message.reply_text("Locked in. I'll ask about it tonight.")
        return
    rows = query("SELECT id, sent_ts FROM checkins ORDER BY id DESC LIMIT 1")
    if rows and now().timestamp() - rows[0]["sent_ts"] < 3600:
        execute(
            "UPDATE checkins SET note = COALESCE(note || ' | ', '') || ? WHERE id = ?",
            (text, rows[0]["id"]),
        )
        await update.message.reply_text("Logged.")
    else:
        await update.message.reply_text("No recent check-in to attach that to. /now to check in, /help for commands.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Handler error", exc_info=context.error)


# ------------------------------------------------------------------ main ----

async def watchdog_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Safety net: if no check-in is booked (and we're not stopped), book one."""
    owner = owner_id()
    if owner and not is_stopped() and not context.job_queue.get_jobs_by_name(CHECKIN_JOB):
        log.warning("No check-in scheduled; rescheduling")
        schedule_checkin(context.job_queue, int(owner), next_regular_time())


WATCHED = [Path(__file__), Path(__file__).with_name(".env")]


def _mtimes() -> list[float]:
    return [p.stat().st_mtime if p.exists() else 0.0 for p in WATCHED]


_STARTUP_MTIMES = _mtimes()
_RELOAD_REQUESTED = False


async def reload_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """If bot.py or .env changed on disk, stop cleanly; main() then re-execs itself on the new code."""
    global _RELOAD_REQUESTED
    if _mtimes() != _STARTUP_MTIMES:
        log.info("bot.py/.env changed on disk; restarting on the new version")
        _RELOAD_REQUESTED = True
        context.application.stop_running()


async def post_init(app: Application) -> None:
    init_db()
    jq = app.job_queue
    # Grace periods: if the Mac was asleep at 09:00, still send the morning prompt
    # when it wakes (up to 3h late); same for the evening review (up to 2h late).
    jq.run_daily(morning_job, time=DAY_START.replace(tzinfo=TZ), name="morning",
                 job_kwargs={"misfire_grace_time": 3 * 3600, "coalesce": True})
    jq.run_daily(evening_job, time=DAY_END.replace(tzinfo=TZ), name="evening",
                 job_kwargs={"misfire_grace_time": 2 * 3600, "coalesce": True})
    jq.run_repeating(watchdog_job, interval=300, first=60, name="watchdog")
    jq.run_repeating(reload_job, interval=30, first=30, name="reload")
    owner = owner_id()
    if owner and not is_stopped():
        schedule_checkin(jq, int(owner), next_regular_time())


def build_app(token: str) -> Application:
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("now", cmd_now))
    app.add_handler(CommandHandler("snooze", cmd_snooze))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("exam", cmd_exam))
    app.add_handler(CommandHandler("exams", cmd_exams))
    app.add_handler(CommandHandler("delexam", cmd_delexam))
    app.add_handler(CallbackQueryHandler(on_busy, pattern=r"^busy:"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^(studying|break|distracted|commitment):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs URLs containing the token
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # otherwise logs every job run
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (get one from @BotFather).")
    build_app(token).run_polling()
    if _RELOAD_REQUESTED:
        # Replace this process with a fresh one running the new code. Same PID, so
        # launchd (or a Terminal session) never sees the bot exit.
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


if __name__ == "__main__":
    main()
