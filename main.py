import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery, BufferedInputFile
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ==================== КОНФИГ ====================
BOT_TOKEN = "8852982033:AAGfbT5tGRR-YsM67H7cVngCGtt5o7_Wwh4"
# ID операторов (админов) — можно несколько
OPERATOR_IDS = {2049718168, 8944641597}
CHANNEL_LINK = "https://t.me/VIprinterr"

DIALOGS_FILE = "dialogs.json"
BANS_FILE = "bans.json"

# ==================== ХРАНИЛИЩЕ ====================
def load_json(path, default=None):
    if default is None:
        default = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

dialogs = load_json(DIALOGS_FILE, {})
bans = set(load_json(BANS_FILE, []))
user_states = {}  # operator_id -> state string

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Ключ = параметр ?start= с сайта (t.me/...?start=ключ)
PRODUCTS = {
    "svistok_v3": "Свисток «Гроза V3»",
    "spiral": "Спиральная виджет-игрушка",
    "lizard": "Ящерица 3D",
    "groot": "Грут 3D",
    # алиасы на случай старых/опечаточных ссылок
    "grut": "Грут 3D",
    "lizard2": "Ящерица 3D",
}

PRODUCT_PRICES = {
    "svistok_v3": 210,
    "spiral": 350,
    "lizard": 100,
    "lizard2": 100,
    "groot": 100,
    "grut": 100,
}

# Нормализация payload → основной ключ товара
PRODUCT_CANONICAL = {
    "svistok_v3": "svistok_v3",
    "spiral": "spiral",
    "lizard": "lizard",
    "lizard2": "lizard",
    "groot": "groot",
    "grut": "groot",
}

# ==================== КЛАВИАТУРЫ ПОЛЬЗОВАТЕЛЯ ====================
main_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢  Купить 3D-модель", callback_data="buy")],
    [InlineKeyboardButton(text="📢  Наш ТГК", url=CHANNEL_LINK)],
    [InlineKeyboardButton(text="🟣  Информация", callback_data="info")],
])

# ==================== АДМИН-КЛАВИАТУРЫ ====================
def admin_main_kb():
    active_count = len(get_active_dialogs())
    total = len(dialogs)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💬 Активные диалоги ({active_count})",
            callback_data="adm_active"
        )],
        [
            InlineKeyboardButton(text="📂 Все диалоги", callback_data="adm_all"),
            InlineKeyboardButton(text="🔒 Закрытые", callback_data="adm_closed"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
            InlineKeyboardButton(text="🔍 Поиск", callback_data="adm_search"),
        ],
        [
            InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"),
            InlineKeyboardButton(text="🚫 Баны", callback_data="adm_bans"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_menu")],
    ])


def dialog_actions_kb(user_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
        [
            InlineKeyboardButton(text="📜 История", callback_data=f"adm_history_{user_id}"),
            InlineKeyboardButton(text="👤 Карточка", callback_data=f"adm_card_{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"adm_close_{user_id}"),
            InlineKeyboardButton(text="🔓 Открыть", callback_data=f"adm_open_{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🚫 Бан", callback_data=f"adm_ban_{user_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"adm_delete_{user_id}"),
        ],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="adm_menu")],
    ])


def cancel_kb(callback="adm_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=callback)]
    ])


# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
def is_operator(user_id: int) -> bool:
    return user_id in OPERATOR_IDS


async def notify_operators(text: str, reply_markup=None, **kwargs):
    """Отправить уведомление всем операторам."""
    for op_id in OPERATOR_IDS:
        try:
            await bot.send_message(op_id, text, reply_markup=reply_markup, **kwargs)
        except Exception:
            logging.exception("Не удалось уведомить оператора %s", op_id)


async def notify_operators_media(send_func, *args, caption_text=None, reply_markup=None, **kwargs):
    """Переслать медиа всем операторам (send_func = bot.send_photo и т.п.)."""
    for op_id in OPERATOR_IDS:
        try:
            if caption_text is not None:
                await send_func(op_id, *args, caption=caption_text, reply_markup=reply_markup, **kwargs)
            else:
                await send_func(op_id, *args, reply_markup=reply_markup, **kwargs)
        except Exception:
            logging.exception("Не удалось отправить медиа оператору %s", op_id)


def is_banned(user_id) -> bool:
    return str(user_id) in bans


def get_dialog(user_id):
    user_id = str(user_id)
    if user_id not in dialogs:
        dialogs[user_id] = {
            "messages": [],
            "last_message": None,
            "created_at": datetime.now().isoformat(),
            "closed": False,
            "username": None,
            "fullname": None,
            "last_product": None,
            "unread": 0,
        }
        save_json(DIALOGS_FILE, dialogs)
    return dialogs[user_id]


def add_message(user_id, text, from_user, username=None, fullname=None, media_type=None):
    user_id = str(user_id)
    dialog = get_dialog(user_id)
    if username:
        dialog["username"] = username
    if fullname:
        dialog["fullname"] = fullname
    entry = {
        "from": from_user,
        "text": text or "",
        "timestamp": datetime.now().isoformat(),
        "media_type": media_type,
    }
    dialog["messages"].append(entry)
    dialog["last_message"] = text or f"[{media_type or 'медиа'}]"
    if from_user == "user":
        dialog["unread"] = dialog.get("unread", 0) + 1
        dialog["closed"] = False
    else:
        dialog["unread"] = 0
    save_json(DIALOGS_FILE, dialogs)


