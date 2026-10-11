import asyncio
import logging
import re

from fastapi.concurrency import run_in_threadpool
from telegram import Update
from telegram.constants import MessageLimit
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import (
    auth_flow_service,
    link_service,
    permission_ask_service,
    schedule_service,
    task_execution_service,
    user_credential_service,
    user_sandbox_service,
)
from app.services.auth_flow_service import (
    LoginFailedError,
    LoginTimeoutError,
    UnsupportedProviderError as AuthUnsupportedProviderError,
)
from app.services.box_operations import ascii_box_service
from app.services.task_execution_service import (
    BoxCommandError,
    UnsupportedProviderError,
)
from app.services.user_credential_service import CredentialNotFoundError

logger = logging.getLogger(__name__)

PROVIDER = "claude"
DEFAULT_TTL_SECONDS = 1800

POLL_INTERVAL_SECONDS = 3
# Give commands their full execution budget, plus a final status check.
POLL_ATTEMPTS = task_execution_service.MAX_TASK_SECONDS // POLL_INTERVAL_SECONDS + 1

# chat_id -> box_id, set while we're waiting for a pasted OAuth code.
_awaiting_code: dict[int, str] = {}


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send /connect to link your Claude account, then just message me a task "
        "(e.g. \"create a webpage that says hello\").\n\n"
        "Send /connect_github if you want tasks to be able to push to your "
        "GitHub repos as you.\n\n"
        "If you got here from the web UI's \"Connect Telegram\" step, send "
        "/link <code> with the code it showed you.\n\n"
        "To repeat a task on a schedule, send /schedule — send it with no "
        "arguments to see how."
    )


async def _get_or_create_sandbox(user_id: str) -> str:
    async with AsyncSessionLocal() as db:
        _, box = await user_sandbox_service.ensure(
            db, user_id=user_id, ttl_seconds=DEFAULT_TTL_SECONDS
        )
        return box.id

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage: /link <code> — get a code from the web UI's \"Connect Telegram\" step."
        )
        return

    code = context.args[0]
    async with AsyncSessionLocal() as db:
        try:
            await link_service.claim_code(db, code, user_id)
        except link_service.LinkCodeInvalidError:
            await update.message.reply_text(
                "That code isn't valid. Generate a new one on the web page."
            )
            return
        except link_service.LinkCodeExpiredError:
            await update.message.reply_text(
                "That code expired. Generate a new one on the web page."
            )
            return

    await update.message.reply_text("Linked! Head back to the web page — it should pick this up automatically.")


async def connect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)

    await update.message.reply_text("Setting up your sandbox...")

    try:
        box_id = await _get_or_create_sandbox(user_id)
        login_url = await run_in_threadpool(auth_flow_service.start_login, box_id, PROVIDER)
    except AuthUnsupportedProviderError as exc:
        await update.message.reply_text(str(exc))
        return
    except LoginTimeoutError as exc:
        await update.message.reply_text(f"Could not start login: {exc}")
        return

    _awaiting_code[chat_id] = box_id
    await update.message.reply_text(
        f"Open this link to log in to Claude:\n{login_url}\n\n"
        "After logging in, paste the code it gives you back here."
    )


async def connect_github_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)

    await update.message.reply_text("Setting up your sandbox...")

    try:
        box_id = await _get_or_create_sandbox(user_id)
        login = await run_in_threadpool(
            auth_flow_service.start_device_login, box_id, "github"
        )
    except LoginTimeoutError as exc:
        await update.message.reply_text(f"Could not start GitHub login: {exc}")
        return

    await update.message.reply_text(
        f"Open {login.url} and enter this code: {login.code}\n\n"
        "I'll message you once it's connected — no need to send anything here."
    )
    asyncio.create_task(_finish_github_login(context.bot, chat_id, user_id, box_id))


async def _finish_github_login(bot, chat_id: int, user_id: str, box_id: str) -> None:
    try:
        credential_content = await run_in_threadpool(
            auth_flow_service.await_device_login, box_id, "github"
        )
    except LoginFailedError as exc:
        await bot.send_message(chat_id=chat_id, text=f"GitHub login failed: {exc}")
        return
    except LoginTimeoutError as exc:
        await bot.send_message(chat_id=chat_id, text=f"GitHub login timed out: {exc}")
        return

    async with AsyncSessionLocal() as db:
        await user_credential_service.save_token(
            db,
            user_id=user_id,
            provider="github",
            file_path=auth_flow_service.PROVIDERS["github"]["credential_path"],
            plaintext=credential_content,
        )

    await bot.send_message(
        chat_id=chat_id,
        text="GitHub connected! Tasks that push to a repo will now use your account.",
    )


