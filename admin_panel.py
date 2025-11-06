import logging
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from admin_roles import AdminRole, get_user_role, require_role, is_admin
from admin_logger import log_admin_action
from markdown_utils import escape_markdown_v2

logger = logging.getLogger(__name__)

escape_markdown = escape_markdown_v2

# Create dedicated router for admin panel
admin_router = Router()

# Global reference to database (will be set during initialization)
database = None
bot_instance = None

# Simple state manager for multi-step operations
_admin_state_store: dict[int, dict] = {}


async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """
    Safely edit a message, catching TelegramBadRequest if message is not modified.
    This prevents errors when trying to edit a message with the same content.
    """
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug(f"Message not modified, skipping edit: {e}")
        else:
            raise


def set_admin_dependencies(db, bot):
    """
    Set database and bot dependencies for admin panel.
    Should be called during bot initialization.
    """
    global database, bot_instance
    database = db
    bot_instance = bot


class AdminSection:
    """Enum-like class for admin panel sections"""
    GENERAL = "general"
    CLIENTS = "clients"
    PAYMENTS = "payments"
    SETTINGS = "settings"


def get_section_permissions() -> dict[str, AdminRole]:
    """
    Define minimum role required for each section.
    
    Returns:
        Dictionary mapping section name to minimum required role
    """
    return {
        AdminSection.GENERAL: AdminRole.RND,     # All admins can access
        AdminSection.CLIENTS: AdminRole.RND,     # All admins can access (write ops require CO+)
        AdminSection.PAYMENTS: AdminRole.CO,     # Co-Admins and above
        AdminSection.SETTINGS: AdminRole.ADMIN,  # Full admins only
    }


def get_section_emoji(section: str) -> str:
    """Get emoji for section"""
    emojis = {
        AdminSection.GENERAL: "📊",
        AdminSection.CLIENTS: "👥",
        AdminSection.PAYMENTS: "💰",
        AdminSection.SETTINGS: "⚙️"
    }
    return emojis.get(section, "📁")


def get_section_name(section: str) -> str:
    """Get display name for section"""
    names = {
        AdminSection.GENERAL: "Общая информация",
        AdminSection.CLIENTS: "Управление клиентами",
        AdminSection.PAYMENTS: "Платежные системы",
        AdminSection.SETTINGS: "Настройки системы"
    }
    return names.get(section, section.title())


def build_admin_menu(user_role: AdminRole) -> InlineKeyboardBuilder:
    """
    Build admin menu showing only sections available to the user's role.
    
    Args:
        user_role: The user's admin role
        
    Returns:
        InlineKeyboardBuilder with appropriate buttons
    """
    builder = InlineKeyboardBuilder()
    permissions = get_section_permissions()
    
    # Add buttons for sections user has access to
    available_sections = []
    for section, required_role in permissions.items():
        if user_role >= required_role:
            emoji = get_section_emoji(section)
            name = get_section_name(section)
            builder.button(
                text=f"{emoji} {name}",
                callback_data=f"admin_section:{section}"
            )
            available_sections.append(section)
    
    # Arrange buttons (2 per row if possible)
    if len(available_sections) > 1:
        builder.adjust(2)
    
    # Add close button
    builder.row()
    builder.button(text="❌ Закрыть", callback_data="admin_close")
    
    return builder


