# bot.py
import asyncio
import logging
import os
from dotenv import load_dotenv
import re
import urllib.parse
from datetime import datetime, timedelta, time
from typing import Optional, Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, Update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from scraper import RbdaScraper
from database import Database
from monitor import FineMonitor
from admin_panel import admin_router, set_admin_dependencies
from bot_mode_service import BotModeService, BotMode
from admin_roles import get_user_role, AdminRole
import bot_mode_service
import subscription_service

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SCRAPER_LOGIN = "neruadmin"
SCRAPER_PASSWORD = "neru900876"
MONITOR_POLL_INTERVAL = int(os.getenv("MONITOR_POLL_INTERVAL", "1800"))  # 30 minutes default
MONITOR_RATE_LIMIT = float(os.getenv("MONITOR_RATE_LIMIT", "5.0"))  # 5 seconds between requests

# Admin user IDs from environment
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(uid.strip()) for uid in ADMIN_IDS_STR.split(",") if uid.strip().isdigit()]

logger = logging.getLogger(__name__)

# Daily quotas
DAILY_QUOTA_FREE = 5
DAILY_QUOTA_PREMIUM = 100

class UserStates(StatesGroup):
    waiting_for_plate = State()
    waiting_for_binding_plate = State()
    waiting_for_binding_confirmation = State()

router = Router()
scraper = RbdaScraper(login=SCRAPER_LOGIN, password=SCRAPER_PASSWORD)
database = Database()
fine_monitor = None  # Will be initialized in main()
mode_service = None  # Will be initialized in main()
user_fines_cache = {}
user_join_dates = {}
user_pagination_state = {}
user_pagination_message_ids = {}  # Store message IDs for pagination controls
user_fine_message_ids = {}  # Store message IDs for fine cards per page

def escape_markdown(text: str) -> str:
    if not isinstance(text, str): text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

class BlockedUserMiddleware(BaseMiddleware):
    """
    Middleware to check if user is blocked before processing any message or callback.
    Admins are exempt from this check.
    """
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        # Get user from event (message or callback query)
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        
        # If no user found or user is admin, allow access
        if not user:
            return await handler(event, data)
        
        # Check if user is admin (admins can always access)
        from admin_roles import get_user_role, AdminRole
        if get_user_role(user.id) >= AdminRole.RND:
            return await handler(event, data)
        
        # Check if user is blocked
        try:
            is_blocked = await database.is_user_blocked(user.id)
            if is_blocked:
                blocked_message = (
                    "⛔ *Ваш аккаунт заблокирован*\n\n"
                    "Вы не можете использовать бота\\.\n"
                    "Для получения дополнительной информации обратитесь в поддержку\\."
                )
                
                if isinstance(event, Message):
                    await event.answer(
                        blocked_message,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "⛔ Ваш аккаунт заблокирован",
                        show_alert=True
                    )
                
                # Stop processing
                return
        except Exception as e:
            logger.error(f"Error checking user block status: {e}")
        
        # User is not blocked, continue processing
        return await handler(event, data)

async def get_premium_expiry_date(user_id: int) -> Optional[datetime]:
    """
    Get the premium expiry date for a user from either subscription or user premium field.
    Returns the latest expiry date if both exist.
    """
    user = await database.get_user(user_id)
    subscription = await database.get_active_subscription(user_id)
    
    expiry_dates = []
    
    # Check user.premium_expires_at
    if user and user.get('premium_expires_at'):
        expiry_dates.append(datetime.fromisoformat(user['premium_expires_at']))
    
    # Check active subscription
    if subscription and subscription.get('expires_at'):
        expiry_dates.append(datetime.fromisoformat(subscription['expires_at']))
    
    # Return the latest expiry date (or None if no premium)
    if expiry_dates:
        return max(expiry_dates)
    
    return None

def get_main_menu(is_premium: bool = False, user_id: int = None):
    keyboard_buttons = [
        [KeyboardButton(text="🚗 Проверить авто")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💎 Подписка")],
        [KeyboardButton(text="Техподдержка")]
    ]
    
    # Add admin panel button for admins
    if user_id and get_user_role(user_id) >= AdminRole.RND:
        keyboard_buttons.append([KeyboardButton(text="🔐 Админ-панель")])
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=keyboard_buttons,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие или введите номер авто"
    )
    return keyboard

