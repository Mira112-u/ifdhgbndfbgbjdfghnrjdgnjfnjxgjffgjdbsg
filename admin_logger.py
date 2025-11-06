import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from admin_roles import admin_config
from markdown_utils import escape_markdown_v2

logger = logging.getLogger(__name__)


async def log_admin_action(
    bot: Bot,
    admin_id: int,
    admin_name: str,
    action: str,
    details: Optional[str] = None,
    target_user_id: Optional[int] = None
) -> None:
    """
    Centralized logging helper that posts formatted messages to the configured log group.
    No-ops silently if LOG_GROUP_ID is not set.
    
    Args:
        bot: Bot instance for sending messages
        admin_id: Telegram ID of the admin performing the action
        admin_name: Display name of the admin
        action: Brief description of the action (e.g., "Block User", "Grant Premium")
        details: Additional details about the action
        target_user_id: User ID affected by the action (if applicable)
    """
    # If no log group is configured, silently return
    if not admin_config.log_group_id:
        logger.debug(f"Admin action logged (no log group): {action} by {admin_name} ({admin_id})")
        return
    
    try:
        # Format timestamp
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        
        # Build log message
        log_parts = [
            "🔧 *Admin Action Log*",
            "",
            f"⏰ *Time:* {escape_markdown(timestamp)}",
            f"👤 *Admin:* {escape_markdown(admin_name)} \\(`{admin_id}`\\)",
            f"⚡️ *Action:* {escape_markdown(action)}"
        ]
        
        if target_user_id:
            log_parts.append(f"🎯 *Target User:* `{target_user_id}`")
        
        if details:
            log_parts.append(f"📝 *Details:* {escape_markdown(details)}")
        
        log_message = "\n".join(log_parts)
        
        # Send to log group
        await bot.send_message(
            chat_id=admin_config.log_group_id,
            text=log_message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        logger.info(f"Admin action logged to group: {action} by {admin_name}")
        
    except TelegramAPIError as e:
        # Log error but don't fail the operation
        logger.error(f"Failed to send admin action log: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in log_admin_action: {e}")


def escape_markdown(text: str) -> str:
    """Backward-compatible helper that delegates to escape_markdown_v2."""
    return escape_markdown_v2(text)