@admin_router.message(Command("admin_search"))
async def cmd_admin_search(message: Message):
    """
    Search for user by Telegram User ID (admin command).
    Usage: /admin_search <user_id>
    """
    user_id = message.from_user.id
    user_role = get_user_role(user_id)
    
    # Check if user has any admin role
    if user_role < AdminRole.RND:
        await message.answer(
            "⛔ *Доступ запрещён*\n\n"
            "У вас нет прав доступа к панели администратора\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Parse search query
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ *Укажите USER\\_ID пользователя*\n\n"
            "Использование: `/admin_search USER_ID`\n\n"
            "💡 Пример: `/admin_search 7240463796`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    user_id_str = args[1].strip()
    
    # Validate user ID format (must be a number)
    if not user_id_str.isdigit():
        await message.answer(
            "❌ *Неверный формат USER\\_ID*\n\n"
            "USER\\_ID должен быть числом\\.\n\n"
            "💡 Пример: `/admin_search 7240463796`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    search_user_id = int(user_id_str)
    
    # Search for user in database
    try:
        user = await database.get_user(search_user_id)
        
        if not user:
            await message.answer(
                f"❌ *Пользователь не найден*\n\n"
                f"Пользователь с ID `{search_user_id}` не найден в базе данных\\.\n\n"
                "*Возможные причины:*\n"
                "• Пользователь еще не запускал бота\n"
                "• Неверный ID\n\n"
                "Попробуйте другой ID или используйте раздел \"Управление клиентами\"\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        # Get additional information
        subscription = await database.get_active_subscription(search_user_id)
        binding = await database.get_vehicle_binding(search_user_id)
        current_usage = await database.get_daily_usage(search_user_id)
        
        # Format registration date
        created_at = user.get('created_at', '')
        if created_at:
            try:
                from datetime import datetime
                if isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at)
                    reg_date = dt.strftime('%d.%m.%Y %H:%M')
                else:
                    reg_date = created_at.strftime('%d.%m.%Y %H:%M')
            except:
                reg_date = 'неизвестно'
        else:
            reg_date = 'неизвестно'
        
        # Format subscription info
        if subscription:
            expires_at = subscription.get('expires_at', '')
            try:
                from datetime import datetime
                if isinstance(expires_at, str):
                    dt = datetime.fromisoformat(expires_at)
                    sub_info = f"до {dt.strftime('%d.%m.%Y')}"
                else:
                    sub_info = f"до {expires_at.strftime('%d.%m.%Y')}"
            except:
                sub_info = "активна"
        else:
            sub_info = "нет активной"
        
        # Format binding info
        binding_info = "нет привязки"
        if binding:
            plate = binding.get('plate_number', 'N/A')
            binding_info = f"{plate}"
        
        # Build user info text
        first_name = user.get('first_name', 'N/A')
        last_name = user.get('last_name', '') or ''
        username = user.get('username', 'не указан')
        is_premium = user.get('is_premium', False)
        is_blocked = user.get('is_blocked', False)
        
        status_emoji = "💎" if is_premium else "📱"
        status_text = "Premium" if is_premium else "Обычный"
        
        if is_blocked:
            status_emoji = "🚫"
            status_text = "Заблокирован"
        
        text = (
            "👤 *Пользователь найден*\n\n"
            f"🆔 *ID:* `{search_user_id}`\n"
            f"👤 *Имя:* {escape_markdown(f'{first_name} {last_name}'.strip())}\n"
            f"📱 *Username:* @{escape_markdown(username)}\n"
            f"{status_emoji} *Статус:* {escape_markdown(status_text)}\n"
            f"📅 *Регистрация:* {escape_markdown(reg_date)}\n"
            f"📆 *Подписка:* {escape_markdown(sub_info)}\n"
            f"🚗 *Привязка авто:* {escape_markdown(binding_info)}\n\n"
            f"*📊 Статистика:*\n"
            f"• Запросов сегодня: {escape_markdown(str(current_usage))}\n"
        )
        
        # Build management keyboard
        builder = InlineKeyboardBuilder()
        
        if is_premium:
            builder.add(InlineKeyboardButton(text="❌ Отозвать Premium", callback_data=f"admin_clients:revoke_premium:{search_user_id}"))
        else:
            builder.add(InlineKeyboardButton(text="💎 Выдать Premium", callback_data=f"admin_clients:grant_premium:{search_user_id}"))
        
        if is_blocked:
            builder.add(InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"admin_clients:unblock:{search_user_id}"))
        else:
            builder.add(InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"admin_clients:block:{search_user_id}"))
        
        builder.adjust(2)
        builder.row(
            InlineKeyboardButton(text="♻️ Снять лимит", callback_data=f"admin_clients:reset_limit:{search_user_id}")
        )
        builder.row(
            InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")
        )
        
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN_V2)
        
        # Log admin action
        if bot_instance:
            admin_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = message.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=message.from_user.id,
                admin_name=admin_name,
                action="User Search",
                details=f"Searched for user ID: {search_user_id}"
            )
        
    except Exception as e:
        logger.error(f"Error in admin search: {e}")
        import traceback
        traceback.print_exc()
        await message.answer("❌ Ошибка при поиске пользователя")


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    """
    Main admin panel command.
    Verifies role, fetches user record from DB, and renders inline menu.
    """
    user_id = message.from_user.id
    user_role = get_user_role(user_id)
    
    # Check if user has any admin role
    if user_role == AdminRole.NONE:
        await message.answer(
            "⛔ *Доступ запрещён*\n\n"
            "У вас нет прав доступа к панели администратора\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    # Fetch user record from database
    user_record = None
    if database:
        try:
            user_record = await database.get_or_create_user(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
        except Exception as e:
            logger.error(f"Error fetching user record for admin {user_id}: {e}")
    
    # Get role name
    role_names = {
        AdminRole.ADMIN: "Администратор",
        AdminRole.CO: "Со-Администратор",
        AdminRole.RND: "R&D Администратор"
    }
    role_name = role_names.get(user_role, "Unknown")
    
    # Build welcome message
    username = message.from_user.username or "Unknown"
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
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
    if bot_instance:
        await log_admin_action(
            bot=bot_instance,
            admin_id=user_id,
            admin_name=full_name,
            action="Accessed Admin Panel",
            details=f"Role: {role_name}"
        )


# General section implementation

@admin_router.callback_query(F.data == "admin_section:general")
@require_role(AdminRole.RND)
async def handle_general_section(callback: CallbackQuery):
    """
    Handle General section - shows bot mode, optimization and allows switching
    """
    from bot_mode_service import bot_mode_service, BotMode, get_mode_emoji, get_mode_display_name, get_mode_description
    
    if not bot_mode_service:
        await callback.answer("❌ Сервис режимов не инициализирован", show_alert=True)
        return
    
    # Get current mode
    current_mode = await bot_mode_service.get_mode()
    
    # Build section text
    emoji = get_mode_emoji(current_mode)
    mode_name = get_mode_display_name(current_mode)
    mode_desc = get_mode_description(current_mode)
    
    # Ensure database is available
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    # Get optimization status
    raw_optimization = await database.get_setting("optimization_enabled")
    if raw_optimization is None:
        optimization_enabled = True
        await database.set_setting(
            "optimization_enabled",
            True,
            "bool",
            "Параллельная загрузка медиафайлов"
        )
    else:
        if isinstance(raw_optimization, bool):
            optimization_enabled = raw_optimization
        else:
            optimization_enabled = str(raw_optimization).lower() == "true"
    
    opt_status_emoji = "✅" if optimization_enabled else "❌"
    opt_status_text = "Включена" if optimization_enabled else "Выключена"
    
    section_text = (
        "⚙️ *ОСНОВНЫЕ НАСТРОЙКИ*\n\n"
        f"📊 *Режим работы:* {emoji} {escape_markdown(mode_name)}\n\n"
        f"⚡ *Оптимизация загрузки:* {opt_status_emoji} {escape_markdown(opt_status_text)}\n\n"
        f"📝 _{escape_markdown(mode_desc)}_\n\n"
        "Выберите действие:"
    )
    
    # Build keyboard
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Изменить режим", callback_data="admin_general:change_mode")
    builder.button(text="⚡ Переключить оптимизацию", callback_data="admin_general:toggle_optimization")
    builder.row()
    builder.button(text="📊 Статистика", callback_data="admin_general:stats")
    builder.row()
    builder.button(text="◀️ Назад в меню", callback_data="admin_back_to_menu")
    
    await safe_edit_message(
        callback,
        section_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("Открыт раздел: Основные настройки")


@admin_router.callback_query(F.data == "admin_general:change_mode")
@require_role(AdminRole.ADMIN)
async def handle_change_mode_menu(callback: CallbackQuery):
    """
    Show mode selection menu - Admin only
    """
    from bot_mode_service import bot_mode_service, BotMode, get_mode_emoji, get_mode_display_name
    
    if not bot_mode_service:
        await callback.answer("❌ Сервис режимов не инициализирован", show_alert=True)
        return
    
    # Get current mode
    current_mode = await bot_mode_service.get_mode()
    
    section_text = (
        "🔄 *Изменение режима бота*\n\n"
        f"Текущий режим: *{escape_markdown(get_mode_display_name(current_mode))}*\n\n"
        "Выберите новый режим:"
    )
    
    # Build mode selection keyboard
    builder = InlineKeyboardBuilder()
    
    for mode in BotMode:
        emoji = get_mode_emoji(mode)
        name = get_mode_display_name(mode)
        # Mark current mode
        if mode == current_mode:
            button_text = f"{emoji} {name} ✓"
        else:
            button_text = f"{emoji} {name}"
        builder.button(text=button_text, callback_data=f"admin_general:set_mode:{mode.value}")
    
    builder.adjust(2)  # 2 buttons per row
    builder.row()
    builder.button(text="◀️ Назад", callback_data="admin_section:general")
    
    await safe_edit_message(
        callback,
        section_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_general:set_mode:"))
@require_role(AdminRole.ADMIN)
async def handle_set_mode(callback: CallbackQuery):
    """
    Set new bot mode and log the change
    """
    from bot_mode_service import bot_mode_service, BotMode, get_mode_display_name
    
    if not bot_mode_service:
        await callback.answer("❌ Сервис режимов не инициализирован", show_alert=True)
        return
    
    # Parse mode from callback data
    mode_value = callback.data.split(":", 3)[2]
    
    try:
        new_mode = BotMode(mode_value)
    except ValueError:
        await callback.answer("❌ Неверный режим", show_alert=True)
        return
    
    # Get old mode for logging
    old_mode = await bot_mode_service.get_mode()
    
    # Check if mode is already set
    if new_mode == old_mode:
        await callback.answer("ℹ️ Этот режим уже установлен")
        # Return to general section
        await handle_general_section(callback)
        return
    
    # Set new mode
    await bot_mode_service.set_mode(new_mode)
    
    # Log to admin log group
    if bot_instance:
        admin_id = callback.from_user.id
        admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
        if not admin_name:
            admin_name = callback.from_user.username or "Unknown"
        
        old_mode_name = get_mode_display_name(old_mode)
        new_mode_name = get_mode_display_name(new_mode)
        
        await log_admin_action(
            bot=bot_instance,
            admin_id=admin_id,
            admin_name=admin_name,
            action="Bot Mode Changed",
            details=f"From: {old_mode_name} → To: {new_mode_name}"
        )
    
    # Show confirmation
    await callback.answer(f"✅ Режим изменён на: {get_mode_display_name(new_mode)}", show_alert=True)
    
    # Return to general section to show new mode
    await handle_general_section(callback)


@admin_router.callback_query(F.data == "admin_general:toggle_optimization")
@require_role(AdminRole.ADMIN)
async def handle_toggle_optimization(callback: CallbackQuery):
    """
    Toggle optimization mode for media downloads
    """
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        # Get current setting
        raw_current = await database.get_setting("optimization_enabled")
        if raw_current is None:
            current = True
        elif isinstance(raw_current, bool):
            current = raw_current
        else:
            current = str(raw_current).lower() == "true"
        
        # Toggle value
        new_value = not current
        
        # Save to database
        await database.set_setting("optimization_enabled", new_value, "bool", "Параллельная загрузка медиафайлов")
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Optimization Toggle",
                details=f"Optimization set to: {str(new_value).lower()}"
            )
        
        # Show confirmation
        status = "включена ⚡" if new_value else "выключена"
        await callback.answer(f"Оптимизация загрузки {status}!", show_alert=True)
        
        # Refresh view
        await handle_general_section(callback)
        
    except Exception as e:
        logger.error(f"Error toggling optimization: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer("❌ Ошибка при переключении оптимизации", show_alert=True)


@admin_router.callback_query(F.data == "admin_general:stats")
@require_role(AdminRole.RND)
async def handle_general_stats(callback: CallbackQuery):
    """
    Show comprehensive bot statistics
    """
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        stats = await database.get_statistics()
        
        # Build statistics text
        stats_text = (
            "📊 *Статистика бота*\n\n"
            "*Общая информация:*\n"
            f"👥 Всего пользователей: {escape_markdown(str(stats['total_users']))}\n"
            f"💎 Premium пользователей: {escape_markdown(str(stats['premium_users']))}\n"
            f"📱 Обычных пользователей: {escape_markdown(str(stats['regular_users']))}\n"
            f"⭐ Активных подписок: {escape_markdown(str(stats['active_subscriptions']))}\n\n"
            "*За сегодня:*\n"
            f"👤 Новых пользователей: {escape_markdown(str(stats['new_users_today']))}\n"
            f"🔍 Выполненных запросов: {escape_markdown(str(stats['requests_today']))}\n"
            f"💎 Новых подписок: {escape_markdown(str(stats['subscriptions_today']))}\n\n"
            "*Финансы:*\n"
            f"💳 Всего оплат: {escape_markdown(str(stats['total_payments_count']))} \\({escape_markdown(str(stats['total_payments_amount']))} смн\\)\n"
            f"⏳ Ожидают подтверждения: {escape_markdown(str(stats['pending_payments_count']))} \\({escape_markdown(str(stats['pending_payments_amount']))} смн\\)\n"
            f"✅ Подтверждено сегодня: {escape_markdown(str(stats['confirmed_payments_today_count']))} \\({escape_markdown(str(stats['confirmed_payments_today_amount']))} смн\\)\n"
        )
        
        # Add top users
        if stats['top_users']:
            stats_text += "\n*Топ\\-3 активных пользователя:*\n"
            for idx, user in enumerate(stats['top_users'], 1):
                username = user.get('username', 'N/A')
                user_id = user.get('user_id')
                requests = user.get('requests', 0)
                stats_text += f"{idx}\\. @{escape_markdown(username)} \\(ID: `{user_id}`\\) \\- {escape_markdown(str(requests))} запросов\n"
        
        # Build keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Обновить", callback_data="admin_general:stats")
        builder.row()
        builder.button(text="◀️ Назад", callback_data="admin_section:general")
        
        await safe_edit_message(
            callback,
            stats_text,
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await callback.answer("Статистика обновлена")
    
    except Exception as e:
        logger.error(f"Error fetching statistics: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_general:"))
@require_role(AdminRole.RND)
async def handle_general_actions(callback: CallbackQuery):
    """Placeholder for other general section actions"""
    await callback.answer("🚧 В разработке", show_alert=True)


@admin_router.callback_query(F.data == "admin_section:clients")
@require_role(AdminRole.RND)
async def handle_clients_section(callback: CallbackQuery):
    """
    Handle Clients section - main entry point
    Shows search interface and user list
    """
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    # Build section text
    section_text = (
        "👥 *Управление клиентами*\n\n"
        "🔍 Используйте кнопки ниже для поиска пользователей\\.\n\n"
        "💡 *Подсказка:* Введите Telegram ID или username для поиска\\."
    )
    
    # Build keyboard
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск по ID", callback_data="admin_clients:search_prompt")
    builder.button(text="📋 Список всех", callback_data="admin_clients:list:0")
    builder.row()
    builder.button(text="◀️ Назад в меню", callback_data="admin_back_to_menu")
    
    await safe_edit_message(
        callback,
        section_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("Открыт раздел: Управление клиентами")


@admin_router.callback_query(F.data.startswith("admin_clients:list:"))
@require_role(AdminRole.RND)
async def handle_clients_list(callback: CallbackQuery):
    """Show paginated list of users"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    # Parse page number from callback data
    try:
        page = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        page = 0
    
    page = max(0, page)  # Ensure non-negative
    
    # Get users from database
    users, total_count = await database.search_users(limit=10, offset=page * 10)
    
    if not users:
        await callback.answer("Пользователи не найдены", show_alert=True)
        return
    
    # Build user list text
    text_parts = [
        "👥 *Список пользователей*\n"
    ]
    
    for idx, user in enumerate(users, start=page * 10 + 1):
        user_id = user['user_id']
        username = user.get('username') or 'N/A'
        first_name = user.get('first_name') or ''
        last_name = user.get('last_name') or ''
        full_name = f"{first_name} {last_name}".strip() or username
        
        status_icons = []
        if user['is_blocked']:
            status_icons.append("🚫")
        if user['is_premium']:
            status_icons.append("⭐")
        
        status_str = " ".join(status_icons) if status_icons else ""
        
        text_parts.append(
            f"\n{idx}\\. `{user_id}` \\- {escape_markdown(full_name)} {status_str}"
        )
    
    # Add pagination info
    total_pages = (total_count + 9) // 10
    current_page = page + 1
    text_parts.append(f"\n\n📄 Страница {current_page} из {total_pages} \\({total_count} всего\\)")
    
    list_text = "".join(text_parts)
    
    # Build keyboard with user buttons and pagination
    builder = InlineKeyboardBuilder()
    
    # Add user buttons (2 per row)
    for user in users:
        user_id = user['user_id']
        username = user.get('username') or str(user_id)[:8]
        builder.button(text=f"👤 {username}", callback_data=f"admin_clients:view:{user_id}")
    
    builder.adjust(2)
    
    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(builder.button(text="◀️ Назад", callback_data=f"admin_clients:list:{page-1}"))
    if (page + 1) * 10 < total_count:
        nav_row.append(builder.button(text="Вперёд ▶️", callback_data=f"admin_clients:list:{page+1}"))
    
    if nav_row:
        builder.row()
    
    # Back to menu
    builder.row()
    builder.button(text="🔍 Поиск", callback_data="admin_clients:search_prompt")
    builder.button(text="◀️ Назад", callback_data="admin_section:clients")
    
    await safe_edit_message(
        callback,
        list_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer()


@admin_router.callback_query(F.data == "admin_clients:search_prompt")
@require_role(AdminRole.RND)
async def handle_search_prompt(callback: CallbackQuery):
    """Prompt user to enter search query"""
    prompt_text = (
        "🔍 *Поиск пользователя*\n\n"
        "Для поиска пользователя введите команду:\n\n"
        "`/admin_search` `<Telegram ID>`\n\n"
        "💡 *Пример:*\n"
        "`/admin_search 123456789`\n\n"
        "_После поиска вернитесь в админ\\-панель командой_ `/admin`"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="admin_section:clients")
    
    await safe_edit_message(
        callback,
        prompt_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_clients:view:"))
@require_role(AdminRole.RND)
async def handle_view_client(callback: CallbackQuery):
    """Show detailed user card with action buttons"""
    # Clear any pending state when viewing a user
    admin_id = callback.from_user.id
    if admin_id in _admin_state_store:
        del _admin_state_store[admin_id]
    
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    # Parse user_id from callback data
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    # Get user from database
    user = await database.get_user(user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Get additional data
    vehicle_binding = await database.get_vehicle_binding(user_id)
    daily_usage = await database.get_daily_usage(user_id)
    
    # Build user card
    from datetime import datetime
    
    username = user.get('username') or 'N/A'
    first_name = user.get('first_name') or ''
    last_name = user.get('last_name') or ''
    full_name = f"{first_name} {last_name}".strip() or username
    
    # Parse dates
    created_at = datetime.fromisoformat(user['created_at']).strftime("%d.%m.%Y")
    
    premium_status = "⭐ Активна" if user['is_premium'] else "❌ Нет"
    if user['is_premium'] and user['premium_expires_at']:
        expires_at = datetime.fromisoformat(user['premium_expires_at']).strftime("%d.%m.%Y %H:%M")
        premium_status = f"⭐ До {escape_markdown(expires_at)}"
    
    block_status = "🚫 Заблокирован" if user['is_blocked'] else "✅ Активен"
    
    binding_info = "❌ Нет"
    if vehicle_binding:
        plate = vehicle_binding['plate_number']
        binding_expires = datetime.fromisoformat(vehicle_binding['subscription_expires_at']).strftime("%d.%m.%Y")
        binding_info = f"🚗 {escape_markdown(plate)} \\(до {escape_markdown(binding_expires)}\\)"
    
    card_text = (
        f"👤 *Карточка пользователя*\n\n"
        f"🆔 *ID:* `{user_id}`\n"
        f"👤 *Имя:* {escape_markdown(full_name)}\n"
        f"📱 *Username:* @{escape_markdown(username) if username != 'N/A' else 'N/A'}\n"
        f"📅 *Регистрация:* {escape_markdown(created_at)}\n\n"
        f"⭐ *Premium:* {premium_status}\n"
        f"🔒 *Статус:* {block_status}\n"
        f"🚗 *Привязка ТС:* {binding_info}\n"
        f"📊 *Запросов сегодня:* {escape_markdown(str(daily_usage))}\n"
    )
    
    # Build action buttons based on role
    admin_role = get_user_role(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    
    # Premium management (CO and ADMIN only)
    if admin_role >= AdminRole.CO:
        if user['is_premium']:
            builder.button(text="❌ Отозвать Premium", callback_data=f"admin_clients:revoke_premium:{user_id}")
        else:
            builder.button(text="⭐ Выдать Premium", callback_data=f"admin_clients:grant_premium:{user_id}")
    
    # Block/Unblock (CO and ADMIN only)
    if admin_role >= AdminRole.CO:
        if user['is_blocked']:
            builder.button(text="✅ Разблокировать", callback_data=f"admin_clients:unblock:{user_id}")
        else:
            builder.button(text="🚫 Заблокировать", callback_data=f"admin_clients:block:{user_id}")
    
    builder.adjust(2)
    
    # Vehicle binding management (All admin roles)
    builder.row()
    if vehicle_binding:
        builder.button(text="🗑 Удалить привязку", callback_data=f"admin_clients:remove_binding:{user_id}")
    builder.button(text="🔄 Изменить привязку", callback_data=f"admin_clients:reassign_binding:{user_id}")
    
    # Reset daily limit (All admin roles)
    builder.row()
    builder.button(text="♻️ СНЯТЬ ЛИМИТ", callback_data=f"admin_clients:reset_limit:{user_id}")
    
    # Navigation
    builder.row()
    builder.button(text="◀️ К списку", callback_data="admin_clients:list:0")
    builder.button(text="🏠 Главное меню", callback_data="admin_back_to_menu")
    
    await safe_edit_message(
        callback,
        card_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_clients:grant_premium:"))
@require_role(AdminRole.CO)
async def handle_grant_premium_prompt(callback: CallbackQuery):
    """Show duration selection for granting premium"""
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    prompt_text = (
        "⭐ *Выдача Intellex Premium*\n\n"
        "Выберите срок действия подписки:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="7 дней", callback_data=f"admin_clients:grant_premium_confirm:{user_id}:7")
    builder.button(text="30 дней", callback_data=f"admin_clients:grant_premium_confirm:{user_id}:30")
    builder.row()
    builder.button(text="90 дней", callback_data=f"admin_clients:grant_premium_confirm:{user_id}:90")
    builder.button(text="365 дней", callback_data=f"admin_clients:grant_premium_confirm:{user_id}:365")
    builder.row()
    builder.button(text="❌ Отмена", callback_data=f"admin_clients:view:{user_id}")
    
    await safe_edit_message(
        callback,
        prompt_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_clients:grant_premium_confirm:"))
@require_role(AdminRole.CO)
async def handle_grant_premium_confirm(callback: CallbackQuery):
    """Grant premium to user with selected duration"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[2])
        days = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверные параметры", show_alert=True)
        return
    
    # Calculate expiry date
    from datetime import datetime, timedelta
    expires_at = datetime.now() + timedelta(days=days)
    
    # Update user premium status
    try:
        await database.update_user_premium(user_id, is_premium=True, expires_at=expires_at)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Grant Premium",
                details=f"Duration: {days} days, Expires: {expires_at.strftime('%d.%m.%Y %H:%M')}",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="grant_premium",
            target_user_id=user_id,
            action_details={"days": days, "expires_at": expires_at.isoformat()}
        )
        
        await callback.answer(f"✅ Premium выдан на {days} дней", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error granting premium: {e}")
        await callback.answer("❌ Ошибка при выдаче Premium", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:revoke_premium:"))
@require_role(AdminRole.CO)
async def handle_revoke_premium(callback: CallbackQuery):
    """Revoke premium from user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    # Update user premium status
    try:
        from datetime import datetime
        await database.update_user_premium(user_id, is_premium=False, expires_at=None)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Revoke Premium",
                details=f"Revoked at: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="revoke_premium",
            target_user_id=user_id,
            action_details={"revoked_at": datetime.now().isoformat()}
        )
        
        await callback.answer("✅ Premium отозван", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error revoking premium: {e}")
        await callback.answer("❌ Ошибка при отзыве Premium", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:block:"))
@require_role(AdminRole.CO)
async def handle_block_user(callback: CallbackQuery):
    """Block a user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    try:
        await database.block_user(user_id)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Block User",
                details="User access blocked",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="block_user",
            target_user_id=user_id,
            action_details={"blocked": True}
        )
        
        await callback.answer("✅ Пользователь заблокирован", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error blocking user: {e}")
        await callback.answer("❌ Ошибка при блокировке", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:unblock:"))
@require_role(AdminRole.CO)
async def handle_unblock_user(callback: CallbackQuery):
    """Unblock a user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    try:
        await database.unblock_user(user_id)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Unblock User",
                details="User access restored",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="unblock_user",
            target_user_id=user_id,
            action_details={"blocked": False}
        )
        
        await callback.answer("✅ Пользователь разблокирован", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error unblocking user: {e}")
        await callback.answer("❌ Ошибка при разблокировке", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:reset_limit:"))
@require_role(AdminRole.RND)
async def handle_reset_daily_limit(callback: CallbackQuery):
    """Reset daily usage limit for a user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    try:
        old_usage = await database.get_daily_usage(user_id)
        await database.reset_daily_usage(user_id)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Reset Daily Limit",
                details=f"Reset usage from {old_usage} to 0",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="reset_daily_limit",
            target_user_id=user_id,
            action_details={"old_usage": old_usage, "new_usage": 0}
        )
        
        await callback.answer(f"✅ Дневной лимит сброшен (было: {old_usage})", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error resetting daily limit: {e}")
        await callback.answer("❌ Ошибка при сбросе лимита", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:remove_binding:"))
@require_role(AdminRole.RND)
async def handle_remove_binding(callback: CallbackQuery):
    """Remove vehicle binding from user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    try:
        # Get current binding for logging
        binding = await database.get_vehicle_binding(user_id)
        plate = binding['plate_number'] if binding else "Unknown"
        
        await database.remove_vehicle_binding(user_id)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Remove Vehicle Binding",
                details=f"Removed plate: {plate}",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="remove_binding",
            target_user_id=user_id,
            action_details={"plate": plate}
        )
        
        await callback.answer("✅ Привязка удалена", show_alert=True)
        
        # Return to user card - create new callback data instead of modifying frozen object
        from aiogram.types import CallbackQuery as CQ
        new_callback = CQ(
            id=callback.id,
            from_user=callback.from_user,
            message=callback.message,
            chat_instance=callback.chat_instance,
            data=f"admin_clients:view:{user_id}"
        )
        await handle_view_client(new_callback)
        
    except Exception as e:
        logger.error(f"Error removing binding: {e}")
        await callback.answer("❌ Ошибка при удалении привязки", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:reassign_binding:"))
@require_role(AdminRole.RND)
async def handle_reassign_binding_prompt(callback: CallbackQuery):
    """Prompt for new plate number to reassign binding"""
    try:
        target_user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    # Store state for this admin
    admin_id = callback.from_user.id
    _admin_state_store[admin_id] = {
        "action": "reassign_binding",
        "target_user_id": target_user_id
    }
    
    prompt_text = (
        "🔄 *Изменение привязки ТС*\n\n"
        f"Пользователь: `{target_user_id}`\n\n"
        "Отправьте номер нового транспортного средства для привязки\\.\n\n"
        "💡 *Формат:* `A123BC777` или `А123ВС777`\n\n"
        "_Используйте_ `/admin_cancel` _для отмены\\._"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"admin_clients:view:{target_user_id}")
    
    await safe_edit_message(
        callback,
        prompt_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("Введите новый номер ТС")


@admin_router.message(Command("admin_cancel"))
async def cmd_admin_cancel(message: Message):
    """Cancel any pending admin action"""
    admin_id = message.from_user.id
    if admin_id in _admin_state_store:
        del _admin_state_store[admin_id]
        await message.answer("✅ Действие отменено")
    else:
        await message.answer("ℹ️ Нет активных действий для отмены")


@admin_router.message(F.text)
async def handle_admin_text_input(message: Message):
    """Handle text input for admin actions that require it"""
    admin_id = message.from_user.id
    
    # Check if admin has pending action
    if admin_id not in _admin_state_store:
        return  # Not an admin action, let other handlers process it
    
    state = _admin_state_store[admin_id]
    action = state.get("action")
    
    if action == "reassign_binding":
        # Verify admin still has required role
        user_role = get_user_role(admin_id)
        if user_role < AdminRole.RND:
            del _admin_state_store[admin_id]
            await message.answer("⛔ Недостаточно прав доступа")
            return
        
        if not database:
            del _admin_state_store[admin_id]
            await message.answer("❌ База данных не инициализирована")
            return
        
        target_user_id = state.get("target_user_id")
        new_plate = message.text.strip().upper()
        
        # Basic validation
        if len(new_plate) < 6 or len(new_plate) > 15:
            await message.answer(
                "❌ *Неверный формат номера*\n\n"
                "Номер должен содержать от 6 до 15 символов\\.\n\n"
                "Попробуйте еще раз или используйте `/admin_cancel`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        try:
            # Set binding with 365 days expiry (can be adjusted)
            from datetime import datetime, timedelta
            expires_at = datetime.now() + timedelta(days=365)
            
            # Get old binding for logging
            old_binding = await database.get_vehicle_binding(target_user_id)
            old_plate = old_binding['plate_number'] if old_binding else "None"
            
            await database.set_vehicle_binding(target_user_id, new_plate, expires_at)
            
            # Log action
            if bot_instance:
                admin_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
                if not admin_name:
                    admin_name = message.from_user.username or "Unknown"
                
                await log_admin_action(
                    bot=bot_instance,
                    admin_id=admin_id,
                    admin_name=admin_name,
                    action="Reassign Vehicle Binding",
                    details=f"Old: {old_plate} → New: {new_plate}, Expires: {expires_at.strftime('%d.%m.%Y')}",
                    target_user_id=target_user_id
                )
            
            # Log to database
            await database.log_admin_action(
                admin_user_id=admin_id,
                action_type="reassign_binding",
                target_user_id=target_user_id,
                action_details={"old_plate": old_plate, "new_plate": new_plate, "expires_at": expires_at.isoformat()}
            )
            
            # Clear state
            del _admin_state_store[admin_id]
            
            # Show success with user card
            await message.answer(
                f"✅ *Привязка изменена*\n\n"
                f"Пользователь: `{target_user_id}`\n"
                f"Старый номер: {escape_markdown(old_plate)}\n"
                f"Новый номер: {escape_markdown(new_plate)}\n"
                f"Срок действия: до {expires_at.strftime('%d.%m.%Y')}",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            # Build user card for quick access
            user = await database.get_user(target_user_id)
            if user:
                vehicle_binding = await database.get_vehicle_binding(target_user_id)
                daily_usage = await database.get_daily_usage(target_user_id)
                
                username = user.get('username') or 'N/A'
                first_name = user.get('first_name') or ''
                last_name = user.get('last_name') or ''
                full_name = f"{first_name} {last_name}".strip() or username
                
                created_at = datetime.fromisoformat(user['created_at']).strftime("%d.%m.%Y")
                
                premium_status = "⭐ Активна" if user['is_premium'] else "❌ Нет"
                if user['is_premium'] and user['premium_expires_at']:
                    expires_at_premium = datetime.fromisoformat(user['premium_expires_at']).strftime("%d.%m.%Y %H:%M")
                    premium_status = f"⭐ До {escape_markdown(expires_at_premium)}"
                
                block_status = "🚫 Заблокирован" if user['is_blocked'] else "✅ Активен"
                
                binding_info = "❌ Нет"
                if vehicle_binding:
                    plate = vehicle_binding['plate_number']
                    binding_expires = datetime.fromisoformat(vehicle_binding['subscription_expires_at']).strftime("%d.%m.%Y")
                    binding_info = f"🚗 {escape_markdown(plate)} \\(до {escape_markdown(binding_expires)}\\)"
                
                card_text = (
                    f"👤 *Обновленная карточка пользователя*\n\n"
                    f"🆔 *ID:* `{target_user_id}`\n"
                    f"👤 *Имя:* {escape_markdown(full_name)}\n"
                    f"📱 *Username:* @{escape_markdown(username) if username != 'N/A' else 'N/A'}\n"
                    f"📅 *Регистрация:* {escape_markdown(created_at)}\n\n"
                    f"⭐ *Premium:* {premium_status}\n"
                    f"🔒 *Статус:* {block_status}\n"
                    f"🚗 *Привязка ТС:* {binding_info}\n"
                    f"📊 *Запросов сегодня:* {escape_markdown(str(daily_usage))}\n"
                )
                
                builder = InlineKeyboardBuilder()
                
                # Premium management (CO and ADMIN only)
                if user_role >= AdminRole.CO:
                    if user['is_premium']:
                        builder.button(text="❌ Отозвать Premium", callback_data=f"admin_clients:revoke_premium:{target_user_id}")
                    else:
                        builder.button(text="⭐ Выдать Premium", callback_data=f"admin_clients:grant_premium:{target_user_id}")
                
                # Block/Unblock (CO and ADMIN only)
                if user_role >= AdminRole.CO:
                    if user['is_blocked']:
                        builder.button(text="✅ Разблокировать", callback_data=f"admin_clients:unblock:{target_user_id}")
                    else:
                        builder.button(text="🚫 Заблокировать", callback_data=f"admin_clients:block:{target_user_id}")
                
                builder.adjust(2)
                
                # Vehicle binding management (All admin roles)
                builder.row()
                if vehicle_binding:
                    builder.button(text="🗑 Удалить привязку", callback_data=f"admin_clients:remove_binding:{target_user_id}")
                builder.button(text="🔄 Изменить привязку", callback_data=f"admin_clients:reassign_binding:{target_user_id}")
                
                # Reset daily limit (All admin roles)
                builder.row()
                builder.button(text="♻️ СНЯТЬ ЛИМИТ", callback_data=f"admin_clients:reset_limit:{target_user_id}")
                
                await message.answer(
                    card_text,
                    reply_markup=builder.as_markup(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            
        except Exception as e:
            logger.error(f"Error reassigning binding: {e}")
            del _admin_state_store[admin_id]
            await message.answer("❌ Ошибка при изменении привязки")


@admin_router.callback_query(F.data.startswith("admin_clients:reset_limit:"))
@require_role(AdminRole.RND)
async def handle_reset_limit(callback: CallbackQuery):
    """Reset daily usage limit for user"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID пользователя", show_alert=True)
        return
    
    try:
        # Get current usage for logging
        current_usage = await database.get_daily_usage(user_id)
        
        await database.reset_daily_usage(user_id)
        
        # Log action
        if bot_instance:
            admin_id = callback.from_user.id
            admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
            if not admin_name:
                admin_name = callback.from_user.username or "Unknown"
            
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Reset Daily Limit",
                details=f"Previous usage: {current_usage} requests",
                target_user_id=user_id
            )
        
        # Log to database
        await database.log_admin_action(
            admin_user_id=callback.from_user.id,
            action_type="reset_limit",
            target_user_id=user_id,
            action_details={"previous_usage": current_usage}
        )
        
        await callback.answer(f"✅ Лимит сброшен (было: {current_usage})", show_alert=True)
        
        # Return to user card
        callback.data = f"admin_clients:view:{user_id}"
        await handle_view_client(callback)
        
    except Exception as e:
        logger.error(f"Error resetting limit: {e}")
        await callback.answer("❌ Ошибка при сбросе лимита", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_clients:"))
@require_role(AdminRole.RND)
async def handle_client_actions(callback: CallbackQuery):
    """Catch-all handler for other client actions"""
    await callback.answer("🚧 В разработке", show_alert=True)


@admin_router.callback_query(F.data == "admin_section:payments")
@require_role(AdminRole.CO)
async def handle_payments_section(callback: CallbackQuery):
    """
    Handle Payments section - show payment system information
    """
    # Build section text
    section_text = (
        "💰 *Платежные системы*\n\n"
        "✅ Привязан DC/ExpressPay"
    )
    
    # Build keyboard
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад в меню", callback_data="admin_back_to_menu")
    
    await safe_edit_message(
        callback,
        section_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("Открыт раздел: Платежные системы")


@admin_router.callback_query(F.data.startswith("payment:approve:"))
@require_role(AdminRole.CO)
async def handle_payment_approval(callback: CallbackQuery):
    """Handle payment approval"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        request_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID заявки", show_alert=True)
        return
    
    # Get payment request
    request = await database.get_payment_request(request_id)
    
    if not request:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    
    if request['status'] != 'awaiting_approval':
        await callback.answer("⚠️ Заявка уже обработана", show_alert=True)
        return
    
    user_id = request['user_id']
    payment_type = request['payment_type']
    
    if payment_type == 'subscription':
        # Process subscription payment
        from datetime import datetime, timedelta
        import subscription_service
        
        metadata = request.get('payment_metadata') or {}
        plan_id = metadata.get('plan_id')
        
        if not plan_id:
            await callback.answer("❌ Неверные данные заявки", show_alert=True)
            return
        
        # Calculate new expiry date
        current_subscription = await database.get_active_subscription(user_id)
        current_expiry = None
        if current_subscription:
            current_expiry = datetime.fromisoformat(current_subscription['expires_at'])
        
        new_expiry = subscription_service.calculate_new_expiry(current_expiry, plan_id)
        
        # Create or update subscription
        await database.create_subscription(
            user_id=user_id,
            subscription_type="premium",
            starts_at=datetime.now(),
            expires_at=new_expiry,
            payment_amount=request['amount'],
            payment_method="manual_approval"
        )
        
        # Update user premium status
        await database.update_user_premium(
            user_id=user_id,
            is_premium=True,
            expires_at=new_expiry
        )
        
        # Update payment request status
        await database.update_payment_request_status(
            request_id=request_id,
            status="approved",
            payment_method="manual_approval"
        )
        
        # Log action
        admin_id = callback.from_user.id
        admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
        if not admin_name:
            admin_name = callback.from_user.username or "Unknown"
        
        if bot_instance:
            await log_admin_action(
                bot=bot_instance,
                admin_id=admin_id,
                admin_name=admin_name,
                action="Approve Subscription Payment",
                details=f"Request #{request_id}, Plan: {metadata.get('plan_name', 'Unknown')}, Amount: {request['amount']} смн",
                target_user_id=user_id
            )
        
        await database.log_admin_action(
            admin_user_id=admin_id,
            action_type="approve_payment",
            target_user_id=user_id,
            action_details={
                "request_id": request_id,
                "plan_id": plan_id,
                "amount": request['amount'],
                "expires_at": new_expiry.isoformat()
            }
        )
        
        # Notify user
        try:
            expiry_str = subscription_service.format_expiry_date(new_expiry)
            notification_text = (
                "✅ *Платеж обработан*\n\n"
                "Ваша подписка Intellex Premium успешно активирована\\!\n\n"
                f"💎 *План:* {escape_markdown(metadata.get('plan_name', 'Unknown'))}\n"
                f"📅 *Действует до:* {escape_markdown(expiry_str)}\n\n"
                "Спасибо за использование нашего сервиса\\!"
            )
            
            await bot_instance.send_message(
                chat_id=user_id,
                text=notification_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error sending user notification: {e}")
        
        # Update callback message
        await safe_edit_message(
            callback,
            f"✅ *Заявка \\#{request_id} одобрена*\n\n"
            f"Пользователь `{user_id}` получил подписку\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        await callback.answer("✅ Платеж одобрен", show_alert=True)
    
    else:
        await callback.answer("⚠️ Неизвестный тип платежа", show_alert=True)


@admin_router.callback_query(F.data.startswith("payment:reject:"))
@require_role(AdminRole.CO)
async def handle_payment_rejection(callback: CallbackQuery):
    """Handle payment rejection"""
    if not database:
        await callback.answer("❌ База данных не инициализирована", show_alert=True)
        return
    
    try:
        request_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный ID заявки", show_alert=True)
        return
    
    # Get payment request
    request = await database.get_payment_request(request_id)
    
    if not request:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    
    if request['status'] != 'awaiting_approval':
        await callback.answer("⚠️ Заявка уже обработана", show_alert=True)
        return
    
    user_id = request['user_id']
    
    # Update payment request status
    await database.update_payment_request_status(
        request_id=request_id,
        status="rejected"
    )
    
    # Log action
    admin_id = callback.from_user.id
    admin_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
    if not admin_name:
        admin_name = callback.from_user.username or "Unknown"
    
    metadata = request.get('payment_metadata') or {}
    
    if bot_instance:
        await log_admin_action(
            bot=bot_instance,
            admin_id=admin_id,
            admin_name=admin_name,
            action="Reject Subscription Payment",
            details=f"Request #{request_id}, Amount: {request['amount']} смн",
            target_user_id=user_id
        )
    
    await database.log_admin_action(
        admin_user_id=admin_id,
        action_type="reject_payment",
        target_user_id=user_id,
        action_details={
            "request_id": request_id,
            "amount": request['amount']
        }
    )
    
    # Notify user
    try:
        notification_text = (
            "❌ *Оплата не найдена*\n\n"
            "К сожалению, мы не смогли подтвердить вашу оплату\\.\n\n"
            "Пожалуйста, убедитесь, что:\n"
            "• Платеж был успешно выполнен\n"
            "• Указана правильная сумма\n\n"
            "Если у вас есть вопросы, свяжитесь с поддержкой\\."
        )
        
        await bot_instance.send_message(
            chat_id=user_id,
            text=notification_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Error sending user notification: {e}")
    
    # Update callback message
    await safe_edit_message(
        callback,
        f"❌ *Заявка \\#{request_id} отклонена*\n\n"
        f"Пользователь `{user_id}` уведомлен\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("❌ Платеж отклонен", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_payments:"))
@require_role(AdminRole.CO)
async def handle_payment_actions(callback: CallbackQuery):
    """Catch-all handler for other payment actions"""
    await callback.answer("🚧 В разработке", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin_settings:"))
@require_role(AdminRole.ADMIN)
async def handle_settings_actions(callback: CallbackQuery):
    """Placeholder for settings actions"""
    await callback.answer("🚧 В разработке", show_alert=True)


# Generic section handler (fallback for sections without specific handlers)
# This must be placed AFTER all specific section handlers to avoid capturing their callbacks

@admin_router.callback_query(F.data.startswith("admin_section:"))
async def handle_admin_section(callback: CallbackQuery):
    """
    Handle navigation to admin sections.
    Shows "under construction" message for sections not yet wired.
    This is a fallback handler for sections without specific handlers.
    """
    section = callback.data.split(":", 1)[1]
    
    user_id = callback.from_user.id
    user_role = get_user_role(user_id)
    
    # Verify user has access to this section
    permissions = get_section_permissions()
    required_role = permissions.get(section, AdminRole.ADMIN)
    
    if user_role < required_role:
        await callback.answer("⛔ Недостаточно прав доступа", show_alert=True)
        return
    
    # Get section info
    emoji = get_section_emoji(section)
    name = get_section_name(section)
    
    # Build "under construction" message
    construction_text = (
        f"{emoji} *{escape_markdown(name)}*\n\n"
        "🚧 *Раздел в разработке*\n\n"
        "Данный функционал находится в процессе разработки "
        "и будет доступен в ближайшее время\\.\n\n"
        "_Следите за обновлениями\\!_"
    )
    
    # Back button
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад в меню", callback_data="admin_back_to_menu")
    
    await safe_edit_message(
        callback,
        construction_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer(f"Открыт раздел: {name}")


@admin_router.callback_query(F.data == "admin_back_to_menu")
async def handle_back_to_menu(callback: CallbackQuery):
    """Navigate back to main admin menu"""
    user_id = callback.from_user.id
    user_role = get_user_role(user_id)
    
    # Check if user still has admin role
    if user_role == AdminRole.NONE:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Get role name
    role_names = {
        AdminRole.ADMIN: "Администратор",
        AdminRole.CO: "Со-Администратор",
        AdminRole.RND: "R&D Администратор"
    }
    role_name = role_names.get(user_role, "Unknown")
    
    # Get user info
    first_name = callback.from_user.first_name or ""
    last_name = callback.from_user.last_name or ""
    username = callback.from_user.username or "Unknown"
    full_name = f"{first_name} {last_name}".strip() or username
    
    welcome_text = (
        "🔐 *Панель администратора*\n\n"
        f"👤 *Администратор:* {escape_markdown(full_name)}\n"
        f"🎭 *Роль:* {escape_markdown(role_name)}\n"
        f"🆔 *ID:* `{user_id}`\n\n"
        "Выберите раздел для управления:"
    )
    
    # Build menu
    menu = build_admin_menu(user_role)
    
    await safe_edit_message(
        callback,
        welcome_text,
        reply_markup=menu.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    await callback.answer("Главное меню")


@admin_router.callback_query(F.data == "admin_close")
async def handle_close_panel(callback: CallbackQuery):
    """Close admin panel"""
    await callback.message.delete()
    await callback.answer("Панель закрыта")