async def _handle_code_submission(update: Update, box_id: str, code: str) -> None:
    user_id = str(update.effective_user.id)
    await update.message.reply_text("Submitting code...")

    try:
        credential_content = await run_in_threadpool(
            auth_flow_service.submit_code, box_id, PROVIDER, code
        )
    except LoginFailedError as exc:
        await update.message.reply_text(f"Login failed: {exc}")
        return
    except LoginTimeoutError as exc:
        await update.message.reply_text(f"Login timed out: {exc}")
        return

    async with AsyncSessionLocal() as db:
        await user_credential_service.save_token(
            db,
            user_id=user_id,
            provider=PROVIDER,
            file_path=auth_flow_service.PROVIDERS[PROVIDER]["credential_path"],
            plaintext=credential_content,
        )

    await update.message.reply_text("Connected! You can now send me tasks.")


async def _poll_task(bot, chat_id: int, task_id: int) -> None:
    async with AsyncSessionLocal() as db:
        task = await task_execution_service.get_task(db, task_id)
        for _ in range(POLL_ATTEMPTS):
            if task is None or task.status not in task_execution_service.ACTIVE_STATUSES:
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            task = await task_execution_service.sync_task_status(db, task)

    if task is None:
        message = "Task disappeared unexpectedly."
    elif task.status == "succeeded":
        message = task.result or "Task completed."
    elif task.status in task_execution_service.ACTIVE_STATUSES:
        message = f"Task #{task.id} is still {task.status}. Check its status in the web UI."
    else:
        message = f"Task failed: {task.error}"

    # Reports and CLI errors can exceed Telegram's per-message text limit.
    # Send every part in order rather than losing the entire completion.
    for start in range(0, len(message), MessageLimit.MAX_TEXT_LENGTH):
        await bot.send_message(
            chat_id=chat_id, text=message[start:start + MessageLimit.MAX_TEXT_LENGTH]
        )


async def permission_ask_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, ask_id_str, decision, always_str = query.data.split(":")
        ask_id = int(ask_id_str)
    except (ValueError, AttributeError):
        await query.edit_message_text("Couldn't read that button's data.")
        return
    if decision not in ("allow", "deny"):
        await query.edit_message_text("Couldn't read that button's data.")
        return

    async with AsyncSessionLocal() as db:
        try:
            ask = await permission_ask_service.answer_ask(
                db, ask_id, decision=decision, always=always_str == "1"
            )
        except ValueError:
            await query.edit_message_text("This request no longer exists.")
            return

    outcome = "Allowed" if ask.decision == "allow" else "Denied"
    if ask.always:
        outcome += " (always — won't ask again for this)"
    await query.edit_message_text(f"{outcome}.")


TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# JS-style weekday numbering (Sunday = 0), matching the web UI's schedule form.
WEEKDAY_CODES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

SCHEDULE_USAGE = (
    "Usage:\n"
    "/schedule hourly <prompt>\n"
    "/schedule daily HH:MM <prompt>\n"
    "/schedule weekdays HH:MM <prompt>\n"
    "/schedule weekly <mon|tue|wed|thu|fri|sat|sun> HH:MM <prompt>\n\n"
    "Times are UTC. Example: /schedule daily 08:00 Generate my daily report"
)


def _parse_schedule_args(args: list[str]) -> tuple[str, str]:
    """Turns the words after /schedule into (cron_expression, prompt_text).
    Raises ValueError with a user-facing message on anything malformed."""
    if not args:
        raise ValueError(SCHEDULE_USAGE)

    frequency, rest = args[0].lower(), args[1:]

    if frequency == "hourly":
        if not rest:
            raise ValueError(SCHEDULE_USAGE)
        return "0 * * * *", " ".join(rest)

    if frequency in ("daily", "weekdays"):
        if len(rest) < 2 or not TIME_RE.match(rest[0]):
            raise ValueError(SCHEDULE_USAGE)
        hour, minute = map(int, rest[0].split(":"))
        cron = f"{minute} {hour} * * *" if frequency == "daily" else f"{minute} {hour} * * 1-5"
        return cron, " ".join(rest[1:])

    if frequency == "weekly":
        if len(rest) < 3 or rest[0].lower() not in WEEKDAY_CODES or not TIME_RE.match(rest[1]):
            raise ValueError(SCHEDULE_USAGE)
        weekday = WEEKDAY_CODES[rest[0].lower()]
        hour, minute = map(int, rest[1].split(":"))
        return f"{minute} {hour} * * {weekday}", " ".join(rest[2:])

    raise ValueError(SCHEDULE_USAGE)


async def schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)

    try:
        cron_expression, prompt_text = _parse_schedule_args(context.args)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    if not prompt_text.strip():
        await update.message.reply_text(SCHEDULE_USAGE)
        return

    async with AsyncSessionLocal() as db:
        sandbox = await user_sandbox_service.get_active_by_user_id(db, user_id)
        if sandbox is None:
            await update.message.reply_text("You don't have a sandbox yet. Send /connect first.")
            return

        try:
            schedule = await schedule_service.create_schedule(
                db,
                user_id=user_id,
                provider=PROVIDER,
                box_id=sandbox.box_id,
                prompt_text=prompt_text,
                cron_expression=cron_expression,
                timezone="UTC",
            )
        except schedule_service.InvalidScheduleError as exc:
            await update.message.reply_text(str(exc))
            return

    next_run = schedule.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
    await update.message.reply_text(
        f"Scheduled #{schedule.id}. Next run: {next_run}\n"
        f"Manage it with /schedules, /pause {schedule.id}, /resume {schedule.id}, "
        f"or /unschedule {schedule.id}."
    )