async def check_user_access(user_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if user has access to make requests.
    Respects bot mode: disabled mode blocks all, test mode allows free access.
    Returns (can_access, error_message)
    """
    # Check bot mode first
    if mode_service:
        current_mode = await mode_service.get_mode()
        
        # In test mode, allow free access for everyone
        if current_mode == BotMode.TEST:
            return True, None
    
    # Check if user is blocked
    if await database.is_user_blocked(user_id):
        return False, "⛔ Ваш аккаунт заблокирован\\. Обратитесь в поддержку\\."
    
    # Check daily quota
    is_premium = await database.is_user_premium(user_id)
    quota_limit = DAILY_QUOTA_PREMIUM if is_premium else DAILY_QUOTA_FREE
    current_usage = await database.get_daily_usage(user_id)
    
    if current_usage >= quota_limit:
        # Calculate time until midnight
        now = datetime.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min)
        time_remaining = midnight - now
        hours = time_remaining.seconds // 3600
        minutes = (time_remaining.seconds % 3600) // 60
        
        time_str = ""
        if hours > 0:
            time_str = f"{hours} ч\\. {minutes} мин\\."
        else:
            time_str = f"{minutes} мин\\."
        
        quota_type = "премиум" if is_premium else "бесплатный"
        message = (
            f"⏳ *Лимит запросов исчерпан*\n\n"
            f"Ваш {quota_type} лимит: {escape_markdown(str(quota_limit))} запросов/день\\.\n"
            f"Использовано сегодня: {escape_markdown(str(current_usage))}\n\n"
            f"⏰ Лимит обновится через: {time_str}\n\n"
        )
        
        if not is_premium:
            message += "_💎 Хотите больше запросов\\? Приобретите премиум\\!_"
        
        return False, message
    
    return True, None

def validate_plate_number(plate: str) -> bool:
    plate = plate.strip().upper()
    if len(plate) < 4 or len(plate) > 10:
        return False
    if not re.search(r'\d', plate):
        return False
    if not re.search(r'[A-ZА-Я]', plate, re.IGNORECASE):
        return False
    return True

def get_pagination_keyboard(current_page: int, total_pages: int, user_id: int):
    builder = InlineKeyboardBuilder()
    buttons = []
    
    if current_page > 0:
        buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page:{current_page-1}"))
    
    buttons.append(InlineKeyboardButton(text=f"Страница {current_page+1} из {total_pages}", callback_data="page:info"))
    
    if current_page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"page:{current_page+1}"))
    
    builder.row(*buttons)
    return builder.as_markup()

async def send_fines_page(message: Message, user_id: int, page: int):
    cached_data = user_fines_cache.get(user_id)
    if not cached_data:
        await message.answer("Данные устарели, выполните поиск заново.")
        return
    
    fines = cached_data.get("fines", [])
    if not fines:
        return
    
    FINES_PER_PAGE = 5
    total_pages = (len(fines) + FINES_PER_PAGE - 1) // FINES_PER_PAGE
    
    if page < 0 or page >= total_pages:
        return
    
    start_idx = page * FINES_PER_PAGE
    end_idx = min(start_idx + FINES_PER_PAGE, len(fines))
    page_fines = fines[start_idx:end_idx]
    
    # Store message IDs for this page to delete them later
    message_ids = []
    
    for i, fine in enumerate(page_fines):
        global_idx = start_idx + i
        builder = InlineKeyboardBuilder()
        media_buttons = []
        for media_key in ["фото_1", "фото_2", "доп_фото", "видео"]:
            if media_key in fine.get("media_links", {}):
                emoji = "📷" if "фото" in media_key else "🎥"
                media_name = media_key.replace('_', ' ').title()
                media_buttons.append(
                    InlineKeyboardButton(text=f"{emoji} {media_name}", callback_data=f"media:{global_idx}:{media_key}")
                )
        
        if media_buttons:
            builder.row(*media_buttons)
        
        amount_numeric = re.sub(r'[^0-9]', '', fine['amount'])
        payment_url = f"https://pay.dc.tj/pay.php?a={fine['order']}&s={amount_numeric}&c=&f1=346&f2=#kortiMilli"
        builder.button(text=f"💳 Оплатить {fine.get('amount', '')}", url=payment_url)
        builder.adjust(min(len(media_buttons), 2), 1)

        fine_text = (
            f"*\\#{escape_markdown(str(global_idx + 1))}* 📋 *Штраф*\n\n"
            f"📄 *Ордер:* `{escape_markdown(fine.get('order', 'N/A'))}`\n"
            f"📅 *Дата:* {escape_markdown(fine.get('date', 'N/A'))}\n"
            f"⚠️ *Нарушение:* _{escape_markdown(fine.get('violation', 'N/A'))}_\n"
            f"💰 *Сумма:* *{escape_markdown(fine.get('amount', 'N/A'))}*"
        )
        msg = await message.answer(fine_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)
        message_ids.append(msg.message_id)
        await asyncio.sleep(0.1)
    
    # Send pagination controls
    if total_pages > 1:
        pagination_text = f"📄 *Страница {page + 1} из {total_pages}*"
        pagination_msg = await message.answer(
            pagination_text, 
            reply_markup=get_pagination_keyboard(page, total_pages, user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Store pagination message ID for editing later
        user_pagination_message_ids[user_id] = pagination_msg.message_id
        message_ids.append(pagination_msg.message_id)
    
    # Store all message IDs for this page
    user_fine_message_ids[user_id] = message_ids

def format_vehicle_info(vehicle_info: dict) -> str:
    if not vehicle_info:
        return "Информация об автомобиле не найдена\\."
    
    parts = []
    parts.append("*📋 Информация об автомобиле:*\n")
    
    if 'plate' in vehicle_info:
        parts.append(f"🚗 *Номер:* `{escape_markdown(vehicle_info['plate'])}`")
    if 'model' in vehicle_info:
        parts.append(f"🏎 *Модель:* {escape_markdown(vehicle_info['model'])}")
    if 'brand' in vehicle_info:
        parts.append(f"🏷 *Марка:* {escape_markdown(vehicle_info['brand'])}")
    if 'color' in vehicle_info:
        parts.append(f"🎨 *Цвет:* {escape_markdown(vehicle_info['color'])}")
    if 'fine_count' in vehicle_info:
        parts.append(f"⚠️ *Кол\\-во штрафов:* {escape_markdown(vehicle_info['fine_count'])}")
    if 'total_amount' in vehicle_info:
        parts.append(f"💰 *Общая сумма:* {escape_markdown(vehicle_info['total_amount'])}")
    if 'year' in vehicle_info:
        parts.append(f"📅 *Год:* {escape_markdown(vehicle_info['year'])}")
    if 'owner' in vehicle_info:
        parts.append(f"👤 *Владелец:* {escape_markdown(vehicle_info['owner'])}")
    if 'vin' in vehicle_info:
        parts.append(f"🔢 *VIN:* `{escape_markdown(vehicle_info['vin'])}`")
    
    return '\n'.join(parts)

async def check_bot_disabled() -> tuple[bool, Optional[str]]:
    """
    Check if bot is in disabled mode.
    Returns (is_disabled, maintenance_message)
    """
    if not mode_service:
        return False, None
    
    current_mode = await mode_service.get_mode()
    if current_mode == BotMode.DISABLED:
        message = (
            "🔴 *Бот временно недоступен*\n\n"
            "🔧 Проводятся технические работы\\.\n"
            "Пожалуйста, попробуйте позже\\.\n\n"
            "_Приносим извинения за неудобства\\!_"
        )
        return True, message
    return False, None

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Check if bot is disabled
    is_disabled, maintenance_msg = await check_bot_disabled()
    if is_disabled:
        await message.answer(maintenance_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user_id = message.from_user.id
    user = message.from_user
    
    # Create or get user
    await database.get_or_create_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Track join date for legacy compatibility
    if user_id not in user_join_dates:
        user_join_dates[user_id] = datetime.now()
    
    # Auto-detach expired bindings
    await database.remove_expired_bindings()
    
    await state.clear()
    
    is_premium = await database.is_user_premium(user_id)
    
    welcome_text = (
        "👋 *Добро пожаловать в Intellex Mobility\\!*\n\n"
        "Я помогу вам проверить штрафы по номеру автомобиля\\.\n\n"
        "Выберите действие из меню ниже или сразу отправьте номер авто\\."
    )
    
    if is_premium:
        welcome_text += "\n\n✨ _У вас активна премиум\\-подписка\\!_"
    
    # Add mode-specific banners
    if mode_service:
        current_mode = await mode_service.get_mode()
        if current_mode == BotMode.TEST:
            welcome_text += "\n\n🧪 *Пока все бесплатно\\!* Бот в тестовом режиме\\."
        elif current_mode == BotMode.DISCOUNT50:
            welcome_text += "\n\n💎 *Специальное предложение\\!* 🎁 Действует скидка 50% на все подписки\\!"
        elif current_mode == BotMode.DISCOUNT20:
            welcome_text += "\n\n💰 *Специальное предложение\\!* 🎁 Действует скидка 20% на все подписки\\!"
    
    await message.answer(welcome_text, reply_markup=get_main_menu(is_premium, user_id), parse_mode=ParseMode.MARKDOWN_V2)

@router.message(F.text == "🚗 Проверить авто")
async def check_car_button(message: Message, state: FSMContext):
    # Check if bot is disabled
    is_disabled, maintenance_msg = await check_bot_disabled()
    if is_disabled:
        await message.answer(maintenance_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    await state.set_state(UserStates.waiting_for_plate)
    await message.answer(
        "📋 Введите номер автомобиля в формате:\n"
        "• 0000AA01 или 000AA01\n\n"
        "Поддерживаются все зарегистрированные типы номеров\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

@router.message(F.text == "🔐 Админ-панель")
async def admin_panel_button(message: Message):
    """Handler for admin panel button"""
    user_id = message.from_user.id
    
    # Check if user has admin role
    if get_user_role(user_id) < AdminRole.RND:
        await message.answer(
            "⛔ *Доступ запрещён*\n\n"
            "У вас нет прав доступа к панели администратора\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Import admin panel function
    from admin_panel import build_admin_menu
    from admin_logger import log_admin_action
    
    # Get role name
    role_names = {
        AdminRole.ADMIN: "Администратор",
        AdminRole.CO: "Со-Администратор",
        AdminRole.RND: "R&D Администратор"
    }
    user_role = get_user_role(user_id)
    role_name = role_names.get(user_role, "Unknown")
    
    # Build welcome message
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or "Unknown"
    full_name = f"{first_name} {last_name}".strip() or username
    
    welcome_text = (
        "🔐 *Панель администратора*\n\n"
        f"👤 *Администратор:* {escape_markdown(full_name)}\n"
        f"🎭 *Роль:* {escape_markdown(role_name)}\n"
        f"🆔 *ID:* `{user_id}`\n\n"
        "Выберите раздел для управления:"
    )
    
    # Build menu with sections available to this role
    menu = build_admin_menu(user_role)
    
    await message.answer(
        welcome_text,
        reply_markup=menu.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    # Log admin panel access
    bot = message.bot
    await log_admin_action(
        bot=bot,
        admin_id=user_id,
        admin_name=full_name,
        action="Accessed Admin Panel via Button",
        details=f"Role: {role_name}"
    )

@router.message(F.text == "👤 Профиль")
async def profile_button(message: Message):
    # Check if bot is disabled
    is_disabled, maintenance_msg = await check_bot_disabled()
    if is_disabled:
        await message.answer(maintenance_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user = message.from_user
    user_id = user.id
    username = user.username or "Не указан"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip() or "Не указано"
    
    join_date = user_join_dates.get(user_id, datetime.now())
    join_date_str = join_date.strftime("%d.%m.%Y")
    
    # Check if user has premium or active subscription
    is_premium = await database.is_user_premium(user_id)
    subscription = await database.get_active_subscription(user_id)
    has_premium_access = bool(is_premium or subscription)
    
    profile_text = (
        "👤 *Ваш профиль*\n\n"
        f"🆔 *ID:* `{escape_markdown(str(user_id))}`\n"
        f"👨‍💼 *Имя:* {escape_markdown(full_name)}\n"
        f"📱 *Username:* @{escape_markdown(username)}\n"
        f"📅 *Дата регистрации:* {escape_markdown(join_date_str)}\n"
    )
    
    if has_premium_access:
        expiry_date = await get_premium_expiry_date(user_id)
        if expiry_date:
            profile_text += f"💎 *Премиум активен до:* {escape_markdown(expiry_date.strftime('%d.%m.%Y %H:%M'))}\n"
        else:
            profile_text += "💎 *Премиум активен*\n"
    
    # Check for vehicle binding
    binding = await database.get_vehicle_binding(user_id)
    if binding:
        plate = binding['plate_number']
        expires = datetime.fromisoformat(binding['subscription_expires_at'])
        profile_text += (
            f"\n🚗 *Привязанное авто:* `{escape_markdown(plate)}`\n"
            f"📅 *Активна до:* {escape_markdown(expires.strftime('%d.%m.%Y'))}\n"
        )
    else:
        profile_text += "\n🚗 *Привязанное авто:* Нет\n"
    
    profile_text += "\n_Спасибо, что пользуетесь нашим ботом\\!_"
    
    # Add binding buttons for all users to promote the feature
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Привязать машину", callback_data="profile:bind_vehicle")
    builder.button(text="❓ Что такое привязка авто?", callback_data="profile:binding_info")
    
    # Add unbind button if vehicle is bound and user has premium access
    if binding and has_premium_access:
        builder.button(text="🗑 Отвязать машину", callback_data="profile:unbind_vehicle")
    
    builder.adjust(1)
    await message.answer(profile_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)

@router.callback_query(F.data.startswith("profile:"))
async def handle_profile_callbacks(callback: CallbackQuery, state: FSMContext):
    """Handle profile-related callbacks"""
    action = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    if action == "bind_vehicle":
        # Check if user has premium OR active subscription
        is_premium = await database.is_user_premium(user_id)
        subscription = await database.get_active_subscription(user_id)
        
        if not (is_premium or subscription):
            await callback.answer("💎 Эта функция доступна только для Premium-пользователей", show_alert=True)
            return
        
        # Check for existing binding
        existing_binding = await database.get_vehicle_binding(user_id)
        
        if existing_binding:
            plate = existing_binding['plate_number']
            expires = datetime.fromisoformat(existing_binding['subscription_expires_at'])
            
            await callback.message.answer(
                f"🚗 *У вас уже есть привязанная машина:*\n\n"
                f"Номер: `{escape_markdown(plate)}`\n"
                f"Активна до: {escape_markdown(expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
                "Хотите привязать другую машину\\? Отправьте новый номер автомобиля\\.\n\n"
                "_Внимание: новая привязка заменит текущую\\._",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await callback.message.answer(
                "🔗 *Привязка автомобиля*\n\n"
                "📋 Введите номер автомобиля в формате:\n"
                "• 0000AA01 или 000AA01\n\n"
                "Поддерживаются все зарегистрированные типы номеров\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        await state.set_state(UserStates.waiting_for_binding_plate)
        await callback.answer()
    
    elif action == "binding_info":
        info_text = (
            "❓ *Что такое привязка авто\\?*\n\n"
            "🔗 *Привязка автомобиля* \\- это премиум\\-функция для автоматического мониторинга штрафов\\.\n\n"
            "✅ *При привязке авто:*\n"
            "• Сохраняются все текущие ордера штрафов\n"
            "• Уведомления НЕ отправляются \\(это старые штрафы\\)\n"
            "• Показывается сводка: _\"Найдено штрафов: 3 шт\\. на сумму 675 смн\"_\n\n"
            "✅ *При мониторинге:*\n"
            "• Сравниваются новые ордера с сохраненными\n"
            "• Уведомления отправляются ТОЛЬКО о новых штрафах\n"
            "• Список отслеживаемых ордеров обновляется\n\n"
            "✅ *Если штраф оплачен:*\n"
            "• Удаляется из списка отслеживаемых\n"
            "• Уведомление НЕ отправляется\n\n"
            "💡 *Пример работы:*\n"
            "1\\. Привязка: \\[\"0003873679\", \"0003873680\"\\] → БЕЗ уведомлений\n"
            "2\\. Мониторинг: \\[\"0003873679\", \"0003873680\", \"0003873700\"\\] → Уведомление только про \"0003873700\" ✅\n"
            "3\\. Оплата: \\[\"0003873680\", \"0003873700\"\\] → БЕЗ уведомлений\n\n"
            "⚠️ *Важно:* Можно привязать только один автомобиль\\. "
            "Привязка действует в течение срока премиум\\-подписки\\.\n\n"
            "_💎 Функция доступна только для премиум\\-пользователей\\._"
        )
        await callback.message.answer(info_text, parse_mode=ParseMode.MARKDOWN_V2)
        await callback.answer()
    
    elif action == "unbind_vehicle":
        # Check if user has premium OR active subscription
        is_premium = await database.is_user_premium(user_id)
        subscription = await database.get_active_subscription(user_id)
        
        if not (is_premium or subscription):
            await callback.answer("💎 Эта функция доступна только для Premium-пользователей", show_alert=True)
            return
        
        # Check for existing binding
        binding = await database.get_vehicle_binding(user_id)
        
        if not binding:
            await callback.answer("У вас нет привязанного автомобиля", show_alert=True)
            return
        
        # Remove binding
        await database.remove_vehicle_binding(user_id)
        
        await callback.message.edit_text(
            "✅ *Автомобиль успешно отвязан\\!*\n\n"
            "Вы больше не будете получать автоматические уведомления о штрафах\\.\n\n"
            "_Вы можете привязать автомобиль снова в любое время через профиль\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await callback.answer("Автомобиль отвязан")

@router.message(F.text == "💎 Подписка")
async def subscription_button(message: Message):
    # Check if bot is disabled
    is_disabled, maintenance_msg = await check_bot_disabled()
    if is_disabled:
        await message.answer(maintenance_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user_id = message.from_user.id
    
    # Get current mode
    current_mode = BotMode.NORMAL
    if mode_service:
        current_mode = await mode_service.get_mode()
    
    # Check if in test mode - show free mode message
    if current_mode == BotMode.TEST:
        test_mode_text = (
            "💎 *Подписки*\n\n"
            "🎉 Хорошие новости\\! Бот полностью бесплатный на данный момент\\.\n\n"
            "Все функции доступны без ограничений\\.\n\n"
            "⚡️ В будущем могут появиться премиум\\-функции, "
            "но базовая проверка штрафов всегда останется бесплатной\\!"
        )
        await message.answer(test_mode_text, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    # Check if user has active subscription
    subscription = await database.get_active_subscription(user_id)
    
    if subscription:
        # User has active subscription - show expiry info
        expiry_datetime = datetime.fromisoformat(subscription["expires_at"])
        expiry_str = subscription_service.format_expiry_date(expiry_datetime)
        
        subscription_text = (
            "💎 *Поздравляем, вы обладатель Intellex Premium\\!*\n\n"
            f"Ваша подписка активна до: *{escape_markdown(expiry_str)}*\n\n"
            "🎁 *Ваши преимущества:*\n"
            "• Увеличенный дневной лимит запросов \\(100 в день\\)\n"
            "• Возможность привязки автомобиля\n"
            "• Автоматические уведомления о новых штрафах\n\n"
            "_Спасибо, что пользуетесь нашим сервисом\\!_"
        )
        
        await message.answer(subscription_text, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        # No active subscription - show purchase options
        await show_subscription_plans(message)

@router.message(F.text == "Техподдержка")
async def tech_support_button(message: Message):
    """Handler for tech support button"""
    support_text = "Если у вас возникли вопросы напишите нам: @intellex_support"
    await message.answer(support_text)

async def show_subscription_plans(message: Message):
    """Show available subscription plans with prices"""
    subscription_text = (
        "💎 *Intellex Premium*\n\n"
        "У вас нет подписки или она неактивна\\.\n\n"
        "*Получите доступ к премиум\\-функциям:*\n"
        "• 🔄 Увеличенный дневной лимит запросов \\(100 в день\\)\n"
        "• 🔗 Привязка автомобиля для мониторинга\n"
        "• 🔔 Автоматические уведомления о новых штрафах\n\n"
        "*Выберите тарифный план:*"
    )
    
    # Get current discount
    current_mode = BotMode.NORMAL
    if mode_service:
        current_mode = await mode_service.get_mode()
    
    discount_pct = subscription_service.get_discount_percentage(current_mode)
    
    if discount_pct > 0:
        subscription_text += f"\n\n🎁 *Действует скидка {discount_pct}%\\!*"
    
    await message.answer(subscription_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    # Show plan buttons
    builder = InlineKeyboardBuilder()
    
    for plan_id in subscription_service.get_all_plans():
        plan_name = subscription_service.get_plan_name(plan_id)
        price, discount = await subscription_service.get_plan_price(plan_id, mode_service)
        
        base_price = subscription_service.BASE_PRICES[plan_id]
        
        if discount > 0:
            button_text = f"{plan_name} - {price} смн (было {base_price})"
        else:
            button_text = f"{plan_name} - {price} смн"
        
        builder.button(text=button_text, callback_data=f"subscription:select:{plan_id}")
    
    builder.adjust(1)
    await message.answer("Выберите план:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("subscription:"))
async def handle_subscription_callback(callback: CallbackQuery):
    """Handle subscription-related callbacks"""
    parts = callback.data.split(":")
    action = parts[1]
    
    user_id = callback.from_user.id
    
    if action == "select":
        # User selected a plan
        plan_id = parts[2]
        await handle_plan_selection(callback, user_id, plan_id)
    
    elif action == "extend":
        # User wants to extend subscription
        await callback.message.edit_text(
            "💎 *Продление подписки*\n\n"
            "Выберите план для продления:",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Show plan buttons
        builder = InlineKeyboardBuilder()
        
        for plan_id in subscription_service.get_all_plans():
            plan_name = subscription_service.get_plan_name(plan_id)
            price, discount = await subscription_service.get_plan_price(plan_id, mode_service)
            
            base_price = subscription_service.BASE_PRICES[plan_id]
            
            if discount > 0:
                button_text = f"{plan_name} - {price} смн (было {base_price})"
            else:
                button_text = f"{plan_name} - {price} смн"
            
            builder.button(text=button_text, callback_data=f"subscription:select:{plan_id}")
        
        builder.adjust(1)
        await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    
    elif action == "paid":
        # User clicked "Я оплатил"
        request_id = int(parts[2])
        await handle_payment_confirmation(callback, user_id, request_id)
    
    elif action == "cancel":
        # User cancelled payment
        request_id = int(parts[2])
        await database.update_payment_request_status(request_id, "cancelled")
        
        await callback.message.edit_text(
            "❌ *Оплата отменена*\n\n"
            "Вы можете оформить подписку в любое время через меню 💎 Подписка\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    await callback.answer()

async def handle_plan_selection(callback: CallbackQuery, user_id: int, plan_id: str):
    """Handle plan selection and create payment request"""
    # Get plan details
    plan_name = subscription_service.get_plan_name(plan_id)
    price, discount_pct = await subscription_service.get_plan_price(plan_id, mode_service)
    base_price = subscription_service.BASE_PRICES[plan_id]
    
    # Generate payment URL
    payment_url = subscription_service.generate_subscription_payment_url(price, plan_id, user_id)
    
    # Create payment request
    payment_metadata = {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "base_price": base_price,
        "discount_percentage": discount_pct,
        "final_price": price
    }
    
    request_id = await database.create_payment_request(
        user_id=user_id,
        payment_type="subscription",
        amount=str(price),
        payment_url=payment_url,
        payment_metadata=payment_metadata
    )
    
    # Send payment confirmation message
    payment_text = (
        f"💎 *{escape_markdown(plan_name)}*\n\n"
        f"💰 *Сумма к оплате:* {escape_markdown(str(price))} смн"
    )
    
    if discount_pct > 0:
        payment_text += f"\n🎁 *Скидка:* {discount_pct}% \\(экономия: {escape_markdown(str(base_price - price))} смн\\)"
    
    payment_text += (
        "\n\n📋 *Инструкция:*\n"
        "1\\. Нажмите кнопку \"Перейти к оплате\"\n"
        "2\\. Оплатите через платежную систему\n"
        "3\\. После оплаты нажмите \"Я оплатил\"\n\n"
        "_Ваша заявка будет проверена администратором\\._"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", url=payment_url)
    builder.button(text="✅ Я оплатил", callback_data=f"subscription:paid:{request_id}")
    builder.button(text="❌ Отменить", callback_data=f"subscription:cancel:{request_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(
        payment_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_payment_confirmation(callback: CallbackQuery, user_id: int, request_id: int):
    """Handle user payment confirmation"""
    # Update request status to awaiting approval
    await database.update_payment_request_status(request_id, "awaiting_approval")
    
    # Get request details
    request = await database.get_payment_request(request_id)
    
    if not request:
        await callback.message.edit_text(
            "❌ *Ошибка*\n\nЗаявка не найдена\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Notify user
    await callback.message.edit_text(
        "✅ *Заявка на проверку отправлена*\n\n"
        "Ваша оплата будет проверена администратором в ближайшее время\\.\n"
        "Вы получите уведомление о результате\\.\n\n"
        "_Обычно проверка занимает несколько минут\\._",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    # Notify log group if configured
    log_group_id = os.getenv("LOG_GROUP_ID")
    if log_group_id:
        try:
            log_group_id = int(log_group_id)
            
            # Get user info
            user = await database.get_user(user_id)
            username = user.get("username", "N/A") if user else "N/A"
            first_name = user.get("first_name", "") if user else ""
            last_name = user.get("last_name", "") if user else ""
            full_name = f"{first_name} {last_name}".strip() or "N/A"
            
            # Get plan details
            metadata = request["payment_metadata"] or {}
            plan_name = metadata.get("plan_name", "Unknown")
            amount = request["amount"]
            discount_pct = metadata.get("discount_percentage", 0)
            
            log_text = (
                "💰 *Новая заявка на оплату подписки*\n\n"
                f"👤 *Пользователь:*\n"
                f"• ID: `{escape_markdown(str(user_id))}`\n"
                f"• Username: @{escape_markdown(username)}\n"
                f"• Имя: {escape_markdown(full_name)}\n\n"
                f"💎 *План:* {escape_markdown(plan_name)}\n"
                f"💰 *Сумма:* {escape_markdown(amount)} смн"
            )
            
            if discount_pct > 0:
                log_text += f"\n🎁 *Скидка:* {escape_markdown(str(discount_pct))}%"
            
            log_text += f"\n\n🆔 *ID заявки:* `{escape_markdown(str(request_id))}`"
            
            # Add admin action buttons
            builder = InlineKeyboardBuilder()
            builder.button(text="✅ Одобрить", callback_data=f"payment:approve:{request_id}")
            builder.button(text="❌ Отклонить", callback_data=f"payment:reject:{request_id}")
            builder.adjust(2)
            
            bot = callback.bot
            await bot.send_message(
                chat_id=log_group_id,
                text=log_text,
                reply_markup=builder.as_markup(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error sending log group notification: {e}")

@router.callback_query(F.data.startswith("payment:"))
async def handle_payment_admin_actions(callback: CallbackQuery):
    """Handle payment approval/rejection from admin"""
    user_id = callback.from_user.id
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    parts = callback.data.split(":")
    action = parts[1]  # approve or reject
    request_id = int(parts[2])
    
    # Get payment request
    request = await database.get_payment_request(request_id)
    
    if not request:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    
    target_user_id = request["user_id"]
    amount = request["amount"]
    metadata = request["payment_metadata"] or {}
    plan_id = metadata.get("plan_id")
    plan_name = metadata.get("plan_name", "Unknown")
    
    if action == "approve":
        # Approve payment and grant premium
        await database.update_payment_request_status(request_id, "approved")
        
        # Grant premium subscription
        if plan_id:
            duration_days = subscription_service.get_plan_duration_days(plan_id)
            
            # Check if user already has active subscription
            current_subscription = await database.get_active_subscription(target_user_id)
            if current_subscription:
                current_expiry = datetime.fromisoformat(current_subscription["expires_at"])
            else:
                current_expiry = None
            
            # Calculate new expiry
            new_expiry = subscription_service.calculate_new_expiry(current_expiry, plan_id)
            
            # Update user premium status
            await database.update_user_premium(target_user_id, True, new_expiry)
            
            # Update subscription record
            await database.create_or_update_subscription(
                user_id=target_user_id,
                plan_id=plan_id,
                expires_at=new_expiry
            )
        
        # Update the message in log group
        original_text = escape_markdown(callback.message.text)
        admin_identifier = escape_markdown(callback.from_user.username or callback.from_user.first_name or "Unknown")
        await callback.message.edit_text(
            f"{original_text}\n\n"
            f"✅ *Одобрено администратором*\n"
            f"👤 Админ: @{admin_identifier}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Notify user
        bot = callback.bot
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    "✅ *Оплата подтверждена\\!*\n\n"
                    f"Ваша подписка *{escape_markdown(plan_name)}* успешно активирована\\.\n\n"
                    "Теперь вы можете:\n"
                    "• Делать до 100 запросов в день\n"
                    "• Привязать автомобиль для мониторинга\n"
                    "• Получать автоматические уведомления о штрафах\n\n"
                    "_Спасибо за покупку\\!_"
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error notifying user {target_user_id}: {e}")
        
        await callback.answer("✅ Оплата одобрена, премиум активирован")
    
    elif action == "reject":
        # Reject payment
        await database.update_payment_request_status(request_id, "rejected")
        
        # Update the message in log group
        original_text = escape_markdown(callback.message.text)
        admin_identifier = escape_markdown(callback.from_user.username or callback.from_user.first_name or "Unknown")
        await callback.message.edit_text(
            f"{original_text}\n\n"
            f"❌ *Отклонено администратором*\n"
            f"👤 Админ: @{admin_identifier}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Notify user
        bot = callback.bot
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=(
                    "❌ *Оплата не подтверждена*\n\n"
                    "К сожалению, ваша оплата не была найдена в системе\\.\n\n"
                    "Пожалуйста, проверьте:\n"
                    "• Правильность суммы оплаты\n"
                    "• Статус платежа в вашем банке\n\n"
                    "Если вы уверены, что оплата прошла, обратитесь в поддержку\\."
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error notifying user {target_user_id}: {e}")
        
        await callback.answer("❌ Оплата отклонена")

@router.message(UserStates.waiting_for_plate)
async def handle_plate_number(message: Message, state: FSMContext):
    # Check if bot is disabled
    is_disabled, maintenance_msg = await check_bot_disabled()
    if is_disabled:
        await message.answer(maintenance_msg, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user_id = message.from_user.id
    plate_number = message.text.strip()
    
    # Check if this is a valid plate number format
    if not validate_plate_number(plate_number):
        await message.answer(
            "ℹ️ Нарушений не найдено или вы неправильно ввели номер\\.\n\n"
            "Пожалуйста, проверьте правильность введенного номера\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        return
    
    # Auto-detach expired bindings on any user interaction
    await database.remove_expired_bindings()
    
    # Check user access and quotas
    can_access, error_message = await check_user_access(user_id)
    if not can_access:
        await message.answer(error_message, parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    # Increment usage counter (but not in test mode)
    if mode_service:
        current_mode = await mode_service.get_mode()
        if current_mode != BotMode.TEST:
            await database.increment_daily_usage(user_id)
    else:
        await database.increment_daily_usage(user_id)
    
    wait_message = await message.answer(
        "🔍 *Поиск информации\\.\\.\\.*\n\n"
        "_Это может занять несколько секунд\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    search_result = await asyncio.to_thread(scraper.search_fines_by_plate, plate_number)
    
    await wait_message.delete()

    if search_result.get("error"):
        await message.answer(
            "ℹ️ Нарушений не найдено или вы неправильно ввели номер\\.\n\n"
            "Пожалуйста, проверьте правильность введенного номера\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        return
    
    await state.clear()
    user_fines_cache[user_id] = search_result
    user_pagination_state[user_id] = 0
    
    vehicle_info = search_result.get("vehicle_info", {})
    pay_all_data = search_result.get("pay_all_data")
    
    if vehicle_info:
        info_text = format_vehicle_info(vehicle_info)
        
        # Add "Pay All" button if data is available
        if pay_all_data and pay_all_data.get('summa'):
            builder = InlineKeyboardBuilder()
            summa = pay_all_data['summa']
            plate = pay_all_data.get('plate', vehicle_info.get('plate', ''))
            pay_all_url = f"https://pay.dc.tj/pay.php?a={plate}&s={summa}&c=&f1=346&f2=#kortiMilli"
            builder.button(text=f"💳 ОПЛАТИТЬ ВСЕ ШТРАФЫ ({summa} смн)", url=pay_all_url)
            await message.answer(info_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await message.answer(info_text, parse_mode=ParseMode.MARKDOWN_V2)

    fines = search_result.get("fines", [])
    if not fines:
        await message.answer(
            "✅ *Отличные новости\\!*\n\n"
            "По данному автомобилю штрафы не найдены\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Send first page of fines directly without summary message
    await send_fines_page(message, user_id, 0)

@router.callback_query(F.data.startswith("page:"))
async def handle_page_navigation(callback: CallbackQuery):
    _, page_str = callback.data.split(":", 1)
    
    if page_str == "info":
        await callback.answer()
        return
    
    page = int(page_str)
    user_id = callback.from_user.id
    
    cached_data = user_fines_cache.get(user_id)
    if not cached_data:
        await callback.answer("Данные устарели, выполните поиск заново.", show_alert=True)
        return
    
    fines = cached_data.get("fines", [])
    FINES_PER_PAGE = 5
    total_pages = (len(fines) + FINES_PER_PAGE - 1) // FINES_PER_PAGE
    
    user_pagination_state[user_id] = page
    await callback.answer(f"Переход на страницу {page + 1}")
    
    # Delete old messages from previous page
    old_message_ids = user_fine_message_ids.get(user_id, [])
    bot = callback.bot
    for msg_id in old_message_ids:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=msg_id)
        except Exception as e:
            logger.debug(f"Failed to delete message {msg_id}: {e}")
    
    # Clear the old message IDs
    user_fine_message_ids[user_id] = []
    
    # Send new page of fines
    start_idx = page * FINES_PER_PAGE
    end_idx = min(start_idx + FINES_PER_PAGE, len(fines))
    page_fines = fines[start_idx:end_idx]
    
    # Store new message IDs
    message_ids = []
    
    for i, fine in enumerate(page_fines):
        global_idx = start_idx + i
        builder = InlineKeyboardBuilder()
        media_buttons = []
        for media_key in ["фото_1", "фото_2", "доп_фото", "видео"]:
            if media_key in fine.get("media_links", {}):
                emoji = "📷" if "фото" in media_key else "🎥"
                media_name = media_key.replace('_', ' ').title()
                media_buttons.append(
                    InlineKeyboardButton(text=f"{emoji} {media_name}", callback_data=f"media:{global_idx}:{media_key}")
                )
        
        if media_buttons:
            builder.row(*media_buttons)
        
        amount_numeric = re.sub(r'[^0-9]', '', fine['amount'])
        payment_url = f"https://pay.dc.tj/pay.php?a={fine['order']}&s={amount_numeric}&c=&f1=346&f2=#kortiMilli"
        builder.button(text=f"💳 Оплатить {fine.get('amount', '')}", url=payment_url)
        builder.adjust(min(len(media_buttons), 2), 1)

        fine_text = (
            f"*\\#{escape_markdown(str(global_idx + 1))}* 📋 *Штраф*\n\n"
            f"📄 *Ордер:* `{escape_markdown(fine.get('order', 'N/A'))}`\n"
            f"📅 *Дата:* {escape_markdown(fine.get('date', 'N/A'))}\n"
            f"⚠️ *Нарушение:* _{escape_markdown(fine.get('violation', 'N/A'))}_\n"
            f"💰 *Сумма:* *{escape_markdown(fine.get('amount', 'N/A'))}*"
        )
        msg = await callback.message.answer(fine_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)
        message_ids.append(msg.message_id)
        await asyncio.sleep(0.1)
    
    # Send pagination controls
    pagination_text = f"📄 *Страница {page + 1} из {total_pages}*"
    pagination_msg = await callback.message.answer(
        pagination_text,
        reply_markup=get_pagination_keyboard(page, total_pages, user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    message_ids.append(pagination_msg.message_id)
    
    # Store all message IDs for this page
    user_fine_message_ids[user_id] = message_ids

@router.message(F.text == "🔗 Привязать машину")
async def bind_vehicle_button(message: Message, state: FSMContext):
    """Handler for 'Привязать машину' button - premium only"""
    user_id = message.from_user.id
    
    # Check if user has premium OR active subscription
    is_premium = await database.is_user_premium(user_id)
    subscription = await database.get_active_subscription(user_id)
    
    if not (is_premium or subscription):
        await message.answer(
            "💎 *Эта функция доступна только для премиум\\-пользователей\\!*\n\n"
            "Привязка автомобиля позволяет получать автоматические уведомления о новых штрафах\\.\n\n"
            "_Приобретите премиум\\-подписку для доступа к этой функции\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Check for existing binding
    existing_binding = await database.get_vehicle_binding(user_id)
    
    if existing_binding:
        plate = existing_binding['plate_number']
        expires = datetime.fromisoformat(existing_binding['subscription_expires_at'])
        
        await message.answer(
            f"🚗 *У вас уже есть привязанная машина:*\n\n"
            f"Номер: `{escape_markdown(plate)}`\n"
            f"Активна до: {escape_markdown(expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
            "Хотите привязать другую машину\\? Отправьте новый номер автомобиля\\.\n\n"
            "_Внимание: новая привязка заменит текущую\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(UserStates.waiting_for_binding_plate)
    else:
        await message.answer(
            "🔗 *Привязка автомобиля*\n\n"
            "📋 Введите номер автомобиля в формате:\n"
            "• 0000AA01 или 000AA01\n\n"
            "Поддерживаются все зарегистрированные типы номеров\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(UserStates.waiting_for_binding_plate)

@router.message(F.text == "❓ Что такое привязка машины?")
async def vehicle_binding_info(message: Message):
    """Handler for 'Что такое привязка машины?' button"""
    info_text = (
            "❓ *Что такое привязка машины\\?*\n\n"
            "🔗 *Привязка автомобиля* \\- это премиум\\-функция, которая позволяет:\n\n"
            "✅ Автоматически отслеживать новые штрафы на вашем автомобиле\n"
            "✅ Получать мгновенные уведомления при появлении новых штрафов\n"
            "✅ Не проверять штрафы вручную каждый день\n"
            "✅ Быть в курсе всех нарушений и вовремя их оплачивать\n\n"
            "💡 *Как это работает:*\n"
            "1\\. Вы привязываете номер своего автомобиля\n"
            "2\\. Бот автоматически проверяет штрафы каждые 30 минут\n"
            "3\\. При обнаружении новых штрафов вы сразу получаете уведомление\n\n"
            "⚠️ *Важно:* Одновременно можно привязать только один автомобиль\\. "
            "Привязка действует в течение срока вашей премиум\\-подписки\\.\n\n"
            "_💎 Функция доступна только для премиум\\-пользователей\\._"
        )
    await message.answer(info_text, parse_mode=ParseMode.MARKDOWN_V2)

@router.message(UserStates.waiting_for_binding_plate)
async def process_binding_plate(message: Message, state: FSMContext):
    """Process plate number for vehicle binding"""
    user_id = message.from_user.id
    plate_number = message.text.strip().upper()
    
    # Validate plate
    if not validate_plate_number(plate_number):
        await message.answer(
            "ℹ️ Нарушений не найдено или вы неправильно ввели номер\\.\n\n"
            "Пожалуйста, проверьте правильность введенного номера\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Check if user has premium OR active subscription
    is_premium = await database.is_user_premium(user_id)
    subscription = await database.get_active_subscription(user_id)
    
    if not (is_premium or subscription):
        await message.answer(
            "💎 *Ваша премиум\\-подписка истекла\\.*\n\n"
            "_Пожалуйста, продлите подписку для использования этой функции\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        return
    
    # Get user's premium expiration date (from subscription or user field)
    premium_expires = await get_premium_expiry_date(user_id)
    
    if not premium_expires:
        await message.answer(
            "💎 *Ошибка получения данных подписки\\.*\n\n"
            "_Пожалуйста, обратитесь в поддержку\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        return
    
    # Check for existing binding
    existing_binding = await database.get_vehicle_binding(user_id)
    
    if existing_binding:
        old_plate = existing_binding['plate_number']
        
        # Ask for confirmation
        await state.update_data(new_plate=plate_number, expires_at=premium_expires)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, заменить", callback_data="confirm_binding")
        builder.button(text="❌ Отмена", callback_data="cancel_binding")
        builder.adjust(2)
        
        await message.answer(
            f"⚠️ *Подтверждение замены*\n\n"
            f"Текущая привязка: `{escape_markdown(old_plate)}`\n"
            f"Новая привязка: `{escape_markdown(plate_number)}`\n\n"
            "Вы уверены\\, что хотите заменить привязку\\?",
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(UserStates.waiting_for_binding_confirmation)
    else:
        # Set new binding directly
        binding_id = await database.set_vehicle_binding(user_id, plate_number, premium_expires)
        
        # Fetch current fines to initialize tracked orders
        wait_message = await message.answer(
            "🔍 *Загрузка данных об автомобиле\\.\\.\\.*\n\n"
            "_Это займет несколько секунд\\.\\.\\._",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        try:
            search_result = await asyncio.to_thread(scraper.search_fines_by_plate, plate_number)
            await wait_message.delete()
            
            if search_result.get("error"):
                # If we can't fetch fines, still bind but without initializing tracked orders
                await message.answer(
                    f"✅ *Автомобиль успешно привязан\\!*\n\n"
                    f"Номер: `{escape_markdown(plate_number)}`\n"
                    f"Активна до: {escape_markdown(premium_expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
                    "⚠️ Не удалось загрузить текущие штрафы\\. Мониторинг начнется при следующей проверке\\.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                fines = search_result.get("fines", [])
                
                # Extract order numbers and initialize tracked orders
                order_numbers = [fine.get('order') for fine in fines if fine.get('order')]
                await database.update_tracked_orders(binding_id, order_numbers)
                
                # Calculate total amount
                total_amount = 0
                for fine in fines:
                    amount_str = fine.get('amount', '0')
                    # Extract numeric value from string like "150 смн"
                    amount_numeric = re.sub(r'[^0-9]', '', amount_str)
                    if amount_numeric:
                        total_amount += int(amount_numeric)
                
                # Show summary message
                await message.answer(
                    f"✅ *Автомобиль успешно привязан\\!*\n\n"
                    f"🚗 *Номер:* `{escape_markdown(plate_number)}`\n"
                    f"⏰ *Активна до:* {escape_markdown(premium_expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
                    f"📊 *Найдено штрафов:* {len(fines)} шт\\. на сумму {escape_markdown(str(total_amount))} смн\n\n"
                    "🔔 *С этого момента вы будете получать уведомления о новых штрафах\\.*",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error fetching fines during binding: {e}")
            try:
                await wait_message.delete()
            except:
                pass
            await message.answer(
                f"✅ *Автомобиль успешно привязан\\!*\n\n"
                f"Номер: `{escape_markdown(plate_number)}`\n"
                f"Активна до: {escape_markdown(premium_expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
                "⚠️ Не удалось загрузить текущие штрафы\\. Мониторинг начнется при следующей проверке\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
        await state.clear()

@router.callback_query(F.data == "confirm_binding")
async def confirm_binding_replacement(callback: CallbackQuery, state: FSMContext):
    """Confirm vehicle binding replacement"""
    user_id = callback.from_user.id
    data = await state.get_data()
    
    new_plate = data.get('new_plate')
    expires_at = data.get('expires_at')
    
    if not new_plate or not expires_at:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        await state.clear()
        return
    
    # Replace binding
    binding_id = await database.set_vehicle_binding(user_id, new_plate, expires_at)
    
    # Fetch current fines to initialize tracked orders
    try:
        search_result = await asyncio.to_thread(scraper.search_fines_by_plate, new_plate)
        
        if not search_result.get("error"):
            fines = search_result.get("fines", [])
            order_numbers = [fine.get('order') for fine in fines if fine.get('order')]
            await database.update_tracked_orders(binding_id, order_numbers)
            
            # Calculate total amount
            total_amount = 0
            for fine in fines:
                amount_str = fine.get('amount', '0')
                amount_numeric = re.sub(r'[^0-9]', '', amount_str)
                if amount_numeric:
                    total_amount += int(amount_numeric)
            
            await callback.message.edit_text(
                f"✅ *Привязка успешно заменена\\!*\n\n"
                f"🚗 *Новый номер:* `{escape_markdown(new_plate)}`\n"
                f"⏰ *Активна до:* {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\n\n"
                f"📊 *Найдено штрафов:* {len(fines)} шт\\. на сумму {escape_markdown(str(total_amount))} смн\n\n"
                "🔔 *Теперь вы будете получать уведомления о штрафах на новый автомобиль\\.*",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await callback.message.edit_text(
                f"✅ *Привязка успешно заменена\\!*\n\n"
                f"Новый номер: `{escape_markdown(new_plate)}`\n"
                f"Активна до: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\n\n"
                "⚠️ Не удалось загрузить текущие штрафы\\. Мониторинг начнется при следующей проверке\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Error fetching fines during binding replacement: {e}")
        await callback.message.edit_text(
            f"✅ *Привязка успешно заменена\\!*\n\n"
            f"Новый номер: `{escape_markdown(new_plate)}`\n"
            f"Активна до: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\n\n"
            "Теперь вы будете получать уведомления о штрафах на новый автомобиль\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    await callback.answer("Привязка обновлена!")
    await state.clear()

@router.callback_query(F.data == "cancel_binding")
async def cancel_binding_replacement(callback: CallbackQuery, state: FSMContext):
    """Cancel vehicle binding replacement"""
    await callback.message.edit_text(
        "❌ *Замена отменена*\n\n"
        "Ваша текущая привязка осталась без изменений\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await callback.answer("Отменено")
    await state.clear()

@router.message(F.text.startswith("/bind_"))
async def bind_plate(message: Message):
    """
    Legacy admin command to bind a plate to premium subscription
    Usage: /bind_PLATE_NUMBER_DAYS
    Example: /bind_01ABC123_30 (binds plate 01ABC123 for 30 days)
    This also grants premium status to the user
    """
    try:
        parts = message.text[6:].split('_')  # Remove /bind_ prefix
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Неверный формат команды*\n\n"
                "Использование: `/bind_НОМЕР_ДНИ`\n"
                "Пример: `/bind_01ABC123_30`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        plate_number = '_'.join(parts[:-1])  # All parts except last are plate
        days = int(parts[-1])  # Last part is days
        
        if not validate_plate_number(plate_number):
            await message.answer("⚠️ Некорректный номер автомобиля\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        if days < 1 or days > 365:
            await message.answer("⚠️ Количество дней должно быть от 1 до 365\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        
        user_id = message.from_user.id
        user = message.from_user
        expires_at = datetime.now() + timedelta(days=days)
        
        # Create or get user
        await database.get_or_create_user(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        # Grant premium status
        await database.update_user_premium(user_id, True, expires_at)
        
        # Set vehicle binding
        binding_id = await database.set_vehicle_binding(user_id, plate_number, expires_at)
        
        try:
            search_result = await asyncio.to_thread(scraper.search_fines_by_plate, plate_number)
            if search_result.get("error"):
                await message.answer(
                    f"✅ *Успешно\\!*\n\n"
                    f"Премиум\\-подписка активирована\\!\n"
                    f"Номер `{escape_markdown(plate_number.upper())}` привязан к вашему аккаунту\\.\n"
                    f"Подписка активна до: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\\.\n\n"
                    "⚠️ Не удалось загрузить текущие штрафы\\. Мониторинг начнется при следующей проверке\\.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                fines = search_result.get("fines", [])
                order_numbers = [fine.get('order') for fine in fines if fine.get('order')]
                await database.update_tracked_orders(binding_id, order_numbers)
                
                total_amount = 0
                for fine in fines:
                    amount_str = fine.get('amount', '0')
                    amount_numeric = re.sub(r'[^0-9]', '', amount_str)
                    if amount_numeric:
                        total_amount += int(amount_numeric)
                
                await message.answer(
                    f"✅ *Успешно\\!*\n\n"
                    f"Премиум\\-подписка активирована\\!\n"
                    f"Номер `{escape_markdown(plate_number.upper())}` привязан к вашему аккаунту\\.\n"
                    f"Подписка активна до: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\\.\n\n"
                    f"📊 *Найдено штрафов:* {len(fines)} шт\\. на сумму {escape_markdown(str(total_amount))} смн\n\n"
                    f"🔔 *Вы будете получать уведомления о новых штрафах автоматически\\.*",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error fetching fines for /bind command: {e}")
            await message.answer(
                f"✅ *Успешно\\!*\n\n"
                f"Премиум\\-подписка активирована\\!\n"
                f"Номер `{escape_markdown(plate_number.upper())}` привязан к вашему аккаунту\\.\n"
                f"Подписка активна до: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}\\.\n\n"
                "⚠️ Не удалось загрузить текущие штрафы\\. Мониторинг начнется при следующей проверке\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        
    except ValueError:
        await message.answer(
            "⚠️ *Неверный формат команды*\n\n"
            "Количество дней должно быть числом\\.\n"
            "Пример: `/bind_01ABC123_30`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Error binding plate: {e}")
        await message.answer(f"❌ Ошибка при привязке номера: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(F.text == "/my_bindings")
async def show_bindings(message: Message):
    """Show user's active premium bindings"""
    try:
        user_id = message.from_user.id
        binding = await database.get_vehicle_binding(user_id)
        
        if not binding:
            is_premium = await database.is_user_premium(user_id)
            if is_premium:
                await message.answer(
                    "📋 *Ваши привязки*\n\n"
                    "У вас нет активных привязок\\.\n\n"
                    "Используйте кнопку *🔗 Привязать машину* для привязки номера\\.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await message.answer(
                    "📋 *Ваши привязки*\n\n"
                    "У вас нет активных привязок\\.\n\n"
                    "💎 Привязка доступна только для премиум\\-пользователей\\.\n"
                    "Используйте `/bind_НОМЕР_ДНИ` для активации премиум и привязки номера\\.",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            return
        
        plate = binding['plate_number']
        expires = datetime.fromisoformat(binding['subscription_expires_at'])
        text = (
            "📋 *Ваша активная привязка:*\n\n"
            f"🚗 *Номер:* `{escape_markdown(plate)}`\n"
            f"⏰ *Активна до:* {escape_markdown(expires.strftime('%d.%m.%Y %H:%M'))}\n\n"
            "Вы получаете автоматические уведомления о новых штрафах\\."
        )
        
        await message.answer(text, parse_mode=ParseMode.MARKDOWN_V2)
        
    except Exception as e:
        logger.error(f"Error showing bindings: {e}")
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(Command("admin_search_plate"))
async def admin_search_plate_command(message: Message, state: FSMContext):
    """Admin command to search for plate without entering waiting state
    Usage: /admin_search_plate PLATE_NUMBER
    Example: /admin_search_plate 01ABC123
    """
    from admin_roles import get_user_role, AdminRole
    
    user_id = message.from_user.id
    user_role = get_user_role(user_id)
    
    # Check if user has admin role
    if user_role < AdminRole.RND:
        return
    
    # Parse command arguments
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ *Неверный формат*\n\n"
            "Использование: `/admin_search_plate <номер>`\n\n"
            "💡 Пример: `/admin_search_plate 01ABC123`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    plate_number = args[1].strip()
    
    # Validate plate
    if not validate_plate_number(plate_number):
        await message.answer(
            "ℹ️ Нарушений не найдено или вы неправильно ввели номер\\.\n\n"
            "Пожалуйста, проверьте правильность введенного номера\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Clear state to ensure we're not in waiting mode
    await state.clear()
    
    wait_message = await message.answer(
        "🔍 *Поиск информации\\.\\.\\.*\n\n"
        "_Это может занять несколько секунд\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    search_result = await asyncio.to_thread(scraper.search_fines_by_plate, plate_number)
    
    await wait_message.delete()

    if search_result.get("error"):
        await message.answer(
            "ℹ️ Нарушений не найдено или вы неправильно ввели номер\\.\n\n"
            "Пожалуйста, проверьте правильность введенного номера\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    user_fines_cache[user_id] = search_result
    user_pagination_state[user_id] = 0
    
    vehicle_info = search_result.get("vehicle_info", {})
    pay_all_data = search_result.get("pay_all_data")
    
    if vehicle_info:
        info_text = format_vehicle_info(vehicle_info)
        
        # Add "Pay All" button if data is available
        if pay_all_data and pay_all_data.get('summa'):
            builder = InlineKeyboardBuilder()
            summa = pay_all_data['summa']
            plate = pay_all_data.get('plate', vehicle_info.get('plate', ''))
            pay_all_url = f"https://pay.dc.tj/pay.php?a={plate}&s={summa}&c=&f1=346&f2=#kortiMilli"
            builder.button(text=f"💳 ОПЛАТИТЬ ВСЕ ШТРАФЫ ({summa} смн)", url=pay_all_url)
            await message.answer(info_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await message.answer(info_text, parse_mode=ParseMode.MARKDOWN_V2)

    fines = search_result.get("fines", [])
    if not fines:
        await message.answer(
            "✅ *Отличные новости\\!*\n\n"
            "По данному автомобилю штрафы не найдены\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Send first page of fines directly without summary message
    await send_fines_page(message, user_id, 0)

@router.message(Command("grant_premium"))
async def grant_premium_command(message: Message):
    """Admin command to grant premium to a user
    Usage: /grant_premium USER_ID DAYS
    Example: /grant_premium 123456789 30
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer(
                "⚠️ Использование: `/grant_premium USER_ID DAYS`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        target_user_id = int(parts[1])
        days = int(parts[2])
        
        expires_at = datetime.now() + timedelta(days=days)
        
        # Create or get user
        await database.get_or_create_user(target_user_id)
        await database.update_user_premium(target_user_id, True, expires_at)
        
        await message.answer(
            f"✅ Премиум предоставлен пользователю {target_user_id} на {days} дней\\.\n"
            f"Истекает: {escape_markdown(expires_at.strftime('%d.%m.%Y %H:%M'))}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(Command("revoke_premium"))
async def revoke_premium_command(message: Message):
    """Admin command to revoke premium from a user
    Usage: /revoke_premium USER_ID
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer(
                "⚠️ Использование: `/revoke_premium USER_ID`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        target_user_id = int(parts[1])
        await database.update_user_premium(target_user_id, False, None)
        # Also remove vehicle binding
        await database.remove_vehicle_binding(target_user_id)
        
        await message.answer(
            f"✅ Премиум отозван у пользователя {target_user_id}\\.\nПривязка машины удалена\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(Command("block_user"))
async def block_user_command(message: Message):
    """Admin command to block a user
    Usage: /block_user USER_ID
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer(
                "⚠️ Использование: `/block_user USER_ID`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        target_user_id = int(parts[1])
        await database.get_or_create_user(target_user_id)
        await database.block_user(target_user_id)
        
        await message.answer(
            f"✅ Пользователь {target_user_id} заблокирован\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(Command("unblock_user"))
async def unblock_user_command(message: Message):
    """Admin command to unblock a user
    Usage: /unblock_user USER_ID
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer(
                "⚠️ Использование: `/unblock_user USER_ID`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        target_user_id = int(parts[1])
        await database.unblock_user(target_user_id)
        
        await message.answer(
            f"✅ Пользователь {target_user_id} разблокирован\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.message(F.text.regexp(r"^СНЯТЬ ЛИМИТ \d+$"))
async def reset_limit_command(message: Message):
    """Admin command to reset daily usage limit
    Usage: СНЯТЬ ЛИМИТ USER_ID
    Example: СНЯТЬ ЛИМИТ 123456789
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        parts = message.text.split()
        target_user_id = int(parts[2])
        
        await database.reset_daily_usage(target_user_id)
        
        await message.answer(
            f"✅ Лимит сброшен для пользователя {target_user_id}\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError) as e:
        await message.answer(f"❌ Ошибка: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

@router.callback_query(F.data.startswith("media:"))
async def handle_media_request(callback: CallbackQuery):
    _, fine_index_str, media_key = callback.data.split(":", 2)
    fine_index = int(fine_index_str)
    
    user_id = callback.from_user.id
    cached_data = user_fines_cache.get(user_id)
    if not cached_data:
        await callback.answer("Данные устарели, выполните поиск заново.", show_alert=True); return

    fines = cached_data.get("fines", [])
    if fine_index < 0 or fine_index >= len(fines):
        await callback.answer("Штраф не найден. Пожалуйста, выполните поиск заново.", show_alert=True); return
    
    fine = fines[fine_index]
    viewer_link = fine.get("media_links", {}).get(media_key)
    
    if not viewer_link:
        await callback.answer(f"Ссылка на '{media_key.replace('_', ' ').title()}' не найдена.", show_alert=True); return
    
    # Answer callback IMMEDIATELY to prevent timeout errors
    # This must be done BEFORE any long-running operations
    try:
        await callback.answer()
    except Exception:
        pass
    
    # Get optimization setting
    raw_optimization = await database.get_setting("optimization_enabled")
    if raw_optimization is None:
        optimization_enabled = True
    elif isinstance(raw_optimization, bool):
        optimization_enabled = raw_optimization
    else:
        optimization_enabled = str(raw_optimization).lower() == "true"
    
    direct_link = await scraper.get_direct_media_link_async(viewer_link)
    if not direct_link:
        await callback.message.answer(f"Не удалось найти прямую ссылку для скачивания 😕"); return

    # Use optimized download method
    media_results = await scraper.download_media_optimized([direct_link], optimization_enabled)
    media_content = media_results[0] if media_results else None
    
    if not media_content:
        await callback.message.answer("Не удалось скачать файл. 😕"); return

    filename = direct_link.split('/')[-1].split('?')[0] or f"{media_key}_{fine['order']}"
    file = BufferedInputFile(media_content, filename=filename)
    caption = f"Медиа для штрафа `{escape_markdown(fine['order'])}`"
    
    try:
        if any(ext in direct_link.lower() for ext in ['.jpg', '.jpeg', '.png']):
            await callback.message.answer_photo(file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
        elif media_key == "видео" or '.mp4' in direct_link.lower() or 'video.mycar.tj' in direct_link.lower():
            await callback.message.answer_video(file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await callback.message.answer_document(file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
    except TelegramBadRequest as e:
        await callback.message.answer(f"Ошибка при отправке файла: {e}")


async def main():
    global fine_monitor, mode_service
    
    # Initialize database first
    await database.init_db()
    logger.info("Database initialized successfully")
    
    # Initialize bot mode service
    mode_service = BotModeService(database)
    await mode_service.refresh_cache()
    bot_mode_service.bot_mode_service = mode_service  # Set global instance
    logger.info(f"Bot mode service initialized, current mode: {await mode_service.get_mode()}")
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Register middleware to check blocked users
    dp.message.middleware(BlockedUserMiddleware())
    dp.callback_query.middleware(BlockedUserMiddleware())
    
    dp.include_router(router)
    
    # Set up admin panel dependencies
    set_admin_dependencies(database, bot)
    
    # Register admin router
    dp.include_router(admin_router)
    
    # Initialize fine monitor
    fine_monitor = FineMonitor(
        bot=bot,
        scraper=scraper,
        database=database,
        poll_interval=MONITOR_POLL_INTERVAL,
        rate_limit_delay=MONITOR_RATE_LIMIT
    )
    
    # Start monitoring task
    fine_monitor.start()
    
    print("Бот запущен и готов к работе!")
    print(f"Мониторинг штрафов запущен (интервал: {MONITOR_POLL_INTERVAL}s, задержка: {MONITOR_RATE_LIMIT}s)")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown
        print("Остановка мониторинга штрафов...")
        await fine_monitor.stop()
        await scraper.close_aiohttp_session()
        await database.close()
        print("Мониторинг остановлен, база данных закрыта.")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")