def get_active_dialogs():
    """Диалоги, где последнее сообщение от пользователя и не закрыты."""
    active = []
    for uid, data in dialogs.items():
        if data.get("closed"):
            continue
        msgs = data.get("messages") or []
        if not msgs:
            continue
        last = msgs[-1]
        if last.get("from") == "user":
            active.append({
                "user_id": uid,
                "last_message": (last.get("text") or "")[:60],
                "timestamp": last.get("timestamp", ""),
                "fullname": data.get("fullname") or "—",
                "username": data.get("username"),
                "unread": data.get("unread", 0),
            })
    return sorted(active, key=lambda x: x["timestamp"], reverse=True)


def get_all_dialogs(closed_only=False, limit=30):
    items = []
    for uid, data in dialogs.items():
        is_closed = data.get("closed", False)
        if closed_only and not is_closed:
            continue
        if not closed_only and is_closed:
            continue
        msgs = data.get("messages") or []
        last = msgs[-1] if msgs else {}
        items.append({
            "user_id": uid,
            "last_message": (last.get("text") or data.get("last_message") or "—")[:50],
            "timestamp": last.get("timestamp") or data.get("created_at", ""),
            "fullname": data.get("fullname") or "—",
            "username": data.get("username"),
            "closed": is_closed,
            "msg_count": len(msgs),
            "unread": data.get("unread", 0),
        })
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    return items[:limit]


def format_time(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return iso[:16]


def build_history_text(user_id: str, limit=15) -> str:
    dialog = get_dialog(user_id)
    msgs = dialog.get("messages") or []
    name = dialog.get("fullname") or "Пользователь"
    uname = f"@{dialog['username']}" if dialog.get("username") else "без username"
    header = (
        f"📜 <b>История с {name}</b>\n"
        f"🆔 <code>{user_id}</code> · {uname}\n"
        f"💬 Сообщений: {len(msgs)}\n"
        f"{'🔒 Закрыт' if dialog.get('closed') else '🟢 Открыт'}\n"
        f"{'─' * 20}\n\n"
    )
    if not msgs:
        return header + "📭 Пока пусто"
    lines = []
    for m in msgs[-limit:]:
        who = "👤" if m["from"] == "user" else "🛠"
        t = format_time(m.get("timestamp", ""))
        text = m.get("text") or f"[{m.get('media_type') or 'медиа'}]"
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"{who} <i>{t}</i>\n{text}")
    return header + "\n\n".join(lines)


def calc_stats() -> dict:
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    total_users = len(dialogs)
    total_msgs = 0
    user_msgs = 0
    op_msgs = 0
    today_users = set()
    week_users = set()
    today_msgs = 0
    products = {}
    closed = 0
    open_count = 0

    for uid, d in dialogs.items():
        if d.get("closed"):
            closed += 1
        else:
            open_count += 1
        msgs = d.get("messages") or []
        total_msgs += len(msgs)
        for m in msgs:
            total_msgs  # already counted
            ts = m.get("timestamp", "")
            try:
                mdt = datetime.fromisoformat(ts)
            except Exception:
                mdt = None
            if m["from"] == "user":
                user_msgs += 1
                if mdt and mdt >= today_start:
                    today_users.add(uid)
                    today_msgs += 1
                if mdt and mdt >= week_start:
                    week_users.add(uid)
            else:
                op_msgs += 1
        prod = d.get("last_product")
        if prod:
            products[prod] = products.get(prod, 0) + 1

    return {
        "total_users": total_users,
        "total_msgs": total_msgs,
        "user_msgs": user_msgs,
        "op_msgs": op_msgs,
        "today_users": len(today_users),
        "today_msgs": today_msgs,
        "week_users": len(week_users),
        "active": len(get_active_dialogs()),
        "open": open_count,
        "closed": closed,
        "products": products,
        "bans": len(bans),
    }


# ==================== ПОЛЬЗОВАТЕЛЬСКИЕ ХЕНДЛЕРЫ ====================