async def schedules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    async with AsyncSessionLocal() as db:
        schedules = await schedule_service.list_schedules(db, user_id)

    if not schedules:
        await update.message.reply_text("You have no schedules. Create one with /schedule.")
        return

    lines = []
    for s in schedules:
        if s.paused_reason:
            status = f"paused — {s.paused_reason}"
        elif not s.is_active:
            status = "paused"
        else:
            status = f"next run {s.next_run_at.strftime('%Y-%m-%d %H:%M UTC')}"
        lines.append(f"#{s.id} [{s.cron_expression}] {s.prompt_text[:60]} — {status}")

    message = "\n".join(lines)
    for start in range(0, len(message), MessageLimit.MAX_TEXT_LENGTH):
        await update.message.reply_text(message[start:start + MessageLimit.MAX_TEXT_LENGTH])


async def _schedule_id_from_args(update: Update, usage: str) -> int | None:
    args = update.message.text.split()[1:]
    if not args or not args[0].isdigit():
        await update.message.reply_text(usage)
        return None
    return int(args[0])


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedule_id = await _schedule_id_from_args(update, "Usage: /pause <id> — see /schedules for ids")
    if schedule_id is None:
        return
    async with AsyncSessionLocal() as db:
        try:
            await schedule_service.pause_schedule(db, schedule_id, str(update.effective_user.id))
        except schedule_service.ScheduleNotFoundError:
            await update.message.reply_text(f"No schedule #{schedule_id} found.")
            return
    await update.message.reply_text(f"Paused #{schedule_id}.")


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedule_id = await _schedule_id_from_args(update, "Usage: /resume <id> — see /schedules for ids")
    if schedule_id is None:
        return
    async with AsyncSessionLocal() as db:
        try:
            schedule = await schedule_service.resume_schedule(
                db, schedule_id, str(update.effective_user.id)
            )
        except schedule_service.ScheduleNotFoundError:
            await update.message.reply_text(f"No schedule #{schedule_id} found.")
            return
    next_run = schedule.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
    await update.message.reply_text(f"Resumed #{schedule_id}. Next run: {next_run}")


async def unschedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedule_id = await _schedule_id_from_args(update, "Usage: /unschedule <id> — see /schedules for ids")
    if schedule_id is None:
        return
    async with AsyncSessionLocal() as db:
        try:
            await schedule_service.delete_schedule(db, schedule_id, str(update.effective_user.id))
        except schedule_service.ScheduleNotFoundError:
            await update.message.reply_text(f"No schedule #{schedule_id} found.")
            return
    await update.message.reply_text(f"Deleted #{schedule_id}.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text

    if chat_id in _awaiting_code:
        box_id = _awaiting_code.pop(chat_id)
        await _handle_code_submission(update, box_id, text)
        return

    user_id = str(update.effective_user.id)
    async with AsyncSessionLocal() as db:
        sandbox = await user_sandbox_service.get_active_by_user_id(db, user_id)
        if sandbox is None:
            await update.message.reply_text("You don't have a sandbox yet. Send /connect first.")
            return

        try:
            task = await task_execution_service.execute_task(
                db,
                user_id=user_id,
                provider=PROVIDER,
                box_id=sandbox.box_id,
                prompt_text=text,
            )
        except CredentialNotFoundError:
            await update.message.reply_text("You're not connected yet. Send /connect first.")
            return
        except UnsupportedProviderError as exc:
            await update.message.reply_text(str(exc))
            return
        except BoxCommandError as exc:
            await update.message.reply_text(f"Couldn't reach your sandbox: {exc}")
            return

    if task.status == "failed":
        await update.message.reply_text(f"Task failed: {task.error}")
        return

    await update.message.reply_text("Working on it...")
    asyncio.create_task(_poll_task(context.bot, chat_id, task.id))


def build_application() -> Application:
    application = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("link", link_handler))
    application.add_handler(CommandHandler("connect", connect_handler))
    application.add_handler(CommandHandler("connect_github", connect_github_handler))
    application.add_handler(CommandHandler("schedule", schedule_handler))
    application.add_handler(CommandHandler("schedules", schedules_handler))
    application.add_handler(CommandHandler("pause", pause_handler))
    application.add_handler(CommandHandler("resume", resume_handler))
    application.add_handler(CommandHandler("unschedule", unschedule_handler))
    application.add_handler(
        CallbackQueryHandler(permission_ask_callback_handler, pattern=r"^ask:")
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    return application


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_application().run_polling()
