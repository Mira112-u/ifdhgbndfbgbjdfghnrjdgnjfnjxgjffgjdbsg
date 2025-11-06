import os
from enum import IntEnum
from typing import Optional, Callable, Any
from functools import wraps

from aiogram.types import Message, CallbackQuery
from dotenv import load_dotenv

load_dotenv()


class AdminRole(IntEnum):
    """
    Admin role hierarchy with precedence: Admin > Co > RND
    Higher numeric values indicate higher privileges
    """
    NONE = 0
    RND = 1  # Research & Development - limited admin access
    CO = 2   # Co-Admin - moderate admin access
    ADMIN = 3  # Full admin access


class AdminConfig:
    """Configuration holder for admin role assignments"""
    def __init__(self):
        self.admin_ids = self._parse_ids(os.getenv("ADMIN_IDS", ""))
        self.co_admin_ids = self._parse_ids(os.getenv("CO_ADMIN_IDS", ""))
        self.rnd_admin_ids = self._parse_ids(os.getenv("RND_ADMIN_IDS", ""))
        self.log_group_id = self._parse_log_group_id(os.getenv("LOG_GROUP_ID", ""))
    
    @staticmethod
    def _parse_ids(ids_str: str) -> set[int]:
        """Parse comma-separated user IDs from environment variable"""
        if not ids_str:
            return set()
        return {int(uid.strip()) for uid in ids_str.split(",") if uid.strip().isdigit()}
    
    @staticmethod
    def _parse_log_group_id(log_group_str: str) -> Optional[int]:
        """Parse log group ID from environment variable"""
        if not log_group_str or not log_group_str.strip():
            return None
        try:
            return int(log_group_str.strip())
        except ValueError:
            return None


# Global admin configuration instance
admin_config = AdminConfig()


def get_user_role(user_id: int) -> AdminRole:
    """
    Determine the highest role for a given user ID.
    Precedence: Admin > Co-Admin > RND Admin > None
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        AdminRole enum value
    """
    if user_id in admin_config.admin_ids:
        return AdminRole.ADMIN
    if user_id in admin_config.co_admin_ids:
        return AdminRole.CO
    if user_id in admin_config.rnd_admin_ids:
        return AdminRole.RND
    return AdminRole.NONE


def require_role(minimum_role: AdminRole):
    """
    Decorator to gate handler access based on minimum required role.
    Returns a friendly error message if user lacks permissions.
    
    Args:
        minimum_role: Minimum AdminRole required to access the handler
        
    Usage:
        @require_role(AdminRole.CO)
        async def my_admin_handler(message: Message):
            ...
    """
    def decorator(handler: Callable) -> Callable:
        @wraps(handler)
        async def wrapper(event: Message | CallbackQuery, *args, **kwargs) -> Any:
            # Extract user_id from either Message or CallbackQuery
            if isinstance(event, Message):
                user_id = event.from_user.id
                send_method = event.answer
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id
                send_method = event.message.answer if event.message else event.answer
            else:
                # Fallback for unknown event types
                return await handler(event, *args, **kwargs)
            
            user_role = get_user_role(user_id)
            
            # Check if user has sufficient role
            if user_role < minimum_role:
                role_names = {
                    AdminRole.ADMIN: "администратор",
                    AdminRole.CO: "со-администратор",
                    AdminRole.RND: "R&D администратор"
                }
                required_role_name = role_names.get(minimum_role, "администратор")
                
                error_message = (
                    "⛔ *Доступ запрещён*\n\n"
                    f"Для выполнения этого действия требуется роль: _{required_role_name}_\\.\n\n"
                    "Обратитесь к администратору, если считаете это ошибкой\\."
                )
                
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔ Доступ запрещён", show_alert=True)
                    if event.message:
                        try:
                            from aiogram.enums import ParseMode
                            await event.message.answer(error_message, parse_mode=ParseMode.MARKDOWN_V2)
                        except:
                            pass
                else:
                    try:
                        from aiogram.enums import ParseMode
                        await send_method(error_message, parse_mode=ParseMode.MARKDOWN_V2)
                    except:
                        await send_method("⛔ Доступ запрещён. Требуется роль администратора.")
                
                return None
            
            # User has sufficient permissions, proceed with handler
            return await handler(event, *args, **kwargs)
        
        return wrapper
    return decorator


def has_role(user_id: int, minimum_role: AdminRole) -> bool:
    """
    Check if a user has at least the specified role.
    
    Args:
        user_id: Telegram user ID
        minimum_role: Minimum AdminRole to check for
        
    Returns:
        True if user has sufficient role, False otherwise
    """
    return get_user_role(user_id) >= minimum_role


def is_admin(user_id: int) -> bool:
    """Check if user has any admin role"""
    return get_user_role(user_id) > AdminRole.NONE