def resolve_product(payload: str):
    """Вернуть (канонический_ключ, название, цена) или (None, None, None)."""
    if not payload:
        return None, None, None
    key = payload.strip().lower()
    name = PRODUCTS.get(key) or PRODUCTS.get(payload.strip())
    if not name:
        return None, None, None
    canon = PRODUCT_CANONICAL.get(key, key)
    price = PRODUCT_PRICES.get(key) or PRODUCT_PRICES.get(canon)
    return canon, name, price


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if is_banned(message.from_user.id):
        await message.answer("🚫 Доступ ограничен.")
        return

    args = message.text.split(maxsplit=1) if message.text else []
    payload = args[1].strip() if len(args) > 1 else ""
    canon, product_name, price = resolve_product(payload)

    if product_name and canon:
        user_id = message.from_user.id
        dialog = get_dialog(user_id)
        dialog["closed"] = True
        dialog["last_product"] = product_name
        dialog["fullname"] = message.from_user.full_name
        dialog["username"] = message.from_user.username
        save_json(DIALOGS_FILE, dialogs)

        price_line = f"\n💰 Цена: <b>{price} ₽</b>" if price else ""
        await message.answer(
            f"🛍 <b>Заказ с сайта</b>\n\n"
            f"Товар: <b>{product_name}</b>{price_line}\n\n"
            f"Оформить заказ?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Да, написать оператору",
                    callback_data=f"order_yes_{canon}"
                )],
                [InlineKeyboardButton(
                    text="⏳ Нет, позже",
                    callback_data=f"order_no_{canon}"
                )],
            ])
        )
        return

    welcome = (
        "👋 <b>Привет! VI Print</b>\n\n"
        "Добро пожаловать в бота бренда <b>VI print</b>!\n"
        "Здесь можно заказать 3D-модели на свой вкус или выбрать готовые.\n\n"
        "Нажми <b>«Купить 3D-модель»</b> и напиши сообщение — мы ответим быстро 😊"
    )
    await message.answer(welcome, reply_markup=main_keyboard)


@dp.callback_query(F.data.startswith("order_yes_"))
async def order_yes(callback: CallbackQuery):
    if is_banned(callback.from_user.id):
        await callback.answer("Доступ ограничен.", show_alert=True)
        return
    payload = callback.data[len("order_yes_"):]
    canon, product_name, price = resolve_product(payload)
    if not product_name:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    user_id = callback.from_user.id
    dialog = get_dialog(user_id)
    dialog["closed"] = False
    dialog["last_product"] = product_name
    dialog["fullname"] = callback.from_user.full_name
    dialog["username"] = callback.from_user.username
    save_json(DIALOGS_FILE, dialogs)

    price_txt = f" ({price} ₽)" if price else ""
    add_message(
        user_id,
        f"Хочу заказать: {product_name}{price_txt}",
        "user",
        callback.from_user.username,
        callback.from_user.full_name,
    )

    await callback.message.answer(
        f"✅ <b>Заказ: {product_name}</b>"
        + (f" — {price} ₽" if price else "")
        + "\n\nНапишите детали заказа (цвет, количество и т.д.).\n"
        "Оператор скоро ответит.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Закрыть диалог", callback_data="close_dialog")]
        ])
    )
    await callback.answer()

    await notify_operators(
        f"🛍 <b>Новый заказ с сайта!</b>\n"
        f"📦 <b>{product_name}</b>"
        + (f" — {price} ₽" if price else "")
        + f"\n🔑 <code>{canon}</code>\n"
        f"👤 {callback.from_user.full_name}\n"
        f"🆔 @{callback.from_user.username or 'нет'} · <code>{user_id}</code>\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
            [InlineKeyboardButton(text="💬 Открыть диалог", callback_data=f"adm_open_dialog_{user_id}")],
        ])
    )


@dp.callback_query(F.data.startswith("order_no_"))
async def order_no(callback: CallbackQuery):
    payload = callback.data[len("order_no_"):]
    _, product_name, _ = resolve_product(payload)
    product_name = product_name or "этот товар"
    user_id = callback.from_user.id
    dialog = get_dialog(user_id)
    dialog["closed"] = True
    save_json(DIALOGS_FILE, dialogs)
    await callback.message.answer(
        f"👍 Ок! Заказ <b>{product_name}</b> пока не оформляем.\n"
        "Когда будете готовы — нажмите кнопку на сайте.",
        reply_markup=main_keyboard
    )
    await callback.answer()


@dp.callback_query(F.data == "buy")
async def buy_callback(callback: CallbackQuery):
    if is_banned(callback.from_user.id):
        await callback.answer("Доступ ограничен.", show_alert=True)
        return
    user_id = callback.from_user.id
    dialog = get_dialog(user_id)
    dialog["closed"] = False
    dialog["fullname"] = callback.from_user.full_name
    dialog["username"] = callback.from_user.username
    save_json(DIALOGS_FILE, dialogs)

    await callback.message.answer(
        "✏️ <b>Напишите сообщение оператору</b>\n"
        "Опишите модель, которую хотите заказать.\n\n"
        "💡 Можно отправить текст, фото, файл или голосовое.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Закрыть диалог", callback_data="close_dialog")]
        ])
    )
    await callback.answer()

    await notify_operators(
        f"🆕 <b>Новый диалог</b>\n"
        f"👤 {callback.from_user.full_name}\n"
        f"🆔 @{callback.from_user.username or 'нет'} · <code>{user_id}</code>\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
            [InlineKeyboardButton(text="💬 Открыть", callback_data=f"adm_open_dialog_{user_id}")],
        ])
    )


@dp.callback_query(F.data == "close_dialog")
async def close_dialog_user(callback: CallbackQuery):
    uid = str(callback.from_user.id)
    if uid in dialogs:
        dialogs[uid]["closed"] = True
        dialogs[uid]["unread"] = 0
        save_json(DIALOGS_FILE, dialogs)
    await callback.message.answer(
        "🔒 <b>Диалог закрыт</b>\n"
        "Снова связаться можно через «Купить 3D-модель».",
        reply_markup=main_keyboard
    )
    await callback.answer()


