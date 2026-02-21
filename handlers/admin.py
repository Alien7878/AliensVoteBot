import json
from html import escape

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import config
from database import Database
from states import ForceJoinStates, AdminLogStates
from admin_log import log_poll_deleted

router = Router()


def _admin_only(user_id: int) -> bool:
    return user_id == config.ADMIN_ID


async def _resolve_channel(raw: str, bot: Bot):
    """Resolve channel from @username, https://t.me/..., or numeric ID (auto -100)."""
    username = None
    attempts = []

    if "t.me/" in raw:
        username = raw.split("t.me/")[-1].split("/")[0].split("?")[0]
    elif raw.startswith("@"):
        username = raw[1:]
    elif raw.lstrip("-").isdigit():
        num_str = raw.lstrip("-").lstrip("0") or "0"
        num = int(raw)
        if num > 0:
            attempts.append(int(f"-100{num_str}"))
            attempts.append(-num)
        else:
            attempts.append(num)
            cleaned = raw.lstrip("-")
            if not cleaned.startswith("100"):
                attempts.append(int(f"-100{cleaned}"))
    else:
        username = raw

    if username:
        attempts.insert(0, f"@{username}")

    for attempt in attempts:
        try:
            return await bot.get_chat(attempt)
        except Exception:
            continue
    return None


# ──────────────── /admin or callback ────────────────

async def _show_admin_panel(target, db: Database):
    """target can be Message or CallbackQuery."""
    users_count = await db.get_users_count()
    polls_count = await db.get_polls_count()

    text = (
        "⚙️ <b>پنل مدیریت</b>\n\n"
        f"👥 تعداد کاربران: <b>{users_count}</b>\n"
        f"📊 تعداد نظرسنجی‌ها: <b>{polls_count}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 لیست کاربران", callback_data="adm:users:1")],
        [InlineKeyboardButton(text="📊 لیست نظرسنجی‌ها", callback_data="adm:polls:1")],
        [InlineKeyboardButton(text="🔒 تنظیمات جوین اجباری", callback_data="adm:fj")],
        [InlineKeyboardButton(text="📋 کانال لاگ ادمین", callback_data="adm:alog")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="adm:bc")],
        [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")],
    ])

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    await state.clear()
    db = await Database.get_instance()
    await _show_admin_panel(message, db)


@router.callback_query(F.data == "adm:main")
async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین!", show_alert=True)
        return
    await state.clear()
    db = await Database.get_instance()
    await _show_admin_panel(callback, db)


# ──────────────── Users list ────────────────

