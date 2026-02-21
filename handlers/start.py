import json
from html import escape

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext

import config
from database import Database
from states import PollCreation
from admin_log import log_new_user

router = Router()


# ──────────────── Helpers ────────────────

async def check_force_join(bot: Bot, user_id: int) -> tuple[bool, dict | None]:
    """
    Returns (must_block, channel_info_or_None).
    must_block=True  → force-join ON and user is NOT a member → block.
    must_block=False → either off, optional, or user already joined.
    channel_info dict has keys: name, link, id
    """
    db = await Database.get_instance()
    mode = await db.get_setting("force_join_mode", "off")
    if mode == "off":
        return False, None

    # For optional mode: just show stored name/link, no API check needed
    if mode == "optional":
        ch_name = await db.get_setting("force_join_name", "کانال اسپانسر")
        ch_link = await db.get_setting("force_join_link")
        if not ch_link:
            return False, None
        info = {"name": ch_name, "link": ch_link, "id": None}
        return False, info

    # mode == "on" → mandatory: bot must be in channel to check membership
    channel_id = await db.get_setting("force_join_channel")
    if not channel_id:
        return False, None

    # Build channel info first
    ch_name = await db.get_setting("force_join_name", "کانال")
    ch_link = await db.get_setting("force_join_link")
    try:
        chat = await bot.get_chat(int(channel_id))
        ch_name = chat.title or ch_name
        ch_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else ch_link)
    except Exception:
        pass

    info = {"name": ch_name, "link": ch_link, "id": channel_id}

    try:
        member = await bot.get_chat_member(int(channel_id), user_id)
        is_member = member.status in ("member", "administrator", "creator")
    except Exception:
        # If we can't check, block to be safe (bot might not be admin)
        return True, info

    if is_member:
        return False, None

    return True, info      # block


async def show_main_menu(message: Message, ch_info: dict | None = None):
    user = message.from_user
    name = escape(user.first_name or "کاربر")

    text = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 <b>{name}</b> عزیز، خوش اومدی!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🗳 <b>ربات نظرسنجی ساز</b>\n\n"
        f"📌 با این ربات می‌تونی نظرسنجی بسازی\n"
        f"و لینکشو با بقیه به اشتراک بذاری!\n\n"
        f"▼ یکی از گزینه‌ها رو انتخاب کن:"
    )

    buttons = [
        [InlineKeyboardButton(text="📊 ساخت نظرسنجی جدید", callback_data="new_poll")],
        [InlineKeyboardButton(text="📋 نظرسنجی‌های من", callback_data="my_polls:1")],
        [InlineKeyboardButton(text="🗳 رأی‌های من", callback_data="my_votes:1")],
    ]

    # Optional join → show as a button, not raw text
    if ch_info and ch_info.get("link"):
        buttons.append(
            [InlineKeyboardButton(text=f"📢 {ch_info['name']}", url=ch_info["link"])]
        )

    if message.from_user.id == config.ADMIN_ID:
        buttons.append(
            [InlineKeyboardButton(text="⚙️ پنل مدیریت", callback_data="adm:main")]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ──────────────── /start ────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()

    # Register / update user
    db = await Database.get_instance()
    u = message.from_user

    # Check if user is new
    existing = await db.get_user(u.id)
    await db.add_user(u.id, u.username, u.first_name, u.last_name)
    if not existing:
        await log_new_user(bot, u.id, u.first_name, u.last_name, u.username)

    # Force-join check
    must_block, ch_info = await check_force_join(bot, u.id)

    deep_arg = command.args or ""

    if must_block and ch_info:
        rows = []
        if ch_info["link"]:
            rows.append([InlineKeyboardButton(text=ch_info["name"], url=ch_info["link"])])
        rows.append(
            [InlineKeyboardButton(text="✅ عضو شدم", callback_data=f"cj:{deep_arg}")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(
            "⚠️ لطفا برای ادامه در کانال زیر جوین بشید:",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    # Deep link → poll vote
    if deep_arg.startswith("poll_"):
        poll_id = deep_arg[5:]
        # Show optional join message before poll
        if ch_info and ch_info.get("link"):
            join_text = (
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📢 <b>لطفاً ابتدا در کانال اسپانسر عضو شوید:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            join_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📢 {ch_info['name']}", url=ch_info["link"])],
                [InlineKeyboardButton(text="▶️ ادامه به نظرسنجی", callback_data=f"gopoll:{poll_id}")],
            ])
            await message.answer(join_text, reply_markup=join_kb, parse_mode="HTML")
            return

        from handlers.vote import show_poll_for_vote
        await show_poll_for_vote(message, poll_id, bot)
        return

    await show_main_menu(message, ch_info)


# ──────────────── Check Join callback ────────────────

@router.callback_query(F.data.startswith("cj:"))
async def callback_check_join(callback: CallbackQuery, bot: Bot, state: FSMContext):
    deep_arg = callback.data[3:]
    must_block, ch_info = await check_force_join(bot, callback.from_user.id)

    if must_block:
        await callback.answer("❌ هنوز عضو نشده‌اید!", show_alert=True)
        return

    await callback.answer("✅ تأیید شد!")
    await callback.message.delete()

    if deep_arg.startswith("poll_"):
        poll_id = deep_arg[5:]
        from handlers.vote import show_poll_for_vote
        await show_poll_for_vote(callback.message, poll_id, bot)
    else:
        await show_main_menu(callback.message, None)


# ──────────────── Continue to poll (after sponsor) ────────────────

@router.callback_query(F.data.startswith("gopoll:"))
async def callback_go_poll(callback: CallbackQuery, bot: Bot):
    poll_id = callback.data[7:]
    await callback.message.delete()
    from handlers.vote import show_poll_for_vote
    await show_poll_for_vote(callback.message, poll_id, bot)
    await callback.answer()


# ──────────────── Main menu callback ────────────────

@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await show_main_menu(callback.message, None)
    await callback.answer()


# ──────────────── /cancel ────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات لغو شد.")
    await show_main_menu(message)