@dp.callback_query(F.data == "info")
async def info_callback(callback: CallbackQuery):
    text = (
        "🔹 <b>VI print — 3D-модели на заказ</b>\n\n"
        "✅ <b>Что делаем:</b>\n"
        "• Модели для игр и анимации\n"
        "• Прототипы для бизнеса\n"
        "• Персонализированные подарки\n"
        "• Архитектурные макеты\n\n"
        "💰 <b>Цены:</b> от 150 ₽ · индивидуальный расчёт\n"
        "⏱ <b>Сроки:</b> от 1 дня\n\n"
        "📩 Для заказа нажмите «Купить 3D-модель»"
    )
    await callback.message.answer(text, reply_markup=main_keyboard)
    await callback.answer()


# ---------- Сообщения от пользователей ----------
@dp.message(F.text & ~F.text.startswith("/") & (~F.from_user.id.in_(OPERATOR_IDS)))
async def handle_user_text(message: Message):
    if is_banned(message.from_user.id):
        await message.answer("🚫 Доступ ограничен.")
        return

    user_id = message.from_user.id
    dialog = get_dialog(user_id)
    if dialog.get("closed"):
        await message.answer(
            "🔒 Диалог закрыт. Нажмите «Купить 3D-модель», чтобы начать новый.",
            reply_markup=main_keyboard
        )
        return

    add_message(
        user_id, message.text, "user",
        message.from_user.username, message.from_user.full_name
    )

    try:
        await notify_operators(
            f"💬 <b>Сообщение</b>\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 @{message.from_user.username or 'нет'} · <code>{user_id}</code>\n"
            f"📝 {message.text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
                [InlineKeyboardButton(text="📜 История", callback_data=f"adm_history_{user_id}")],
            ])
        )
    except Exception:
        logging.exception("Пересылка текста оператору")
        await message.answer("⚠️ Не удалось отправить. Попробуйте позже.", reply_markup=main_keyboard)
        return

    await message.answer(
        "✅ Сообщение отправлено оператору! Ожидайте ответа.",
        reply_markup=main_keyboard
    )


@dp.message(
    (F.photo | F.document | F.voice | F.video | F.sticker | F.animation | F.video_note | F.audio)
    & (~F.from_user.id.in_(OPERATOR_IDS))
)
async def handle_user_media(message: Message):
    if is_banned(message.from_user.id):
        await message.answer("🚫 Доступ ограничен.")
        return

    user_id = message.from_user.id
    dialog = get_dialog(user_id)
    if dialog.get("closed"):
        await message.answer("🔒 Диалог закрыт.", reply_markup=main_keyboard)
        return

    media_type = (
        "📷 Фото" if message.photo else
        "📄 Файл" if message.document else
        "🎤 Голос" if message.voice else
        "🎥 Видео" if message.video else
        "🖼 Стикер" if message.sticker else
        "🎞 GIF" if message.animation else
        "🔵 Кружок" if message.video_note else
        "🎵 Аудио" if message.audio else
        "📎 Медиа"
    )
    caption = message.caption or ""
    add_message(
        user_id, caption or media_type, "user",
        message.from_user.username, message.from_user.full_name,
        media_type=media_type
    )

    caption_text = (
        f"💬 <b>{media_type} от пользователя</b>\n"
        f"👤 {message.from_user.full_name}\n"
        f"🆔 @{message.from_user.username or 'нет'} · <code>{user_id}</code>"
    )
    if caption:
        caption_text += f"\n📝 {caption}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
    ])

    try:
        for op_id in OPERATOR_IDS:
            try:
                if message.photo:
                    await bot.send_photo(op_id, message.photo[-1].file_id, caption=caption_text, reply_markup=kb)
                elif message.document:
                    await bot.send_document(op_id, message.document.file_id, caption=caption_text, reply_markup=kb)
                elif message.voice:
                    await bot.send_voice(op_id, message.voice.file_id, caption=caption_text, reply_markup=kb)
                elif message.video:
                    await bot.send_video(op_id, message.video.file_id, caption=caption_text, reply_markup=kb)
                elif message.sticker:
                    await bot.send_sticker(op_id, message.sticker.file_id)
                    await bot.send_message(op_id, caption_text, reply_markup=kb)
                elif message.animation:
                    await bot.send_animation(op_id, message.animation.file_id, caption=caption_text, reply_markup=kb)
                elif message.video_note:
                    await bot.send_video_note(op_id, message.video_note.file_id)
                    await bot.send_message(op_id, caption_text, reply_markup=kb)
                elif message.audio:
                    await bot.send_audio(op_id, message.audio.file_id, caption=caption_text, reply_markup=kb)
            except Exception:
                logging.exception("Пересылка медиа оператору %s", op_id)
    except Exception:
        logging.exception("Пересылка медиа оператору")

    await message.answer("✅ Отправлено оператору!", reply_markup=main_keyboard)


