import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

import config
from database import Database
from states import BroadcastStates

router = Router()

# Track cancellation per admin user
_cancel_flags: dict[int, bool] = {}


# ──────────────── Start broadcast ────────────────

@router.callback_query(F.data == "adm:bc")
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔", show_alert=True)
        return

    await state.set_state(BroadcastStates.waiting_message)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ لغو", callback_data="adm:main")]
    ])
    await callback.message.edit_text(
        "📢 <b>پیام همگانی</b>\n\n"
        "پیام مورد نظر خود را ارسال کنید.\n"
        "هر نوع پیامی (متن، عکس، ویدیو، ...) قابل ارسال است.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ──────────────── Receive broadcast message ────────────────

@router.message(BroadcastStates.waiting_message)
async def receive_broadcast_message(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        return

    await state.update_data(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    await state.set_state(BroadcastStates.waiting_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 فوروارد", callback_data="bc:fwd"),
            InlineKeyboardButton(text="📋 کپی", callback_data="bc:copy"),
        ],
        [InlineKeyboardButton(text="❌ لغو", callback_data="bc:cancel")],
    ])
    await message.answer(
        "📢 روش ارسال را انتخاب کنید:",
        reply_markup=kb,
        parse_mode="HTML",
    )


# ──────────────── Broadcast type chosen ────────────────

@router.callback_query(F.data == "bc:cancel", BroadcastStates.waiting_type)
async def cb_broadcast_cancel_before(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ ارسال همگانی لغو شد.")
    await callback.answer()


@router.callback_query(F.data.in_({"bc:fwd", "bc:copy"}), BroadcastStates.waiting_type)
async def cb_broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != config.ADMIN_ID:
        return

    data = await state.get_data()
    from_chat_id = data["from_chat_id"]
    message_id = data["message_id"]
    method = "forward" if callback.data == "bc:fwd" else "copy"
    await state.clear()

    db = await Database.get_instance()
    user_ids = await db.get_all_user_ids()
    total = len(user_ids)

    _cancel_flags[callback.from_user.id] = False

    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔ لغو ارسال", callback_data="bc:stop")]
    ])

    progress_msg = await callback.message.edit_text(
        f"📢 در حال ارسال به {total} کاربر...\n"
        f"✅ 0 | ❌ 0 | 📊 0%",
        reply_markup=kb_cancel,
        parse_mode="HTML",
    )
    await callback.answer()

    success = 0
    failed = 0

    for i, uid in enumerate(user_ids):
        if _cancel_flags.get(callback.from_user.id):
            break

        try:
            if method == "forward":
                await bot.forward_message(uid, from_chat_id, message_id)
            else:
                await bot.copy_message(uid, from_chat_id, message_id)
            success += 1
        except Exception:
            failed += 1

        # Rate limiting: sleep every 25 messages
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1)
            # Update progress
            pct = int((i + 1) / total * 100)
            try:
                await progress_msg.edit_text(
                    f"📢 در حال ارسال به {total} کاربر...\n"
                    f"✅ {success} | ❌ {failed} | 📊 {pct}%",
                    reply_markup=kb_cancel,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    cancelled = _cancel_flags.pop(callback.from_user.id, False)
    status = "⛔ لغو شد" if cancelled else "✅ تکمیل شد"

    try:
        await progress_msg.edit_text(
            f"📢 <b>ارسال همگانی {status}</b>\n\n"
            f"✅ موفق: {success}\n"
            f"❌ ناموفق: {failed}\n"
            f"👥 مجموع: {total}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 پنل مدیریت", callback_data="adm:main")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ──────────────── Stop broadcast ────────────────

@router.callback_query(F.data == "bc:stop")
async def cb_broadcast_stop(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        return
    _cancel_flags[callback.from_user.id] = True
    await callback.answer("⛔ در حال لغو ارسال...", show_alert=True)
