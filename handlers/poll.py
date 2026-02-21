import json
import secrets
import urllib.parse
from html import escape

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

import config
from database import Database
from states import PollCreation
from admin_log import log_new_poll, log_poll_deleted

router = Router()


def _build_share_url(question: str, options: list[str], link: str,
                     log_mention: str | None = None) -> str:
    """Build a Telegram share URL with quoted text, options, and link at bottom."""
    lines = [
        " \u2727 \u0646\u0638\u0631\u0633\u0646\u062c\u06cc \u0634\u0641\u0627\u0641 \u2727" if log_mention else " \u2727 \u0646\u0638\u0631\u0633\u0646\u062c\u06cc \u062c\u062f\u06cc\u062f \u2727",
        " \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        "",
        f" \u2753 {question}",
        "",
    ]
    for opt in options:
        lines.append(f" \u25fb\ufe0f {opt}")
    lines.append("")
    if log_mention:
        lines.append(" \u2705 \u0631\u0623\u06cc\u200c\u06af\u06cc\u0631\u06cc \u0634\u0641\u0627\u0641 \u0648 \u0642\u0627\u0628\u0644 \u0645\u0634\u0627\u0647\u062f\u0647")
        lines.append(f" \U0001f4e2 \u0645\u0634\u0627\u0647\u062f\u0647 \u0622\u0631\u0627: {log_mention}")
        lines.append("")
    lines.append(" \u2728 \u0646\u0638\u0631\u062a \u0645\u0647\u0645\u0647! \u0628\u06cc\u0627 \u0631\u0623\u06cc\u062a \u0631\u0648 \u062b\u0628\u062a \u06a9\u0646")
    lines.append("")
    lines.append(f"\U0001f517 \u0634\u0631\u06a9\u062a \u062f\u0631 \u0646\u0638\u0631\u0633\u0646\u062c\u06cc:")
    lines.append(link)

    share_text = "\n".join(lines)
    return f"https://t.me/share/url?url={urllib.parse.quote(share_text, safe='')}&url="


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


def _options_kb(options: list[str]) -> InlineKeyboardMarkup:
    """Keyboard shown while collecting options."""
    rows = []
    if len(options) >= 2:
        rows.append([InlineKeyboardButton(text=f"✅ پایان ({len(options)} گزینه)", callback_data="done_opts")])
    rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="cancel_poll")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _options_text(question: str, options: list[str]) -> str:
    text = f"❓ سوال: <b>{escape(question)}</b>\n\n"
    for i, o in enumerate(options, 1):
        text += f"  {i}. {escape(o)}\n"
    text += "\n📝 گزینه بعدی را ارسال کنید:"
    if len(options) < 2:
        text += "\n⚠️ حداقل ۲ گزینه لازم است."
    return text


# ──────────────── Start poll creation ────────────────

@router.callback_query(F.data == "new_poll")
async def cb_new_poll(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PollCreation.waiting_question)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ لغو", callback_data="cancel_poll")]
    ])
    await callback.message.edit_text(
        "📊 <b>ساخت نظرسنجی جدید</b>\n\nلطفا سوال نظرسنجی را وارد کنید:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_poll")
