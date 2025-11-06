# subscription_service.py
"""
Subscription service for managing Intellex Premium subscriptions
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from bot_mode_service import BotModeService, BotMode

logger = logging.getLogger(__name__)

# Subscription plan constants
PLAN_1_MONTH = "1_month"
PLAN_3_MONTHS = "3_months"
PLAN_12_MONTHS = "12_months"

# Base prices in TJS (somoni)
BASE_PRICES = {
    PLAN_1_MONTH: 40,
    PLAN_3_MONTHS: 100,
    PLAN_12_MONTHS: 350
}

# Plan durations in days
PLAN_DURATIONS = {
    PLAN_1_MONTH: 30,
    PLAN_3_MONTHS: 90,
    PLAN_12_MONTHS: 365
}

# Plan display names
PLAN_NAMES = {
    PLAN_1_MONTH: "1 месяц",
    PLAN_3_MONTHS: "3 месяца",
    PLAN_12_MONTHS: "12 месяцев"
}


def get_plan_name(plan_id: str) -> str:
    """Get display name for a plan"""
    return PLAN_NAMES.get(plan_id, plan_id)


def get_plan_duration_days(plan_id: str) -> int:
    """Get duration in days for a plan"""
    return PLAN_DURATIONS.get(plan_id, 0)


def calculate_discount_multiplier(mode: BotMode) -> float:
    """
    Calculate discount multiplier based on bot mode
    
    Returns:
        Multiplier to apply to base price (0.5 for 50% off, 0.8 for 20% off, 1.0 for no discount)
    """
    if mode == BotMode.DISCOUNT50:
        return 0.5
    elif mode == BotMode.DISCOUNT20:
        return 0.8
    else:
        return 1.0


def get_discount_percentage(mode: BotMode) -> int:
    """
    Get discount percentage for display
    
    Returns:
        Discount percentage (0, 20, or 50)
    """
    if mode == BotMode.DISCOUNT50:
        return 50
    elif mode == BotMode.DISCOUNT20:
        return 20
    else:
        return 0


async def get_plan_price(plan_id: str, mode_service: Optional[BotModeService] = None) -> Tuple[int, int]:
    """
    Get the price for a subscription plan with current discount applied
    
    Args:
        plan_id: Plan identifier (PLAN_1_MONTH, PLAN_3_MONTHS, or PLAN_12_MONTHS)
        mode_service: Bot mode service instance to check for active discounts
    
    Returns:
        Tuple of (final_price, discount_percentage)
    """
    base_price = BASE_PRICES.get(plan_id, 0)
    
    if not base_price:
        logger.warning(f"Unknown plan ID: {plan_id}")
        return 0, 0
    
    # Get current mode
    current_mode = BotMode.NORMAL
    if mode_service:
        current_mode = await mode_service.get_mode()
    
    # Calculate discount
    multiplier = calculate_discount_multiplier(current_mode)
    discount_pct = get_discount_percentage(current_mode)
    final_price = int(base_price * multiplier)
    
    return final_price, discount_pct


def generate_subscription_payment_url(amount: int, plan_id: str, user_id: int) -> str:
    """
    Generate payment URL for subscription
    
    Args:
        amount: Payment amount in somoni
        plan_id: Plan identifier
        user_id: User ID for reference
    
    Returns:
        Payment URL string
    """
    # Using ExpressPay payment gateway for subscriptions
    # Format: http://pay.expresspay.tj/?A=9762000186477021&s={amount}&c=&f1=133&FIELD2=&FIELD3=
    payment_url = f"http://pay.expresspay.tj/?A=9762000186477021&s={amount}&c=&f1=133&FIELD2=&FIELD3="
    
    return payment_url


def calculate_new_expiry(current_expiry: Optional[datetime], plan_id: str) -> datetime:
    """
    Calculate new subscription expiry date
    
    Args:
        current_expiry: Current expiry datetime (None if no active subscription)
        plan_id: Plan identifier
    
    Returns:
        New expiry datetime
    """
    duration_days = get_plan_duration_days(plan_id)
    
    # If there's an active subscription, extend from expiry date
    # Otherwise, start from now
    if current_expiry and current_expiry > datetime.now():
        start_date = current_expiry
    else:
        start_date = datetime.now()
    
    new_expiry = start_date + timedelta(days=duration_days)
    
    return new_expiry


def format_expiry_date(expiry: datetime) -> str:
    """
    Format expiry date for display
    
    Args:
        expiry: Expiry datetime
    
    Returns:
        Formatted date string (DD.MM.YYYY HH:MM)
    """
    return expiry.strftime("%d.%m.%Y %H:%M")


def get_all_plans() -> list[str]:
    """Get list of all available plan IDs"""
    return [PLAN_1_MONTH, PLAN_3_MONTHS, PLAN_12_MONTHS]
