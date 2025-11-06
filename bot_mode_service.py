"""
Bot Mode Service - Manages bot operating modes with caching
"""
import logging
from enum import Enum
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)


class BotMode(str, Enum):
    """Bot operating modes"""
    NORMAL = "normal"
    TEST = "test"
    DISCOUNT50 = "discount50"
    DISCOUNT20 = "discount20"
    DISABLED = "disabled"


class BotModeService:
    """
    Lightweight service for managing and caching bot operating mode.
    Avoids database hits on every message by caching the current mode.
    """
    
    def __init__(self, database=None):
        """
        Initialize the bot mode service
        
        Args:
            database: Database instance for fetching/setting modes
        """
        self.database = database
        self._cached_mode: Optional[BotMode] = None
        self._lock = asyncio.Lock()
        self.SETTING_KEY = "bot_mode"
    
    async def get_mode(self) -> BotMode:
        """
        Get the current bot mode.
        Uses cached value if available, otherwise fetches from database.
        
        Returns:
            Current BotMode
        """
        # Return cached mode if available
        if self._cached_mode is not None:
            return self._cached_mode
        
        # Fetch from database and cache
        await self.refresh_cache()
        return self._cached_mode or BotMode.NORMAL
    
    async def set_mode(self, mode: BotMode) -> None:
        """
        Set the bot mode and update cache.
        
        Args:
            mode: New BotMode to set
        """
        if not self.database:
            logger.error("Cannot set mode: database not initialized")
            return
        
        async with self._lock:
            # Validate mode
            if not isinstance(mode, BotMode):
                try:
                    mode = BotMode(mode)
                except ValueError:
                    logger.error(f"Invalid mode: {mode}")
                    return
            
            # Persist to database
            await self.database.set_setting(
                key=self.SETTING_KEY,
                value=mode.value,
                value_type="string",
                description="Current bot operating mode"
            )
            
            # Update cache
            self._cached_mode = mode
            logger.info(f"Bot mode updated to: {mode.value}")
    
    async def refresh_cache(self) -> None:
        """
        Refresh the cached mode from database.
        Should be called after mode updates or on initialization.
        """
        if not self.database:
            logger.warning("Cannot refresh cache: database not initialized")
            self._cached_mode = BotMode.NORMAL
            return
        
        async with self._lock:
            mode_str = await self.database.get_setting(self.SETTING_KEY)
            
            if mode_str:
                try:
                    self._cached_mode = BotMode(mode_str)
                    logger.debug(f"Mode cache refreshed: {self._cached_mode.value}")
                except ValueError:
                    logger.warning(f"Invalid mode in database: {mode_str}, defaulting to NORMAL")
                    self._cached_mode = BotMode.NORMAL
            else:
                # No mode set, initialize with NORMAL
                self._cached_mode = BotMode.NORMAL
                await self.database.set_setting(
                    key=self.SETTING_KEY,
                    value=BotMode.NORMAL.value,
                    value_type="string",
                    description="Current bot operating mode"
                )
                logger.info("Initialized bot mode to NORMAL")
    
    def get_discount_multiplier(self, mode: Optional[BotMode] = None) -> float:
        """
        Get the discount multiplier for subscription pricing.
        
        Args:
            mode: BotMode to check (uses current mode if not provided)
        
        Returns:
            Discount multiplier (1.0 = no discount, 0.5 = 50% off, 0.8 = 20% off)
        """
        if mode is None:
            # This is synchronous access to cached value, should be safe
            mode = self._cached_mode or BotMode.NORMAL
        
        if mode == BotMode.DISCOUNT50:
            return 0.5
        elif mode == BotMode.DISCOUNT20:
            return 0.8
        else:
            return 1.0
    
    def is_test_mode(self, mode: Optional[BotMode] = None) -> bool:
        """
        Check if bot is in test mode.
        
        Args:
            mode: BotMode to check (uses current mode if not provided)
        
        Returns:
            True if in test mode
        """
        if mode is None:
            mode = self._cached_mode or BotMode.NORMAL
        return mode == BotMode.TEST
    
    def is_disabled(self, mode: Optional[BotMode] = None) -> bool:
        """
        Check if bot is disabled.
        
        Args:
            mode: BotMode to check (uses current mode if not provided)
        
        Returns:
            True if bot is disabled
        """
        if mode is None:
            mode = self._cached_mode or BotMode.NORMAL
        return mode == BotMode.DISABLED


# Global instance (will be initialized in bot.py)
bot_mode_service: Optional[BotModeService] = None


def get_mode_emoji(mode: BotMode) -> str:
    """Get emoji representation for a mode"""
    emojis = {
        BotMode.NORMAL: "✅",
        BotMode.TEST: "🧪",
        BotMode.DISCOUNT50: "💎",
        BotMode.DISCOUNT20: "💰",
        BotMode.DISABLED: "🔴"
    }
    return emojis.get(mode, "❓")


def get_mode_display_name(mode: BotMode) -> str:
    """Get display name for a mode"""
    names = {
        BotMode.NORMAL: "Нормальный",
        BotMode.TEST: "Тестовый",
        BotMode.DISCOUNT50: "Скидка 50%",
        BotMode.DISCOUNT20: "Скидка 20%",
        BotMode.DISABLED: "Отключён"
    }
    return names.get(mode, "Неизвестно")


def get_mode_description(mode: BotMode) -> str:
    """Get description for a mode"""
    descriptions = {
        BotMode.NORMAL: "Стандартный режим работы бота",
        BotMode.TEST: "Бесплатный доступ для всех пользователей",
        BotMode.DISCOUNT50: "50% скидка на подписки",
        BotMode.DISCOUNT20: "20% скидка на подписки",
        BotMode.DISABLED: "Бот временно недоступен (техническое обслуживание)"
    }
    return descriptions.get(mode, "")
