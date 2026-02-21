import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from html import escape

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext

import config
from database import Database
from states import VoteProcess
from captcha_gen import generate_captcha_image
from admin_log import log_new_vote

router = Router()

# Thread pool for CPU-bound captcha image generation (won't block event loop)
_captcha_pool = ThreadPoolExecutor(max_workers=8)


async def _async_generate_captcha():
    """Run captcha image generation in thread pool to avoid blocking."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_captcha_pool, generate_captcha_image)


def captcha_keyboard(options: list[int]) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(text=str(o), callback_data=f"ca:{o}") for o in options[:2]]
    row2 = [InlineKeyboardButton(text=str(o), callback_data=f"ca:{o}") for o in options[2:]]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


async def _send_captcha(
    chat_id: int,
    bot: Bot,
    state: FSMContext,
    solved: int,
    chosen: str,
    prefix_text: str = "",
    old_message_id: int | None = None,
):
    """Generate captcha image and send it. Deletes the old captcha message."""
    img_bytes, answer, opts = await _async_generate_captcha()
    await state.update_data(captcha_answer=answer)

    caption = (
        f"{prefix_text}"
        f"🔐 <b>کپچا ({solved + 1}/{config.CAPTCHA_COUNT})</b>\n\n"
        f"گزینه انتخابی: <b>{escape(chosen)}</b>\n\n"
        f"🔢 جواب عکس بالا را انتخاب کنید:"
    )

    # Delete old message first
    if old_message_id:
        try:
            await bot.delete_message(chat_id, old_message_id)
        except Exception:
            pass

    photo = BufferedInputFile(img_bytes, filename="captcha.png")
    msg = await bot.send_photo(
        chat_id,
        photo=photo,
        caption=caption,
        reply_markup=captcha_keyboard(opts),
        parse_mode="HTML",
    )
    await state.update_data(captcha_msg_id=msg.message_id)


# ──────────────── Show poll for voting ────────────────

async def show_poll_for_vote(message: Message, poll_id: str, bot: Bot):
    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)

    if not poll:
        await message.answer("❌ این نظرسنجی وجود ندارد یا حذف شده است.")
        return

    options = json.loads(poll["options"])
    vote_counts = await db.get_vote_counts(poll_id)
    total = await db.get_total_votes(poll_id)

    already_voted = await db.has_voted(poll_id, message.chat.id)

    text = f"📊 <b>{escape(poll['question'])}</b>\n\n"
    for i, opt in enumerate(options):
        count = vote_counts.get(i, 0)
        pct = (count / total * 100) if total > 0 else 0
        bar_len = int(pct / 5)
        bar = "▓" * bar_len + "░" * (20 - bar_len)
        text += f"🔹 {escape(opt)}\n{bar} {count} ({pct:.1f}%)\n\n"
    text += f"👥 مجموع آرا: {total}"

    if already_voted:
        text += "\n\n✅ شما قبلا در این نظرسنجی رأی داده‌اید."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")]
        ])
    else:
        rows = []
        for i, opt in enumerate(options):
            rows.append([InlineKeyboardButton(
                text=f"🔘 {opt}",
                callback_data=f"v:{poll_id}:{i}",
            )])
        rows.append([InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ──────────────── Vote option selected → start captcha ────────────────

@router.callback_query(F.data.startswith("v:"))
async def cb_vote_option(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split(":")
    poll_id = parts[1]
    option_index = int(parts[2])

    db = await Database.get_instance()

    if await db.has_voted(poll_id, callback.from_user.id):
        await callback.answer("❌ شما قبلا رأی داده‌اید!", show_alert=True)
        return

    poll = await db.get_poll(poll_id)
    if not poll:
        await callback.answer("❌ نظرسنجی یافت نشد!", show_alert=True)
        return

    options = json.loads(poll["options"])
    chosen = options[option_index] if option_index < len(options) else "?"

    # Start captcha flow
    await state.set_state(VoteProcess.solving_captcha)
    await state.update_data(
        poll_id=poll_id,
        option_index=option_index,
        captcha_solved=0,
    )

    await _send_captcha(
        chat_id=callback.message.chat.id,
        bot=bot,
        state=state,
        solved=0,
        chosen=chosen,
        old_message_id=callback.message.message_id,
    )
    await callback.answer()


# ──────────────── Captcha answer ────────────────

@router.callback_query(F.data.startswith("ca:"), VoteProcess.solving_captcha)
async def cb_captcha_answer(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    selected = int(callback.data.split(":")[1])
    correct = data["captcha_answer"]
    solved = data["captcha_solved"]
    poll_id = data["poll_id"]
    option_index = data["option_index"]

    db = await Database.get_instance()
    poll = await db.get_poll(poll_id)
    if not poll:
        await state.clear()
        await callback.answer("❌ نظرسنجی یافت نشد!", show_alert=True)
        return

    options_list = json.loads(poll["options"])
    chosen = options_list[option_index] if option_index < len(options_list) else "?"

    if selected != correct:
        # Wrong – regenerate same step with new image
        await _send_captcha(
            chat_id=callback.message.chat.id,
            bot=bot,
            state=state,
            solved=solved,
            chosen=chosen,
            prefix_text="❌ <b>اشتباه!</b> دوباره تلاش کنید.\n\n",
            old_message_id=callback.message.message_id,
        )
        await callback.answer("❌ پاسخ اشتباه!")
        return

    # Correct
    solved += 1

    if solved >= config.CAPTCHA_COUNT:
        # All done – register vote
        success = await db.add_vote(poll_id, callback.from_user.id, option_index)

        if success:
            vote_counts = await db.get_vote_counts(poll_id)
            total = await db.get_total_votes(poll_id)
            vote_number = await db.get_vote_number(poll_id, callback.from_user.id)

            text = "✅ <b>رأی شما با موفقیت ثبت شد!</b>\n\n"
            text += f"📊 <b>{escape(poll['question'])}</b>\n\n"

            for i, opt in enumerate(options_list):
                count = vote_counts.get(i, 0)
                pct = (count / total * 100) if total > 0 else 0
                bar_len = int(pct / 5)
                bar = "▓" * bar_len + "░" * (20 - bar_len)
                marker = "✅" if i == option_index else "🔹"
                text += f"{marker} {escape(opt)}\n{bar} {count} ({pct:.1f}%)\n\n"

            text += f"👥 مجموع آرا: {total}"

            kb_rows = []

            # ── Send log to poll's log channel and get message link ──
            log_msg_link = None
            if poll.get("log_channel_id"):
                try:
                    log_channel_id = int(poll["log_channel_id"])

                    uid_str = str(callback.from_user.id)
                    if len(uid_str) > 4:
                        mid = len(uid_str) // 2
                        masked = uid_str[:mid - 2] + "****" + uid_str[mid + 2:]
                    else:
                        masked = "****"

                    log_text = (
                        f"📊 رأی جدید در نظرسنجی:\n"
                        f"❓ {escape(poll['question'])}\n\n"
                        f"#{vote_number}\n"
                        f"🆔 کاربر:\n"
                        f"<code>{masked}</code>\n"
                        f"🔘 رأی:\n"
                        f"<b>{escape(chosen)}</b>\n"
                        f"👥 مجموع آرا:\n"
                        f"{total}"
                    )
                    sent_log = await bot.send_message(
                        log_channel_id, log_text, parse_mode="HTML"
                    )

                    # Build link to this specific log message
                    try:
                        chat_info = await bot.get_chat(log_channel_id)
                        if chat_info.username:
                            log_msg_link = f"https://t.me/{chat_info.username}/{sent_log.message_id}"
                        else:
                            # Private channel: tg://c/{id}/{msg_id} style
                            raw_id = str(log_channel_id).replace("-100", "")
                            log_msg_link = f"https://t.me/c/{raw_id}/{sent_log.message_id}"
                    except Exception:
                        pass

                except Exception:
                    pass

            # Show vote number in user result
            if log_msg_link:
                text += f"\n\n🔢 شماره رأی شما: <a href=\"{log_msg_link}\">#{vote_number}</a>"
                kb_rows.append([InlineKeyboardButton(
                    text=f"📌 رأی #{vote_number} در لاگ",
                    url=log_msg_link,
                )])
            elif poll.get("log_channel_id"):
                text += f"\n\n🔢 شماره رأی شما: #{vote_number}"

            # Log channel button
            if poll.get("log_channel_id"):
                log_link = await db.get_log_channel_link(int(poll["log_channel_id"]), bot)
                if log_link:
                    kb_rows.append([InlineKeyboardButton(text="📢 کانال شفافیت آرا", url=log_link)])

            # حذف دکمه بازگشت
            # kb_rows.append([InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            # Delete captcha image message, send text result
            try:
                await bot.delete_message(callback.message.chat.id, callback.message.message_id)
            except Exception:
                pass
            await bot.send_message(
                callback.message.chat.id, text, reply_markup=kb, parse_mode="HTML"
            )

            # Admin log
            await log_new_vote(
                bot,
                callback.from_user.id,
                callback.from_user.first_name,
                poll["question"],
                chosen,
                total,
            )
        else:
            try:
                await bot.delete_message(callback.message.chat.id, callback.message.message_id)
            except Exception:
                pass
            await bot.send_message(
                callback.message.chat.id,
                "❌ شما قبلا رأی داده‌اید!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="main_menu")]
                ]),
            )

        await state.clear()
        await callback.answer()
        return

    # Next captcha
    await state.update_data(captcha_solved=solved, captcha_answer=None)

    await _send_captcha(
        chat_id=callback.message.chat.id,
        bot=bot,
        state=state,
        solved=solved,
        chosen=chosen,
        prefix_text="✅ <b>درست!</b>\n\n",
        old_message_id=callback.message.message_id,
    )
    await callback.answer("✅ درست!")


# ──────────────── Ignore random text during captcha ────────────────

@router.message(VoteProcess.solving_captcha)
async def ignore_during_captcha(message: Message):
    await message.answer("⚠️ لطفا ابتدا کپچا را با زدن دکمه‌ها حل کنید.")