async def cb_cancel_poll(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ ساخت نظرسنجی لغو شد.")
    await callback.answer()


# ──────────────── Receive question ────────────────

@router.message(PollCreation.waiting_question, F.text)
async def receive_question(message: Message, state: FSMContext):
    question = message.text.strip()
    if not question:
        await message.answer("⚠️ سوال نمی‌تواند خالی باشد. لطفا دوباره وارد کنید:")
        return

    await state.update_data(question=question, options=[])
    await state.set_state(PollCreation.waiting_options)

    kb = _options_kb([])
    await message.answer(
        _options_text(question, []),
        reply_markup=kb,
        parse_mode="HTML",
    )


# ──────────────── Receive options one by one ────────────────

@router.message(PollCreation.waiting_options, F.text)
async def receive_option(message: Message, state: FSMContext):
    data = await state.get_data()
    options: list[str] = data.get("options", [])
    new_opt = message.text.strip()

    if not new_opt:
        await message.answer("⚠️ گزینه خالی قبول نیست.")
        return

    options.append(new_opt)
    await state.update_data(options=options)

    kb = _options_kb(options)
    await message.answer(
        _options_text(data["question"], options),
        reply_markup=kb,
        parse_mode="HTML",
    )


# ──────────────── Done collecting options ────────────────

@router.callback_query(F.data == "done_opts", PollCreation.waiting_options)
async def cb_done_options(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    options = data.get("options", [])
    if len(options) < 2:
        await callback.answer("⚠️ حداقل ۲ گزینه لازم است!", show_alert=True)
        return

    await state.set_state(PollCreation.waiting_log_choice)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله", callback_data="log_yes"),
            InlineKeyboardButton(text="❌ خیر", callback_data="log_no"),
        ],
        [InlineKeyboardButton(text="❌ لغو ساخت", callback_data="cancel_poll")],
    ])
    await callback.message.edit_text(
        "📢 آیا می‌خواهید لاگ رأی‌ها در یک کانال ارسال شود?\n\n"
        "⚠️ ربات باید ادمین آن کانال باشد.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────── Log channel choice ────────────────

@router.callback_query(F.data == "log_no", PollCreation.waiting_log_choice)
async def cb_log_no(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await _create_and_send_poll(callback, state, bot, log_channel=None)


@router.callback_query(F.data == "log_yes", PollCreation.waiting_log_choice)
async def cb_log_yes(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PollCreation.waiting_log_channel)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ لغو", callback_data="cancel_poll")]
    ])
    await callback.message.edit_text(
        "📢 لطفا آی‌دی کانال را ارسال کنید.\n\n"
        "مثال:\n"
        "• <code>@MyChannel</code>\n"
        "• <code>https://t.me/MyChannel</code>\n"
        "• <code>-1001234567890</code>\n\n"
        "⚠️ ربات باید ادمین کانال باشد.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PollCreation.waiting_log_channel, F.text)
async def receive_log_channel(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip()
    chat = await _resolve_channel(raw, bot)

    if not chat:
        await message.answer(
            "❌ کانال یافت نشد یا ربات ادمین نیست.\n\n"
            "مثال:\n"
            "• <code>@MyChannel</code>\n"
            "• <code>https://t.me/MyChannel</code>\n"
            "• <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return

    # Verify bot is admin
    try:
        me = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
        if me.status not in ("administrator", "creator"):
            raise Exception("not admin")
    except Exception:
        await message.answer("❌ ربات ادمین این کانال نیست. لطفا دوباره وارد کنید:")
        return

    channel_id = chat.id
    await state.update_data(log_channel_id=channel_id)
    await _create_and_send_poll(message, state, bot, log_channel=channel_id)


# ──────────────── Actually create poll ────────────────

async def _create_and_send_poll(event, state: FSMContext, bot: Bot, log_channel: int | None):
    data = await state.get_data()
    question = data.get("question")
    options = data.get("options")

    if not question or not options:
        await state.clear()
        await event.answer(
            "❌ اطلاعات نظرسنجی از دست رفته است. لطفاً دوباره بسازید.",
        )
        return

    poll_id = secrets.token_urlsafe(6)  # ~8 chars
    creator_id = event.from_user.id

    db = await Database.get_instance()
    await db.create_poll(poll_id, creator_id, question, options, log_channel)
    await state.clear()

    bot_username = config.BOT_USERNAME or (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=poll_{poll_id}"

    share_url_simple = _build_share_url(question, options, link)

    text = (
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u2705 <b>\u0646\u0638\u0631\u0633\u0646\u062c\u06cc \u0634\u0645\u0627 \u0633\u0627\u062e\u062a\u0647 \u0634\u062f!</b>\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"\u2753 \u0633\u0648\u0627\u0644: <b>{escape(question)}</b>\n"
        f"\U0001f4dd \u062a\u0639\u062f\u0627\u062f \u06af\u0632\u06cc\u0646\u0647\u200c\u0647\u0627: {len(options)}\n"
    )
    if log_channel:
        text += f"\U0001f4e2 \u06a9\u0627\u0646\u0627\u0644 \u0634\u0641\u0627\u0641\u06cc\u062a \u0622\u0631\u0627: <code>{log_channel}</code>\n"
    text += f"\n\U0001f517 \u0644\u06cc\u0646\u06a9 \u0646\u0638\u0631\u0633\u0646\u062c\u06cc:\n<code>{link}</code>"

    kb_rows = []

    # Share without log channel
    kb_rows.append([InlineKeyboardButton(text="\U0001f4e4 \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u06af\u0630\u0627\u0631\u06cc \u0644\u06cc\u0646\u06a9", url=share_url_simple)])

    # If log channel exists, share with log channel link
    if log_channel:
        log_link = await db.get_log_channel_link(log_channel, bot)
        log_mention = await db.get_log_channel_mention(log_channel, bot)
        if log_link and log_mention:
            share_url_with_log = _build_share_url(question, options, link, log_mention)
            kb_rows.append([InlineKeyboardButton(text="\U0001f4e4 \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u06af\u0630\u0627\u0631\u06cc + \u0634\u0641\u0627\u0641\u06cc\u062a \u0622\u0631\u0627", url=share_url_with_log)])

    kb_rows.append([InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

    # Admin log
    await log_new_poll(bot, creator_id, event.from_user.first_name, question, poll_id, len(options))


# ──────────────── My Polls ────────────────

@router.callback_query(F.data.startswith("my_polls:"))
async def cb_my_polls(callback: CallbackQuery, bot: Bot):
    page = int(callback.data.split(":")[1])
    db = await Database.get_instance()
    polls = await db.get_polls_by_creator(callback.from_user.id)

    if not polls:
        await callback.message.edit_text(
            "📋 شما هنوز نظرسنجی‌ای نساخته‌اید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")]
            ]),
        )
        await callback.answer()
        return

    per_page = config.PER_PAGE
    total_pages = (len(polls) + per_page - 1) // per_page
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_polls = polls[start : start + per_page]

    text = f"📋 <b>نظرسنجی‌های شما</b> (صفحه {page}/{total_pages})\n\n"
    rows = []
    for p in page_polls:
        options = json.loads(p["options"])
        total_v = await db.get_total_votes(p["poll_id"])
        text += (
            f"• <b>{escape(p['question'])}</b>\n"
            f"  گزینه‌ها: {len(options)} | آرا: {total_v}\n\n"
        )
        rows.append(
            [InlineKeyboardButton(
                text=f"📊 {p['question'][:30]}",
                callback_data=f"mpd:{p['poll_id']}",
            )]
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"my_polls:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"my_polls:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ──────────────── My Poll Detail ────────────────

@router.callback_query(F.data.startswith("mpd:"))
async def cb_my_poll_detail(callback: CallbackQuery, bot: Bot):
    poll_id = callback.data[4:]
    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)

    if not poll:
        await callback.answer("❌ نظرسنجی یافت نشد!", show_alert=True)
        return

    options = json.loads(poll["options"])
    vote_counts = await db.get_vote_counts(poll_id)
    total = await db.get_total_votes(poll_id)

    bot_username = config.BOT_USERNAME or (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=poll_{poll_id}"

    text = f"📊 <b>{escape(poll['question'])}</b>\n\n"
    for i, opt in enumerate(options):
        count = vote_counts.get(i, 0)
        pct = (count / total * 100) if total > 0 else 0
        bar_len = int(pct / 5)
        bar = "▓" * bar_len + "░" * (20 - bar_len)
        text += f"🔹 {escape(opt)}\n{bar} {count} ({pct:.1f}%)\n\n"

    text += (
        f"👥 مجموع آرا: {total}\n"
        f"🔗 <code>{link}</code>"
    )

    share_url_simple = _build_share_url(poll['question'], options, link)

    kb_rows = [
        [InlineKeyboardButton(text="\U0001f4e4 \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u06af\u0630\u0627\u0631\u06cc \u0644\u06cc\u0646\u06a9", url=share_url_simple)],
    ]

    # Share with log channel link
    if poll.get("log_channel_id"):
        log_link = await db.get_log_channel_link(int(poll["log_channel_id"]), bot)
        log_mention = await db.get_log_channel_mention(int(poll["log_channel_id"]), bot)
        if log_link and log_mention:
            share_url_with_log = _build_share_url(poll['question'], options, link, log_mention)
            kb_rows.append([InlineKeyboardButton(text="\U0001f4e4 \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u06af\u0630\u0627\u0631\u06cc + \u0634\u0641\u0627\u0641\u06cc\u062a \u0622\u0631\u0627", url=share_url_with_log)])
            kb_rows.append([InlineKeyboardButton(text="\U0001f4e2 \u06a9\u0627\u0646\u0627\u0644 \u0634\u0641\u0627\u0641\u06cc\u062a \u0622\u0631\u0627", url=log_link)])

    kb_rows.append([InlineKeyboardButton(text="🔄 بروزرسانی", callback_data=f"mpd:{poll_id}")])
    kb_rows.append([InlineKeyboardButton(text="🗑 حذف نظرسنجی", callback_data=f"del:{poll_id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 نظرسنجی‌های من", callback_data="my_polls:1")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# ──────────────── Delete poll ────────────────

@router.callback_query(F.data.startswith("del:"))
async def cb_delete_poll(callback: CallbackQuery):
    poll_id = callback.data[4:]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"cdel:{poll_id}"),
            InlineKeyboardButton(text="❌ خیر", callback_data=f"mpd:{poll_id}"),
        ]
    ])
    await callback.message.edit_text(
        "⚠️ آیا مطمئن هستید که می‌خواهید این نظرسنجی را حذف کنید?",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cdel:"))
async def cb_confirm_delete(callback: CallbackQuery, bot: Bot):
    poll_id = callback.data[5:]
    db = await Database.get_instance()

    # Get poll info for logging before deletion
    poll = await db.get_poll(poll_id)
    ok = await db.delete_poll(poll_id, callback.from_user.id)

    if ok:
        await callback.message.edit_text("✅ نظرسنجی با موفقیت حذف شد.")
        if poll:
            await log_poll_deleted(bot, callback.from_user.id, callback.from_user.first_name, poll["question"], poll_id)
    else:
        await callback.message.edit_text("❌ خطا در حذف نظرسنجی.")
    await callback.answer()


# ──────────────── My Votes (vote history) ────────────────

@router.callback_query(F.data.startswith("my_votes:"))
async def cb_my_votes(callback: CallbackQuery, bot: Bot):
    page = int(callback.data.split(":")[1])
    db = await Database.get_instance()

    total = await db.get_user_votes_count(callback.from_user.id)
    if total == 0:
        await callback.message.edit_text(
            "🗳 شما هنوز در هیچ نظرسنجی‌ای رأی نداده‌اید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")]
            ]),
        )
        await callback.answer()
        return

    per_page = config.PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    votes = await db.get_user_votes(callback.from_user.id, page, per_page)

    text = f"🗳 <b>رأی‌های شما</b> (صفحه {page}/{total_pages}) — مجموع: {total}\n\n"

    rows = []
    for v in votes:
        options_list = json.loads(v["options"])
        chosen = options_list[v["option_index"]] if v["option_index"] < len(options_list) else "?"
        text += (
            f"• <b>{escape(v['question'])}</b>\n"
            f"  🔘 رأی شما: {escape(chosen)}\n\n"
        )
        rows.append(
            [InlineKeyboardButton(
                text=f"📊 {v['question'][:28]}",
                callback_data=f"vd:{v['poll_id']}",
            )]
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"my_votes:{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"my_votes:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 بروزرسانی", callback_data=f"my_votes:{page}")])
    rows.append([InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# ──────────────── Vote Detail (from history) ────────────────

@router.callback_query(F.data.startswith("vd:"))
async def cb_vote_detail(callback: CallbackQuery, bot: Bot):
    poll_id = callback.data[3:]
    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)

    if not poll:
        await callback.answer("❌ این نظرسنجی حذف شده است.", show_alert=True)
        return

    options_list = json.loads(poll["options"])
    vote_counts = await db.get_vote_counts(poll_id)
    total = await db.get_total_votes(poll_id)

    text = f"📊 <b>{escape(poll['question'])}</b>\n\n"
    for i, opt in enumerate(options_list):
        count = vote_counts.get(i, 0)
        pct = (count / total * 100) if total > 0 else 0
        bar_len = int(pct / 5)
        bar = "▓" * bar_len + "░" * (20 - bar_len)
        text += f"🔹 {escape(opt)}\n{bar} {count} ({pct:.1f}%)\n\n"

    text += f"👥 مجموع آرا: {total}"

    kb_rows = []

    # Log channel button if available
    if poll.get("log_channel_id"):
        log_link = await db.get_log_channel_link(int(poll["log_channel_id"]), bot)
        if log_link:
            kb_rows.append([InlineKeyboardButton(text="📢 کانال شفافیت آرا", url=log_link)])

    kb_rows.append([InlineKeyboardButton(text="🔄 بروزرسانی نتایج", callback_data=f"vd:{poll_id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 رأی‌های من", callback_data="my_votes:1")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()