@router.callback_query(F.data.startswith("adm:users:"))
async def cb_admin_users(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return

    page = int(callback.data.split(":")[2])
    db = await Database.get_instance()

    total = await db.get_users_count()
    per_page = config.PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    users = await db.get_all_users(page, per_page)

    text = f"👥 <b>لیست کاربران</b> (صفحه {page}/{total_pages}) — مجموع: {total}\n\n"

    bot = callback.bot
    for u in users:
        user_id = u["user_id"]
        try:
            tg_user = await bot.get_chat(user_id)
            name = escape(tg_user.first_name or "")
            if getattr(tg_user, "last_name", None):
                name += f" {escape(tg_user.last_name)}"
            name = name or "بدون نام"
            uname = f"@{tg_user.username}" if getattr(tg_user, "username", None) else "—"
        except Exception:
            name = "ناشناس"
            uname = "—"
        text += (
            f"• <a href='tg://user?id={user_id}'>{name}</a>\n"
            f"  🆔 <code>{user_id}</code> | {uname}\n\n"
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"adm:users:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"adm:users:{page + 1}"))

    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 پنل مدیریت", callback_data="adm:main")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ──────────────── Polls list (admin) ────────────────

@router.callback_query(F.data.startswith("adm:polls:"))
async def cb_admin_polls(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return

    page = int(callback.data.split(":")[2])
    db = await Database.get_instance()

    total = await db.get_polls_count()
    per_page = config.PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    polls = await db.get_all_polls(page, per_page)

    text = f"📊 <b>لیست نظرسنجی‌ها</b> (صفحه {page}/{total_pages}) — مجموع: {total}\n\n"

    rows_btns = []
    for p in polls:
        options = json.loads(p["options"])
        total_v = await db.get_total_votes(p["poll_id"])
        creator = await db.get_user(p["creator_id"])
        creator_name = "ناشناس"
        if creator:
            creator_name = escape(creator["first_name"] or "")
            if creator["last_name"]:
                creator_name += f" {escape(creator['last_name'])}"

        text += (
            f"• <b>{escape(p['question'])}</b>\n"
            f"  سازنده: <a href='tg://user?id={p['creator_id']}'>{creator_name}</a>"
            f" (<code>{p['creator_id']}</code>)\n"
            f"  گزینه‌ها: {len(options)} | آرا: {total_v}\n\n"
        )
        rows_btns.append(
            [InlineKeyboardButton(
                text=f"📊 {p['question'][:28]}",
                callback_data=f"adm:pd:{p['poll_id']}",
            )]
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"adm:polls:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"adm:polls:{page + 1}"))
    if nav:
        rows_btns.append(nav)
    rows_btns.append([InlineKeyboardButton(text="🔙 پنل مدیریت", callback_data="adm:main")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows_btns)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ──────────────── Poll detail (admin) ────────────────

@router.callback_query(F.data.startswith("adm:pd:"))
async def cb_admin_poll_detail(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return

    poll_id = callback.data[7:]
    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)

    if not poll:
        await callback.answer("❌ نظرسنجی یافت نشد!", show_alert=True)
        return

    options = json.loads(poll["options"])
    vote_counts = await db.get_vote_counts(poll_id)
    total = await db.get_total_votes(poll_id)
    creator = await db.get_user(poll["creator_id"])

    bot_username = config.BOT_USERNAME or (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=poll_{poll_id}"

    creator_name = "ناشناس"
    creator_uname = "—"
    if creator:
        creator_name = escape(creator["first_name"] or "")
        if creator["last_name"]:
            creator_name += f" {escape(creator['last_name'])}"
        creator_uname = f"@{creator['username']}" if creator["username"] else "—"

    text = (
        f"📊 <b>{escape(poll['question'])}</b>\n\n"
    )
    for i, opt in enumerate(options):
        count = vote_counts.get(i, 0)
        pct = (count / total * 100) if total > 0 else 0
        bar_len = int(pct / 5)
        bar = "▓" * bar_len + "░" * (20 - bar_len)
        text += f"🔹 {escape(opt)}\n{bar} {count} ({pct:.1f}%)\n\n"

    text += (
        f"👥 مجموع آرا: {total}\n"
        f"👤 سازنده: <a href='tg://user?id={poll['creator_id']}'>{creator_name}</a>\n"
        f"  🆔 <code>{poll['creator_id']}</code> | {creator_uname}\n"
    )
    if poll.get("log_channel_id"):
        text += f"📢 کانال لاگ: <code>{poll['log_channel_id']}</code>\n"
    text += (
        f"📅 تاریخ ساخت: {poll['created_at']}\n"
        f"🔗 <code>{link}</code>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 حذف نظرسنجی", callback_data=f"adm:del:{poll_id}")],
        [InlineKeyboardButton(text="🔙 لیست نظرسنجی‌ها", callback_data="adm:polls:1")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ──────────────── Admin delete poll ────────────────

@router.callback_query(F.data.startswith("adm:del:"))
async def cb_admin_delete_poll(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return
    poll_id = callback.data[8:]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"adm:cdel:{poll_id}"),
            InlineKeyboardButton(text="❌ خیر", callback_data=f"adm:pd:{poll_id}"),
        ]
    ])
    await callback.message.edit_text(
        "⚠️ آیا مطمئن هستید که می‌خواهید این نظرسنجی را حذف کنید?",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cdel:"))
async def cb_admin_confirm_delete(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return
    poll_id = callback.data[9:]
    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)
    await db.delete_poll(poll_id)
    await callback.message.edit_text("✅ نظرسنجی حذف شد.")
    if poll:
        await log_poll_deleted(bot, callback.from_user.id, callback.from_user.first_name, poll["question"], poll_id)
    await callback.answer()


# ──────────────── Force Join Settings ────────────────

@router.callback_query(F.data == "adm:fj")
async def cb_force_join_settings(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return

    db = await Database.get_instance()
    mode = await db.get_setting("force_join_mode", "off")
    channel_id = await db.get_setting("force_join_channel")

    ch_name = await db.get_setting("force_join_name", "—")
    ch_link = await db.get_setting("force_join_link", "—")
    if ch_name == "—" and channel_id:
        try:
            chat = await bot.get_chat(int(channel_id))
            ch_name = chat.title or channel_id
        except Exception:
            ch_name = str(channel_id)

    mode_text = {"on": "✅ اجباری", "off": "❌ خاموش", "optional": "🔔 غیر اجباری"}.get(mode, mode)

    text = (
        "� <b>تنظیمات جوین اجباری</b>\n\n"
        f"وضعیت فعلی: <b>{mode_text}</b>\n"
        f"کانال: <b>{escape(str(ch_name))}</b>\n\n"
        "یک حالت را انتخاب کنید:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("🟢 " if mode == "on" else "") + "اجباری",
                callback_data="adm:fjm:on",
            ),
            InlineKeyboardButton(
                text=("🟢 " if mode == "optional" else "") + "غیر اجباری",
                callback_data="adm:fjm:optional",
            ),
            InlineKeyboardButton(
                text=("🟢 " if mode == "off" else "") + "خاموش",
                callback_data="adm:fjm:off",
            ),
        ],
        [InlineKeyboardButton(text="📝 تنظیم کانال", callback_data="adm:fjc")],
        [InlineKeyboardButton(text="🔙 پنل مدیریت", callback_data="adm:main")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm:fjm:"))
async def cb_set_force_join_mode(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return
    mode = callback.data.split(":")[2]
    db = await Database.get_instance()
    await db.set_setting("force_join_mode", mode)
    await callback.answer(f"✅ حالت تغییر کرد: {mode}", show_alert=True)
    # Refresh the settings page
    await cb_force_join_settings(callback, bot)


@router.callback_query(F.data == "adm:fjc")
async def cb_set_force_join_channel(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return

    await state.set_state(ForceJoinStates.waiting_channel)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ لغو", callback_data="adm:fj")]
    ])
    await callback.message.edit_text(
        "📝 لینک یا یوزرنیم کانال را ارسال کنید.\n\n"
        "مثال:\n"
        "<code>@mychannel</code>\n"
        "<code>https://t.me/mychannel</code>\n"
        "<code>-1001234567890</code>\n\n"
        "💡 برای حالت غیراجباری فقط لینک کافیست.\n"
        "⚠️ برای حالت اجباری ربات باید ادمین کانال باشد.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ForceJoinStates.waiting_channel, F.text)
async def receive_force_join_channel(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return

    raw = message.text.strip()
    db = await Database.get_instance()
    mode = await db.get_setting("force_join_mode", "off")

    # Extract username from t.me links
    username = None
    link = None
    if "t.me/" in raw:
        username = raw.split("t.me/")[-1].split("/")[0].split("?")[0]
        link = f"https://t.me/{username}"
    elif raw.startswith("@"):
        username = raw[1:]
        link = f"https://t.me/{username}"
    elif not raw.lstrip("-").isdigit():
        # Might be bare username
        username = raw
        link = f"https://t.me/{raw}"

    # For optional mode: just save the link and name, no API call needed
    if mode == "optional":
        if username:
            ch_name = f"@{username}"
            ch_link = link
        elif raw.lstrip("-").isdigit():
            ch_name = raw
            ch_link = None
        else:
            ch_name = raw
            ch_link = None

        # Try to get real name from API (optional, might fail)
        try:
            if username:
                chat = await bot.get_chat(f"@{username}")
            else:
                chat = await bot.get_chat(int(raw))
            ch_name = chat.title or ch_name
            ch_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else ch_link)
            await db.set_setting("force_join_channel", str(chat.id))
        except Exception:
            pass  # It's fine - optional mode doesn't need bot in channel

        if not ch_link and not username:
            await message.answer(
                "⚠️ برای حالت غیراجباری حداقل لینک یا یوزرنیم کانال لازم است.\n"
                "مثال: <code>@mychannel</code> یا <code>https://t.me/mychannel</code>",
                parse_mode="HTML",
            )
            return

        await db.set_settings_batch({
            "force_join_name": ch_name,
            "force_join_link": ch_link or link,
        })
        await state.clear()

        await message.answer(
            f"✅ کانال اسپانسر تنظیم شد:\n"
            f"📢 نام: <b>{escape(ch_name)}</b>\n"
            f"🔗 لینک: {ch_link or link}",
            parse_mode="HTML",
        )
        return

    # For mandatory mode: bot MUST be in the channel
    chat = None
    attempts = []

    if username:
        attempts.append(f"@{username}")
    elif raw.lstrip("-").replace(" ", "").isdigit():
        cleaned = raw.lstrip("-").replace(" ", "")
        num = int(cleaned)
        if not raw.startswith("-"):
            attempts.append(int(f"-100{num}"))
            attempts.append(-num)
            attempts.append(num)
        else:
            attempts.append(int(raw))
            if not raw.startswith("-100"):
                attempts.append(int(f"-100{num}"))
    else:
        attempts.append(f"@{raw}")

    for attempt in attempts:
        try:
            chat = await bot.get_chat(attempt)
            break
        except Exception:
            continue

    if not chat:
        await message.answer(
            "❌ کانال یافت نشد.\n\n"
            "⚠️ برای حالت اجباری ربات باید ادمین کانال باشد.\n\n"
            "لطفا دوباره وارد کنید:"
        )
        return

    ch_name = chat.title or str(chat.id)
    ch_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)

    batch = {
        "force_join_channel": str(chat.id),
        "force_join_name": ch_name,
    }
    if ch_link:
        batch["force_join_link"] = ch_link
    await db.set_settings_batch(batch)
    await state.clear()

    await message.answer(
        f"✅ کانال تنظیم شد: <b>{escape(ch_name)}</b>\n"
        f"🆔 آیدی: <code>{chat.id}</code>",
        parse_mode="HTML",
    )


# ──────────────── Admin Log Channel ────────────────

@router.callback_query(F.data == "adm:alog")
async def cb_admin_log_settings(callback: CallbackQuery, bot: Bot):
    if not _admin_only(callback.from_user.id):
        return

    db = await Database.get_instance()
    channel_id = await db.get_setting("admin_log_channel")

    ch_name = "تنظیم نشده"
    if channel_id:
        try:
            chat = await bot.get_chat(int(channel_id))
            ch_name = chat.title or str(channel_id)
        except Exception:
            ch_name = str(channel_id)

    text = (
        "📋 <b>تنظیمات کانال لاگ ادمین</b>\n\n"
        f"کانال فعلی: <b>{escape(ch_name)}</b>\n"
    )
    if channel_id:
        text += f"🆔 آیدی: <code>{channel_id}</code>\n"
    text += "\nتمام فعالیت‌های ربات (کاربر جدید، نظرسنجی جدید، رأی جدید) در این کانال لاگ می‌شوند."

    rows = [
        [InlineKeyboardButton(text="📝 تنظیم کانال لاگ", callback_data="adm:alog:set")],
    ]
    if channel_id:
        rows.append([InlineKeyboardButton(text="🗑 حذف کانال لاگ", callback_data="adm:alog:rm")])
    rows.append([InlineKeyboardButton(text="🔙 پنل مدیریت", callback_data="adm:main")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "adm:alog:set")
async def cb_admin_log_set(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return

    await state.set_state(AdminLogStates.waiting_channel)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ لغو", callback_data="adm:alog")]
    ])
    await callback.message.edit_text(
        "📝 آی‌دی کانال لاگ را ارسال کنید.\n\n"
        "مثال:\n"
        "• <code>@MyChannel</code>\n"
        "• <code>https://t.me/MyChannel</code>\n"
        "• <code>-1001234567890</code>\n\n"
        "⚠️ ربات باید ادمین کانال باشد.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminLogStates.waiting_channel, F.text)
async def receive_admin_log_channel(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return

    raw = message.text.strip()
    chat = await _resolve_channel(raw, bot)

    if not chat:
        await message.answer(
            "❌ کانال یافت نشد.\n\n"
            "مثال:\n"
            "• <code>@MyChannel</code>\n"
            "• <code>https://t.me/MyChannel</code>\n"
            "• <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return

    try:
        me = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
        if me.status not in ("administrator", "creator"):
            raise Exception("not admin")
    except Exception:
        await message.answer("❌ ربات ادمین این کانال نیست.")
        return

    db = await Database.get_instance()
    await db.set_setting("admin_log_channel", str(chat.id))
    await state.clear()

    await message.answer(
        f"✅ کانال لاگ ادمین تنظیم شد: <b>{escape(chat.title or str(chat.id))}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:alog:rm")
async def cb_admin_log_remove(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return

    db = await Database.get_instance()
    await db.set_setting("admin_log_channel", "")
    await callback.answer("✅ کانال لاگ حذف شد.", show_alert=True)

    # Refresh
    from handlers.admin import cb_admin_log_settings
    await cb_admin_log_settings(callback, callback.bot)