# ==================== АДМИНКА ====================

@dp.message(Command("operator", "admin", "panel"))
async def cmd_operator(message: Message):
    if not is_operator(message.from_user.id):
        return
    stats = calc_stats()
    text = (
        "🛠 <b>Админ-панель VI Print</b>\n\n"
        f"💬 Активных: <b>{stats['active']}</b>\n"
        f"📂 Открытых: {stats['open']} · Закрытых: {stats['closed']}\n"
        f"👥 Всего клиентов: {stats['total_users']}\n"
        f"📨 Сообщений сегодня: {stats['today_msgs']}"
    )
    await message.answer(text, reply_markup=admin_main_kb())


@dp.callback_query(F.data == "adm_menu")
async def adm_menu(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_states.pop(callback.from_user.id, None)
    stats = calc_stats()
    text = (
        "🛠 <b>Админ-панель VI Print</b>\n\n"
        f"💬 Активных: <b>{stats['active']}</b>\n"
        f"📂 Открытых: {stats['open']} · Закрытых: {stats['closed']}\n"
        f"👥 Всего клиентов: {stats['total_users']}\n"
        f"📨 Сообщений сегодня: {stats['today_msgs']}"
    )
    await callback.message.edit_text(text, reply_markup=admin_main_kb())
    await callback.answer()


@dp.callback_query(F.data == "adm_active")
async def adm_active(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    active = get_active_dialogs()
    if not active:
        await callback.message.edit_text(
            "📭 <b>Нет активных диалогов</b>\n\nВсе ответы даны или диалоги закрыты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")]
            ])
        )
        await callback.answer()
        return

    text = f"💬 <b>Активные диалоги ({len(active)})</b>\n\n"
    buttons = []
    for d in active[:20]:
        unread = f" 🔴{d['unread']}" if d.get("unread") else ""
        uname = f"@{d['username']}" if d.get("username") else ""
        text += (
            f"👤 <b>{d['fullname']}</b> {uname}{unread}\n"
            f"   📝 {d['last_message'] or '—'}\n"
            f"   ⏱ {format_time(d['timestamp'])}\n\n"
        )
        buttons.append([InlineKeyboardButton(
            text=f"➡️ {d['fullname'][:22]}{unread}",
            callback_data=f"adm_open_dialog_{d['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data == "adm_all")
async def adm_all(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = get_all_dialogs(closed_only=False, limit=25)
    if not items:
        await callback.message.edit_text(
            "📂 Нет открытых диалогов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")]
            ])
        )
        await callback.answer()
        return

    text = f"📂 <b>Открытые диалоги ({len(items)})</b>\n\n"
    buttons = []
    for d in items:
        uname = f"@{d['username']}" if d.get("username") else ""
        text += f"👤 {d['fullname']} {uname} · 💬{d['msg_count']}\n"
        buttons.append([InlineKeyboardButton(
            text=f"➡️ {d['fullname'][:24]}",
            callback_data=f"adm_open_dialog_{d['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data == "adm_closed")
async def adm_closed(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = get_all_dialogs(closed_only=True, limit=25)
    if not items:
        await callback.message.edit_text(
            "🔒 Нет закрытых диалогов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")]
            ])
        )
        await callback.answer()
        return

    text = f"🔒 <b>Закрытые ({len(items)})</b>\n\n"
    buttons = []
    for d in items:
        text += f"👤 {d['fullname']} · 💬{d['msg_count']}\n"
        buttons.append([InlineKeyboardButton(
            text=f"➡️ {d['fullname'][:24]}",
            callback_data=f"adm_open_dialog_{d['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_open_dialog_"))
async def adm_open_dialog(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_open_dialog_"):]
    dialog = get_dialog(user_id)
    dialog["unread"] = 0
    save_json(DIALOGS_FILE, dialogs)

    name = dialog.get("fullname") or "Пользователь"
    uname = f"@{dialog['username']}" if dialog.get("username") else "без username"
    status = "🔒 Закрыт" if dialog.get("closed") else "🟢 Открыт"
    product = dialog.get("last_product") or "—"
    msg_count = len(dialog.get("messages") or [])

    text = (
        f"👤 <b>{name}</b>\n"
        f"🆔 <code>{user_id}</code>\n"
        f"🔗 {uname}\n"
        f"📦 Товар: {product}\n"
        f"💬 Сообщений: {msg_count}\n"
        f"Статус: {status}\n"
        f"Создан: {format_time(dialog.get('created_at', ''))}"
    )
    await callback.message.edit_text(text, reply_markup=dialog_actions_kb(user_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_history_"))
async def adm_history(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_history_"):]
    text = build_history_text(user_id, limit=20)
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"adm_reply_{user_id}")],
            [InlineKeyboardButton(text="◀️ К диалогу", callback_data=f"adm_open_dialog_{user_id}")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="adm_menu")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_card_"))
async def adm_card(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_card_"):]
    dialog = get_dialog(user_id)
    try:
        chat = await bot.get_chat(int(user_id))
        name = chat.full_name or dialog.get("fullname") or "—"
        uname = f"@{chat.username}" if chat.username else "нет"
        bio = getattr(chat, "bio", None) or "—"
    except Exception:
        name = dialog.get("fullname") or "—"
        uname = f"@{dialog['username']}" if dialog.get("username") else "нет"
        bio = "—"

    banned = "🚫 Да" if is_banned(user_id) else "✅ Нет"
    text = (
        f"👤 <b>Карточка клиента</b>\n\n"
        f"Имя: <b>{name}</b>\n"
        f"Username: {uname}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Бан: {banned}\n"
        f"Товар: {dialog.get('last_product') or '—'}\n"
        f"Сообщений: {len(dialog.get('messages') or [])}\n"
        f"Статус: {'🔒 Закрыт' if dialog.get('closed') else '🟢 Открыт'}\n"
        f"Первый контакт: {format_time(dialog.get('created_at', ''))}"
    )
    await callback.message.edit_text(text, reply_markup=dialog_actions_kb(user_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_reply_"))
async def adm_reply_start(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_reply_"):]
    user_states[callback.from_user.id] = f"reply_{user_id}"
    await callback.message.edit_text(
        f"✏️ <b>Режим ответа</b>\n"
        f"Клиент: <code>{user_id}</code>\n\n"
        "Отправьте любое сообщение — оно уйдёт клиенту:\n"
        "текст · фото · видео · голос · стикер · файл · GIF · кружок · аудио",
        reply_markup=cancel_kb(f"adm_open_dialog_{user_id}")
    )
    await callback.answer()


async def _show_dialog_card(message_or_cb, user_id: str, alert: str | None = None):
    """Показать карточку диалога (edit text)."""
    dialog = get_dialog(user_id)
    name = dialog.get("fullname") or "Пользователь"
    uname = f"@{dialog['username']}" if dialog.get("username") else "без username"
    product = dialog.get("last_product") or "—"
    banned = "🚫 Да" if is_banned(user_id) else "✅ Нет"
    text = (
        f"👤 <b>{name}</b>\n"
        f"🆔 <code>{user_id}</code>\n"
        f"🔗 {uname}\n"
        f"📦 Товар: {product}\n"
        f"💬 Сообщений: {len(dialog.get('messages') or [])}\n"
        f"Статус: {'🔒 Закрыт' if dialog.get('closed') else '🟢 Открыт'}\n"
        f"Бан: {banned}\n"
        f"Создан: {format_time(dialog.get('created_at', ''))}"
    )
    if hasattr(message_or_cb, "message"):
        if alert:
            await message_or_cb.answer(alert, show_alert=True)
        await message_or_cb.message.edit_text(text, reply_markup=dialog_actions_kb(user_id))
    else:
        await message_or_cb.edit_text(text, reply_markup=dialog_actions_kb(user_id))


@dp.callback_query(F.data.startswith("adm_close_"))
async def adm_close(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_close_"):]
    dialog = get_dialog(user_id)
    dialog["closed"] = True
    dialog["unread"] = 0
    save_json(DIALOGS_FILE, dialogs)
    try:
        await bot.send_message(
            int(user_id),
            "🔒 Оператор закрыл диалог.\n"
            "Чтобы написать снова — нажмите «Купить 3D-модель».",
            reply_markup=main_keyboard
        )
    except Exception:
        pass
    await _show_dialog_card(callback, user_id, "Диалог закрыт")


@dp.callback_query(F.data.regexp(r"^adm_open_\d+$"))
async def adm_reopen(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_open_"):]
    dialog = get_dialog(user_id)
    dialog["closed"] = False
    save_json(DIALOGS_FILE, dialogs)
    await _show_dialog_card(callback, user_id, "Диалог открыт")


@dp.callback_query(F.data.startswith("adm_ban_"))
async def adm_ban(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_ban_"):]
    if user_id in bans:
        bans.discard(user_id)
        save_json(BANS_FILE, list(bans))
        alert = "Разбанен"
    else:
        bans.add(user_id)
        save_json(BANS_FILE, list(bans))
        dialog = get_dialog(user_id)
        dialog["closed"] = True
        save_json(DIALOGS_FILE, dialogs)
        alert = "Забанен"
    await _show_dialog_card(callback, user_id, alert)


@dp.callback_query(F.data.startswith("adm_delete_"))
async def adm_delete(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.data[len("adm_delete_"):]
    if user_id in dialogs:
        del dialogs[user_id]
        save_json(DIALOGS_FILE, dialogs)
    await callback.answer("Диалог удалён", show_alert=True)
    await callback.message.edit_text(
        "🗑 Диалог удалён.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data="adm_menu")]
        ])
    )


@dp.callback_query(F.data == "adm_stats")
async def adm_stats(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    s = calc_stats()
    products_text = "\n".join(
        f"  • {name}: {cnt}" for name, cnt in s["products"].items()
    ) or "  —"
    text = (
        "📊 <b>Статистика VI Print</b>\n\n"
        f"👥 Всего клиентов: <b>{s['total_users']}</b>\n"
        f"💬 Всего сообщений: <b>{s['total_msgs']}</b>\n"
        f"   ↳ от клиентов: {s['user_msgs']}\n"
        f"   ↳ от оператора: {s['op_msgs']}\n\n"
        f"📅 Сегодня:\n"
        f"   • Активных клиентов: {s['today_users']}\n"
        f"   • Сообщений: {s['today_msgs']}\n\n"
        f"📆 За 7 дней: {s['week_users']} клиентов\n\n"
        f"🟢 Активных диалогов: <b>{s['active']}</b>\n"
        f"📂 Открытых: {s['open']} · 🔒 Закрытых: {s['closed']}\n"
        f"🚫 Банов: {s['bans']}\n\n"
        f"🛍 Заказы (последний товар):\n{products_text}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_stats")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_search")
async def adm_search(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_states[callback.from_user.id] = "search"
    await callback.message.edit_text(
        "🔍 <b>Поиск клиента</b>\n\n"
        "Отправьте ID (число) или username (без @).",
        reply_markup=cancel_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_states[callback.from_user.id] = "broadcast"
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        f"Получателей: <b>{len(dialogs)}</b>\n\n"
        "Отправьте текст сообщения. Оно уйдёт всем, кто когда-либо писал боту.\n"
        "Можно также фото с подписью.",
        reply_markup=cancel_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "adm_bans")
async def adm_bans(callback: CallbackQuery):
    if not is_operator(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not bans:
        text = "🚫 <b>Список банов пуст</b>"
        buttons = [[InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")]]
    else:
        text = f"🚫 <b>Забанено: {len(bans)}</b>\n\n"
        buttons = []
        for uid in list(bans)[:30]:
            d = dialogs.get(uid, {})
            name = d.get("fullname") or uid
            text += f"• {name} (<code>{uid}</code>)\n"
            buttons.append([InlineKeyboardButton(
                text=f"✅ Разбан {name[:18]}",
                callback_data=f"adm_ban_{uid}"
            )])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# ---------- Ответы оператора и состояния ----------
@dp.message(F.from_user.id.in_(OPERATOR_IDS))
async def handle_operator_messages(message: Message):
    state = user_states.get(message.from_user.id)

    # --- Поиск ---
    if state == "search":
        query = (message.text or "").strip().lstrip("@")
        user_states.pop(message.from_user.id, None)
        found = None
        if query.isdigit() and query in dialogs:
            found = query
        else:
            for uid, d in dialogs.items():
                if (d.get("username") or "").lower() == query.lower():
                    found = uid
                    break
                if query.lower() in (d.get("fullname") or "").lower():
                    found = uid
                    break
        if not found:
            await message.answer(
                f"❌ Ничего не найдено по «{query}»",
                reply_markup=admin_main_kb()
            )
            return
        # Показать карточку
        dialog = get_dialog(found)
        name = dialog.get("fullname") or "—"
        text = (
            f"🔍 Найден: <b>{name}</b>\n"
            f"ID: <code>{found}</code>\n"
            f"@{dialog.get('username') or 'нет'}"
        )
        await message.answer(text, reply_markup=dialog_actions_kb(found))
        return

    # --- Рассылка ---
    if state == "broadcast":
        user_states.pop(message.from_user.id, None)
        text = message.text or message.caption or ""
        sent = 0
        fail = 0
        for uid in list(dialogs.keys()):
            if is_banned(uid):
                continue
            try:
                if message.photo:
                    await bot.send_photo(int(uid), message.photo[-1].file_id, caption=text or None)
                elif message.video:
                    await bot.send_video(int(uid), message.video.file_id, caption=text or None)
                elif message.document:
                    await bot.send_document(int(uid), message.document.file_id, caption=text or None)
                elif message.animation:
                    await bot.send_animation(int(uid), message.animation.file_id, caption=text or None)
                elif message.voice:
                    await bot.send_voice(int(uid), message.voice.file_id)
                elif message.audio:
                    await bot.send_audio(int(uid), message.audio.file_id, caption=text or None)
                elif message.sticker:
                    await bot.send_sticker(int(uid), message.sticker.file_id)
                elif message.video_note:
                    await bot.send_video_note(int(uid), message.video_note.file_id)
                elif text:
                    await bot.send_message(int(uid), text)
                else:
                    await message.copy_to(chat_id=int(uid))
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail += 1
        await message.answer(
            f"📢 <b>Рассылка завершена</b>\n"
            f"✅ Отправлено: {sent}\n"
            f"❌ Ошибок: {fail}",
            reply_markup=admin_main_kb()
        )
        return

    # --- Ответ клиенту (любой тип сообщения) ---
    if state and state.startswith("reply_"):
        target_id = state.split("_", 1)[1]
        user_states.pop(message.from_user.id, None)
        tid = int(target_id)
        cap = message.caption or ""

        try:
            if message.photo:
                await bot.send_photo(
                    tid, message.photo[-1].file_id,
                    caption=(f"📨 <b>Ответ оператора:</b>\n{cap}" if cap else "📨 <b>Ответ оператора</b>")
                )
                add_message(target_id, cap or "[фото]", "operator", media_type="📷 Фото")
            elif message.video:
                await bot.send_video(
                    tid, message.video.file_id,
                    caption=(f"📨 <b>Ответ оператора:</b>\n{cap}" if cap else "📨 <b>Ответ оператора</b>")
                )
                add_message(target_id, cap or "[видео]", "operator", media_type="🎥 Видео")
            elif message.document:
                await bot.send_document(
                    tid, message.document.file_id,
                    caption=(f"📨 <b>Ответ оператора:</b>\n{cap}" if cap else "📨 <b>Ответ оператора</b>")
                )
                add_message(target_id, cap or "[файл]", "operator", media_type="📄 Файл")
            elif message.voice:
                await bot.send_voice(tid, message.voice.file_id)
                if cap:
                    await bot.send_message(tid, f"📨 {cap}")
                add_message(target_id, cap or "[голос]", "operator", media_type="🎤 Голос")
            elif message.audio:
                await bot.send_audio(
                    tid, message.audio.file_id,
                    caption=(f"📨 <b>Ответ оператора:</b>\n{cap}" if cap else "📨 <b>Ответ оператора</b>")
                )
                add_message(target_id, cap or "[аудио]", "operator", media_type="🎵 Аудио")
            elif message.video_note:
                await bot.send_video_note(tid, message.video_note.file_id)
                add_message(target_id, "[кружок]", "operator", media_type="🔵 Кружок")
            elif message.sticker:
                await bot.send_sticker(tid, message.sticker.file_id)
                add_message(target_id, "[стикер]", "operator", media_type="🖼 Стикер")
            elif message.animation:
                await bot.send_animation(
                    tid, message.animation.file_id,
                    caption=(f"📨 <b>Ответ оператора:</b>\n{cap}" if cap else "📨 <b>Ответ оператора</b>")
                )
                add_message(target_id, cap or "[GIF]", "operator", media_type="🎞 GIF")
            elif message.location:
                await bot.send_location(tid, message.location.latitude, message.location.longitude)
                add_message(target_id, "[геолокация]", "operator", media_type="📍 Локация")
            elif message.contact:
                await bot.send_contact(
                    tid,
                    phone_number=message.contact.phone_number,
                    first_name=message.contact.first_name,
                    last_name=message.contact.last_name or "",
                )
                add_message(target_id, "[контакт]", "operator", media_type="👤 Контакт")
            elif message.venue:
                await bot.send_venue(
                    tid,
                    latitude=message.venue.location.latitude,
                    longitude=message.venue.location.longitude,
                    title=message.venue.title,
                    address=message.venue.address,
                )
                add_message(target_id, f"[место: {message.venue.title}]", "operator", media_type="📍 Место")
            elif message.text:
                await bot.send_message(tid, f"📨 <b>Ответ оператора:</b>\n{message.text}")
                add_message(target_id, message.text, "operator")
            else:
                # Универсальный fallback — копируем сообщение как есть
                await message.copy_to(chat_id=tid)
                add_message(target_id, "[сообщение]", "operator", media_type="📎 Медиа")

            await message.answer(
                f"✅ Отправлено клиенту <code>{target_id}</code>",
                reply_markup=dialog_actions_kb(target_id)
            )
        except Exception as e:
            logging.exception("Ошибка ответа клиенту")
            await message.answer(
                f"❌ Не удалось отправить: {e}",
                reply_markup=admin_main_kb()
            )
        return

    # По умолчанию — показать меню
    if message.text and not message.text.startswith("/"):
        await message.answer(
            "🛠 Используйте кнопки админ-панели или команды:\n"
            "/operator — открыть панель\n"
            "/dialogs — активные\n"
            "/stats — статистика",
            reply_markup=admin_main_kb()
        )


# Быстрые команды
@dp.message(Command("dialogs"))
async def cmd_dialogs(message: Message):
    if not is_operator(message.from_user.id):
        return
    active = get_active_dialogs()
    if not active:
        await message.answer(
            "📭 Нет активных диалогов.",
            reply_markup=admin_main_kb()
        )
        return
    text = f"💬 <b>Активные ({len(active)})</b>\n\n"
    buttons = []
    for d in active[:20]:
        unread = f" 🔴{d['unread']}" if d.get("unread") else ""
        text += f"👤 <b>{d['fullname']}</b>{unread}\n   📝 {d['last_message'] or '—'}\n\n"
        buttons.append([InlineKeyboardButton(
            text=f"➡️ {d['fullname'][:22]}{unread}",
            callback_data=f"adm_open_dialog_{d['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🏠 Меню", callback_data="adm_menu")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_operator(message.from_user.id):
        return
    s = calc_stats()
    text = (
        f"📊 Клиентов: {s['total_users']} · Сообщений: {s['total_msgs']}\n"
        f"Активных: {s['active']} · Сегодня: {s['today_msgs']} сообщ."
    )
    await message.answer(text, reply_markup=admin_main_kb())


# ==================== ЗАПУСК ====================
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    print("🤖 Бот VI Print запущен!")
    print(f"📢 Канал: {CHANNEL_LINK}")
    print(f"🛠 Операторы: {', '.join(map(str, OPERATOR_IDS))}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
