"""Centralized admin logging — sends events to the admin log channel."""
from html import escape
from aiogram import Bot
from database import Database


async def admin_log(bot: Bot, text: str):
    """Send a log message to the admin log channel (if configured)."""
    try:
        db = await Database.get_instance()
        channel_id = await db.get_setting("admin_log_channel")
        if channel_id:
            await bot.send_message(int(channel_id), text, parse_mode="HTML")
    except Exception:
        pass


async def log_new_user(bot: Bot, user_id: int, first_name: str, last_name: str | None, username: str | None):
    name = escape(first_name or "")
    if last_name:
        name += f" {escape(last_name)}"
    uname = f"@{username}" if username else "—"
    text = (
        f"👤 <b>کاربر جدید</b>\n\n"
        f"نام: <a href='tg://user?id={user_id}'>{name}</a>\n"
        f"🆔 <code>{user_id}</code>\n"
        f"یوزرنیم: {uname}"
    )
    await admin_log(bot, text)


async def log_new_poll(bot: Bot, user_id: int, first_name: str, question: str, poll_id: str, options_count: int):
    name = escape(first_name or "کاربر")
    text = (
        f"📊 <b>نظرسنجی جدید</b>\n\n"
        f"سازنده: <a href='tg://user?id={user_id}'>{name}</a> (<code>{user_id}</code>)\n"
        f"❓ سوال: <b>{escape(question)}</b>\n"
        f"📝 تعداد گزینه‌ها: {options_count}\n"
        f"🔑 شناسه: <code>{poll_id}</code>"
    )
    await admin_log(bot, text)


async def log_new_vote(bot: Bot, user_id: int, first_name: str, poll_question: str, chosen_option: str, total_votes: int):
    name = escape(first_name or "کاربر")
    text = (
        f"🗳 <b>رأی جدید</b>\n\n"
        f"کاربر: <a href='tg://user?id={user_id}'>{name}</a> (<code>{user_id}</code>)\n"
        f"❓ نظرسنجی: <b>{escape(poll_question)}</b>\n"
        f"🔘 رأی: <b>{escape(chosen_option)}</b>\n"
        f"👥 مجموع آرا: {total_votes}"
    )
    await admin_log(bot, text)


async def log_poll_deleted(bot: Bot, user_id: int, first_name: str, question: str, poll_id: str):
    name = escape(first_name or "کاربر")
    text = (
        f"🗑 <b>نظرسنجی حذف شد</b>\n\n"
        f"توسط: <a href='tg://user?id={user_id}'>{name}</a> (<code>{user_id}</code>)\n"
        f"❓ سوال: <b>{escape(question)}</b>\n"
        f"🔑 شناسه: <code>{poll_id}</code>"
    )
    await admin_log(bot, text)
