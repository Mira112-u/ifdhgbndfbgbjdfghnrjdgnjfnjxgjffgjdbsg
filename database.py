# database.py
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import re
import json

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, JSON, select, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all ORM models"""
    pass


class User(Base):
    __tablename__ = "users"
    
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    premium_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_blocked": self.is_blocked,
            "is_premium": self.is_premium,
            "premium_expires_at": self.premium_expires_at.isoformat() if self.premium_expires_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    subscription_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., "premium", "basic"
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payment_amount: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    payment_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class VehicleBinding(Base):
    __tablename__ = "vehicle_bindings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False)
    subscription_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tracked_orders: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class FineOrder(Base):
    __tablename__ = "fine_orders"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False)
    violation_type: Mapped[str] = mapped_column(Text, nullable=False)
    violation_date: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[str] = mapped_column(String(50), nullable=False)
    outstanding_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_links: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    order_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # fine order or subscription
    payment_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "fine", "subscription", etc.
    amount: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, completed, failed
    payment_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payment_metadata: Mapped[Optional[Dict]] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class BotSetting(Base):
    __tablename__ = "bot_settings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_type: Mapped[str] = mapped_column(String(50), default="string", nullable=False)  # string, int, bool, json
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class AdminActionLog(Base):
    __tablename__ = "admin_action_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action_details: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False, index=True)


class Database:
    """Async database manager with repository methods"""
    
    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize async database connection
        
        Args:
            database_url: SQLAlchemy database URL. Defaults to sqlite+aiosqlite:///bot_data.db
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_data.db")
        
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        logger.info(f"Database initialized with URL: {database_url}")
        
        # Statistics cache
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_expiration: Optional[datetime] = None
    
    @asynccontextmanager
    async def get_session(self):
        """Get an async database session"""
        async with self.async_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    
    async def init_db(self):
        """Initialize database tables"""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables initialized successfully")
        except SQLAlchemyError as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    async def close(self):
        """Close database connection"""
        await self.engine.dispose()
        logger.info("Database connection closed")
    
    # User methods
    async def get_or_create_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get user or create if doesn't exist"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                return user.to_dict()
            
            # Create new user
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            session.add(user)
            await session.flush()
            
            logger.info(f"Created new user: {user_id}")
            return user.to_dict()
    
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID without creating"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            return user.to_dict() if user else None
    
    async def search_users(
        self,
        search_query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Search users by Telegram ID or username with pagination.
        Returns (users_list, total_count).
        """
        async with self.get_session() as session:
            # Build base query
            query = select(User)
            count_query = select(func.count()).select_from(User)
            
            # Apply search filter if provided
            if search_query:
                search_query = search_query.strip()
                # Try to parse as integer for user_id search
                if search_query.isdigit():
                    user_id_search = int(search_query)
                    query = query.where(User.user_id == user_id_search)
                    count_query = count_query.where(User.user_id == user_id_search)
                else:
                    # Search by username (partial match, case-insensitive)
                    search_pattern = f"%{search_query}%"
                    query = query.where(User.username.ilike(search_pattern))
                    count_query = count_query.where(User.username.ilike(search_pattern))
            
            # Get total count
            total_result = await session.execute(count_query)
            total_count = total_result.scalar()
            
            # Apply ordering and pagination
            query = query.order_by(User.created_at.desc()).limit(limit).offset(offset)
            
            # Execute query
            result = await session.execute(query)
            users = result.scalars().all()
            
            return [user.to_dict() for user in users], total_count
    
    async def update_user_premium(
        self,
        user_id: int,
        is_premium: bool,
        expires_at: Optional[datetime] = None
    ):
        """Update user premium status"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.is_premium = is_premium
                user.premium_expires_at = expires_at
                user.updated_at = datetime.now()
                logger.info(f"Updated premium status for user {user_id}: {is_premium}")
    
    async def is_user_blocked(self, user_id: int) -> bool:
        """Check if user is blocked"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User.is_blocked).where(User.user_id == user_id)
            )
            is_blocked = result.scalar_one_or_none()
            return bool(is_blocked) if is_blocked is not None else False
    
    async def is_user_premium(self, user_id: int) -> bool:
        """Check if user has active premium subscription"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user and user.is_premium:
                # Check if premium hasn't expired
                if user.premium_expires_at and datetime.now() > user.premium_expires_at:
                    # Premium expired, update user
                    user.is_premium = False
                    user.premium_expires_at = None
                    user.updated_at = datetime.now()
                    logger.info(f"Premium expired for user {user_id}, status updated")
                    return False
                return True
        
        # If user record doesn't show premium, check active subscriptions
        subscription = await self.get_active_subscription(user_id)
        if subscription:
            try:
                expires_at = datetime.fromisoformat(subscription["expires_at"])
            except (KeyError, ValueError, TypeError):
                expires_at = datetime.now()
            await self.update_user_premium(user_id, True, expires_at)
            return True
        
        return False
    
    async def block_user(self, user_id: int):
        """Block a user"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.is_blocked = True
                user.updated_at = datetime.now()
                logger.info(f"Blocked user {user_id}")
    
    async def unblock_user(self, user_id: int):
        """Unblock a user"""
        async with self.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.is_blocked = False
                user.updated_at = datetime.now()
                logger.info(f"Unblocked user {user_id}")
    
    # Vehicle binding methods
    async def get_vehicle_binding(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's vehicle binding if exists and not expired"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding).where(
                    VehicleBinding.user_id == user_id,
                    VehicleBinding.subscription_expires_at > datetime.now()
                )
            )
            binding = result.scalar_one_or_none()
            
            if binding:
                return {
                    "id": binding.id,
                    "plate_number": binding.plate_number,
                    "subscription_expires_at": binding.subscription_expires_at.isoformat(),
                    "tracked_orders": binding.tracked_orders,
                    "created_at": binding.created_at.isoformat()
                }
            return None
    
    async def set_vehicle_binding(
        self,
        user_id: int,
        plate_number: str,
        expires_at: datetime
    ):
        """Set or replace vehicle binding for a user"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding).where(VehicleBinding.user_id == user_id)
            )
            binding = result.scalar_one_or_none()
            
            normalized_plate = plate_number.upper()
            if binding:
                if binding.plate_number != normalized_plate:
                    binding.tracked_orders = None
                binding.plate_number = normalized_plate
                binding.subscription_expires_at = expires_at
            else:
                binding = VehicleBinding(
                    user_id=user_id,
                    plate_number=normalized_plate,
                    subscription_expires_at=expires_at,
                    tracked_orders=None
                )
                session.add(binding)
            
            await session.flush()
            logger.info(f"Set vehicle binding: user_id={user_id}, plate={plate_number}")
            return binding.id
    
    async def add_premium_binding(
        self,
        user_id: int,
        plate_number: str,
        expires_at: datetime
    ):
        """Add or update a premium binding (alias for set_vehicle_binding)"""
        return await self.set_vehicle_binding(user_id, plate_number, expires_at)
    
    async def remove_vehicle_binding(self, user_id: int):
        """Remove vehicle binding for a user"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding).where(VehicleBinding.user_id == user_id)
            )
            binding = result.scalar_one_or_none()
            
            if binding:
                await session.delete(binding)
                logger.info(f"Removed vehicle binding for user {user_id}")
    
    async def get_active_premium_bindings(self) -> List[Dict[str, Any]]:
        """Get all active premium bindings (not expired) - for monitor"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding, User).join(
                    User, VehicleBinding.user_id == User.user_id
                ).where(
                    VehicleBinding.subscription_expires_at > datetime.now(),
                    User.is_blocked == False
                )
            )
            
            bindings = []
            for binding, user in result:
                try:
                    tracked_orders = json.loads(binding.tracked_orders) if binding.tracked_orders else []
                    tracked_initialized = binding.tracked_orders is not None
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to decode tracked orders for binding %s. Resetting.",
                        binding.id
                    )
                    tracked_orders = []
                    tracked_initialized = False
                bindings.append({
                    "binding_id": binding.id,
                    "user_id": binding.user_id,
                    "plate_number": binding.plate_number,
                    "subscription_expires_at": binding.subscription_expires_at.isoformat(),
                    "tracked_orders": tracked_orders,
                    "tracked_initialized": tracked_initialized
                })
            
            return bindings
    
    async def remove_expired_bindings(self) -> int:
        """Remove expired premium bindings"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding).where(
                    VehicleBinding.subscription_expires_at <= datetime.now()
                )
            )
            expired_bindings = result.scalars().all()
            
            count = len(expired_bindings)
            for binding in expired_bindings:
                await session.delete(binding)
            
            if count > 0:
                logger.info(f"Removed {count} expired premium bindings")
            
            return count
    
    async def update_tracked_orders(self, binding_id: int, order_numbers: List[str]):
        """Update the list of tracked orders for a vehicle binding"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding).where(VehicleBinding.id == binding_id)
            )
            binding = result.scalar_one_or_none()
            
            if binding:
                binding.tracked_orders = json.dumps(order_numbers)
                logger.info(f"Updated tracked orders for binding {binding_id}: {len(order_numbers)} orders")
    
    async def get_tracked_orders(self, binding_id: int) -> List[str]:
        """Get the list of tracked orders for a vehicle binding"""
        async with self.get_session() as session:
            result = await session.execute(
                select(VehicleBinding.tracked_orders).where(VehicleBinding.id == binding_id)
            )
            tracked_orders_json = result.scalar_one_or_none()
            
            if tracked_orders_json:
                try:
                    return json.loads(tracked_orders_json)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode tracked orders for binding {binding_id}")
                    return []
            return []
    
    # Fine order methods
    async def get_fine_order(
        self,
        order_number: str,
        user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific fine order"""
        async with self.get_session() as session:
            result = await session.execute(
                select(FineOrder).where(
                    FineOrder.order_number == order_number,
                    FineOrder.user_id == user_id
                )
            )
            order = result.scalar_one_or_none()
            
            if order:
                return {
                    "id": order.id,
                    "order_number": order.order_number,
                    "user_id": order.user_id,
                    "plate_number": order.plate_number,
                    "violation_type": order.violation_type,
                    "violation_date": order.violation_date,
                    "amount": order.amount,
                    "outstanding_info": order.outstanding_info,
                    "media_links": order.media_links or {},
                    "notified": order.notified,
                    "created_at": order.created_at.isoformat(),
                    "updated_at": order.updated_at.isoformat()
                }
            return None
    
    async def add_or_update_fine_order(
        self,
        order_number: str,
        user_id: int,
        plate_number: str,
        violation_type: str,
        violation_date: str,
        amount: str,
        outstanding_info: Optional[str],
        media_links: Dict[str, Any]
    ) -> bool:
        """
        Add or update a fine order
        Returns True if it's a new order, False if it's an update
        """
        existing = await self.get_fine_order(order_number, user_id)
        
        async with self.get_session() as session:
            if existing:
                # Update existing order
                result = await session.execute(
                    select(FineOrder).where(
                        FineOrder.order_number == order_number,
                        FineOrder.user_id == user_id
                    )
                )
                order = result.scalar_one()
                order.outstanding_info = outstanding_info
                order.updated_at = datetime.now()
                
                logger.info(f"Updated fine order: {order_number} for user {user_id}")
                return False
            else:
                # Insert new order
                order = FineOrder(
                    order_number=order_number,
                    user_id=user_id,
                    plate_number=plate_number.upper(),
                    violation_type=violation_type,
                    violation_date=violation_date,
                    amount=amount,
                    outstanding_info=outstanding_info,
                    media_links=media_links,
                    notified=False
                )
                session.add(order)
                
                logger.info(f"Added new fine order: {order_number} for user {user_id}")
                return True
    
    async def mark_order_notified(self, order_number: str, user_id: int):
        """Mark a fine order as notified"""
        async with self.get_session() as session:
            result = await session.execute(
                select(FineOrder).where(
                    FineOrder.order_number == order_number,
                    FineOrder.user_id == user_id
                )
            )
            order = result.scalar_one_or_none()
            
            if order:
                order.notified = True
                order.updated_at = datetime.now()
                logger.info(f"Marked order {order_number} as notified for user {user_id}")
    
    # Daily usage methods
    async def get_daily_usage(self, user_id: int) -> int:
        """Get today's request count for a user"""
        today = date.today()
        
        async with self.get_session() as session:
            result = await session.execute(
                select(DailyUsage.request_count).where(
                    DailyUsage.user_id == user_id,
                    DailyUsage.usage_date == today
                )
            )
            count = result.scalar_one_or_none()
            return count if count is not None else 0
    
    async def increment_daily_usage(self, user_id: int):
        """Increment today's request count for a user"""
        today = date.today()
        
        async with self.get_session() as session:
            result = await session.execute(
                select(DailyUsage).where(
                    DailyUsage.user_id == user_id,
                    DailyUsage.usage_date == today
                )
            )
            usage = result.scalar_one_or_none()
            
            if usage:
                usage.request_count += 1
                usage.updated_at = datetime.now()
            else:
                usage = DailyUsage(
                    user_id=user_id,
                    usage_date=today,
                    request_count=1
                )
                session.add(usage)
    
    async def reset_daily_usage(self, user_id: int):
        """Reset daily usage for a user (admin command)"""
        today = date.today()
        
        async with self.get_session() as session:
            result = await session.execute(
                select(DailyUsage).where(
                    DailyUsage.user_id == user_id,
                    DailyUsage.usage_date == today
                )
            )
            usage = result.scalar_one_or_none()
            
            if usage:
                await session.delete(usage)
                logger.info(f"Reset daily usage for user {user_id}")
    
    # Bot settings methods
    async def get_setting(self, key: str) -> Optional[Any]:
        """Get a bot setting by key"""
        async with self.get_session() as session:
            result = await session.execute(
                select(BotSetting).where(BotSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            
            if setting:
                # Parse value based on type
                if setting.value_type == "int":
                    return int(setting.value) if setting.value else None
                elif setting.value_type == "bool":
                    return setting.value.lower() == "true" if setting.value else False
                elif setting.value_type == "json":
                    import json
                    return json.loads(setting.value) if setting.value else None
                else:
                    return setting.value
            return None
    
    async def set_setting(
        self,
        key: str,
        value: Any,
        value_type: str = "string",
        description: Optional[str] = None
    ):
        """Set or update a bot setting"""
        async with self.get_session() as session:
            result = await session.execute(
                select(BotSetting).where(BotSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            
            # Convert value to string
            if value_type == "json":
                import json
                value_str = json.dumps(value)
            else:
                value_str = str(value)
            
            if setting:
                setting.value = value_str
                setting.value_type = value_type
                if description:
                    setting.description = description
                setting.updated_at = datetime.now()
            else:
                setting = BotSetting(
                    key=key,
                    value=value_str,
                    value_type=value_type,
                    description=description
                )
                session.add(setting)
            
            logger.info(f"Set bot setting: {key} = {value_str}")
    
    # Admin action log methods
    async def log_admin_action(
        self,
        admin_user_id: int,
        action_type: str,
        target_user_id: Optional[int] = None,
        action_details: Optional[Dict[str, Any]] = None
    ):
        """Log an admin action"""
        async with self.get_session() as session:
            log = AdminActionLog(
                admin_user_id=admin_user_id,
                action_type=action_type,
                target_user_id=target_user_id,
                action_details=action_details
            )
            session.add(log)
            logger.info(f"Logged admin action: {action_type} by {admin_user_id}")
    
    async def get_admin_actions(
        self,
        admin_user_id: Optional[int] = None,
        action_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get admin action logs with optional filters"""
        async with self.get_session() as session:
            query = select(AdminActionLog)
            
            if admin_user_id:
                query = query.where(AdminActionLog.admin_user_id == admin_user_id)
            if action_type:
                query = query.where(AdminActionLog.action_type == action_type)
            
            query = query.order_by(AdminActionLog.created_at.desc()).limit(limit)
            
            result = await session.execute(query)
            logs = result.scalars().all()
            
            return [
                {
                    "id": log.id,
                    "admin_user_id": log.admin_user_id,
                    "action_type": log.action_type,
                    "target_user_id": log.target_user_id,
                    "action_details": log.action_details,
                    "created_at": log.created_at.isoformat()
                }
                for log in logs
            ]
    
    # Payment request methods
    async def create_payment_request(
        self,
        user_id: int,
        payment_type: str,
        amount: str,
        payment_url: Optional[str] = None,
        order_number: Optional[str] = None,
        payment_metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Create a new payment request and return its ID"""
        async with self.get_session() as session:
            request = PaymentRequest(
                user_id=user_id,
                payment_type=payment_type,
                amount=amount,
                status="pending",
                payment_url=payment_url,
                order_number=order_number,
                payment_metadata=payment_metadata
            )
            session.add(request)
            await session.flush()
            logger.info(f"Created payment request {request.id} for user {user_id}: {payment_type} - {amount}")
            return request.id
    
    async def get_payment_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Get payment request by ID"""
        async with self.get_session() as session:
            result = await session.execute(
                select(PaymentRequest).where(PaymentRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            
            if request:
                return {
                    "id": request.id,
                    "user_id": request.user_id,
                    "order_number": request.order_number,
                    "payment_type": request.payment_type,
                    "amount": request.amount,
                    "status": request.status,
                    "payment_url": request.payment_url,
                    "payment_method": request.payment_method,
                    "payment_metadata": request.payment_metadata,
                    "created_at": request.created_at.isoformat(),
                    "updated_at": request.updated_at.isoformat()
                }
            return None
    
    async def update_payment_request_status(
        self,
        request_id: int,
        status: str,
        payment_method: Optional[str] = None
    ):
        """Update payment request status"""
        async with self.get_session() as session:
            result = await session.execute(
                select(PaymentRequest).where(PaymentRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            
            if request:
                request.status = status
                if payment_method:
                    request.payment_method = payment_method
                request.updated_at = datetime.now()
                logger.info(f"Updated payment request {request_id} status to {status}")
    
    async def get_pending_payment_requests(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all payment requests awaiting approval"""
        async with self.get_session() as session:
            result = await session.execute(
                select(PaymentRequest).where(
                    PaymentRequest.status == "awaiting_approval"
                ).order_by(PaymentRequest.created_at.desc()).limit(limit)
            )
            requests = result.scalars().all()
            
            return [
                {
                    "id": req.id,
                    "user_id": req.user_id,
                    "order_number": req.order_number,
                    "payment_type": req.payment_type,
                    "amount": req.amount,
                    "status": req.status,
                    "payment_url": req.payment_url,
                    "payment_method": req.payment_method,
                    "payment_metadata": req.payment_metadata,
                    "created_at": req.created_at.isoformat(),
                    "updated_at": req.updated_at.isoformat()
                }
                for req in requests
            ]
    
    # Subscription methods
    async def create_subscription(
        self,
        user_id: int,
        subscription_type: str,
        starts_at: datetime,
        expires_at: datetime,
        payment_amount: Optional[str] = None,
        payment_method: Optional[str] = None
    ) -> int:
        """Create a new subscription record"""
        async with self.get_session() as session:
            subscription = Subscription(
                user_id=user_id,
                subscription_type=subscription_type,
                starts_at=starts_at,
                expires_at=expires_at,
                is_active=True,
                payment_amount=payment_amount,
                payment_method=payment_method
            )
            session.add(subscription)
            await session.flush()
            logger.info(f"Created subscription {subscription.id} for user {user_id}")
            return subscription.id
    
    async def get_active_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get active subscription for a user"""
        async with self.get_session() as session:
            result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.is_active == True,
                    Subscription.expires_at > datetime.now()
                ).order_by(Subscription.expires_at.desc()).limit(1)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                return {
                    "id": subscription.id,
                    "user_id": subscription.user_id,
                    "subscription_type": subscription.subscription_type,
                    "starts_at": subscription.starts_at.isoformat(),
                    "expires_at": subscription.expires_at.isoformat(),
                    "is_active": subscription.is_active,
                    "payment_amount": subscription.payment_amount,
                    "payment_method": subscription.payment_method,
                    "created_at": subscription.created_at.isoformat()
                }
            return None
    
    async def create_or_update_subscription(
        self,
        user_id: int,
        plan_id: str,
        expires_at: datetime,
        payment_amount: Optional[str] = None,
        payment_method: Optional[str] = None
    ) -> int:
        """
        Create or update subscription for a user.
        If an active subscription exists, deactivate it and create a new one.
        
        Args:
            user_id: User's Telegram ID
            plan_id: Subscription plan identifier (e.g., "1_month", "3_months", "12_months")
            expires_at: Expiration datetime for the subscription
            payment_amount: Payment amount (optional)
            payment_method: Payment method (optional)
        
        Returns:
            ID of the created subscription
        """
        async with self.get_session() as session:
            # Deactivate any existing active subscriptions
            result = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.is_active == True
                )
            )
            existing_subscriptions = result.scalars().all()
            
            for sub in existing_subscriptions:
                sub.is_active = False
                sub.updated_at = datetime.now()
            
            # Create new subscription
            subscription = Subscription(
                user_id=user_id,
                subscription_type=plan_id,
                starts_at=datetime.now(),
                expires_at=expires_at,
                is_active=True,
                payment_amount=payment_amount,
                payment_method=payment_method
            )
            session.add(subscription)
            await session.flush()
            
            logger.info(f"Created/updated subscription {subscription.id} for user {user_id}, plan: {plan_id}")
            return subscription.id
    
    # Statistics methods
    async def get_statistics(self) -> Dict[str, Any]:
        """
        Get comprehensive bot statistics.
        Returns statistics including users, subscriptions, payments, and activity.
        Cached for 5 minutes to reduce database load.
        """
        # Check cache
        now = datetime.now()
        if self._stats_cache and self._stats_cache_expiration and now < self._stats_cache_expiration:
            return self._stats_cache
        
        async with self.get_session() as session:
            stats = {}
            
            # Total users count
            result = await session.execute(select(func.count()).select_from(User))
            stats['total_users'] = int(result.scalar() or 0)
            
            # Premium users count (only active premiums)
            result = await session.execute(
                select(func.count()).select_from(User).where(
                    User.is_premium == True,
                    User.premium_expires_at != None,
                    User.premium_expires_at > datetime.now()
                )
            )
            stats['premium_users'] = int(result.scalar() or 0)
            
            # Regular users
            stats['regular_users'] = max(stats['total_users'] - stats['premium_users'], 0)
            
            # Active subscriptions
            result = await session.execute(
                select(func.count()).select_from(Subscription).where(
                    Subscription.is_active == True,
                    Subscription.expires_at > datetime.now()
                )
            )
            stats['active_subscriptions'] = int(result.scalar() or 0)
            
            # Today's statistics
            today = date.today()
            today_start = datetime.combine(today, datetime.min.time())
            
            # New users today
            result = await session.execute(
                select(func.count()).select_from(User).where(
                    User.created_at >= today_start
                )
            )
            stats['new_users_today'] = int(result.scalar() or 0)
            
            # Requests today
            result = await session.execute(
                select(func.sum(DailyUsage.request_count)).where(
                    DailyUsage.usage_date == today
                )
            )
            stats['requests_today'] = int(result.scalar() or 0)
            
            # New subscriptions today
            result = await session.execute(
                select(func.count()).select_from(Subscription).where(
                    Subscription.created_at >= today_start
                )
            )
            stats['subscriptions_today'] = int(result.scalar() or 0)
            
            # Finance statistics
            def parse_amount(value: Optional[str]) -> float:
                if not value:
                    return 0.0
                normalized = re.sub(r'[^0-9.,]', '', value)
                if not normalized:
                    return 0.0
                normalized = normalized.replace(',', '.')
                try:
                    return float(normalized)
                except ValueError:
                    return 0.0
            
            # Total completed payments (sum and count) - including all non-pending statuses
            result = await session.execute(
                select(PaymentRequest.amount).where(
                    PaymentRequest.status.in_(["completed", "awaiting_approval", "confirmed"])
                )
            )
            completed_amounts = result.scalars().all()
            stats['total_payments_count'] = len(completed_amounts)
            stats['total_payments_amount'] = round(sum(parse_amount(amount) for amount in completed_amounts), 2)
            
            # Awaiting approval payments (sum and count)
            result = await session.execute(
                select(PaymentRequest.amount).where(
                    PaymentRequest.status == "awaiting_approval"
                )
            )
            pending_amounts = result.scalars().all()
            stats['pending_payments_count'] = len(pending_amounts)
            stats['pending_payments_amount'] = round(sum(parse_amount(amount) for amount in pending_amounts), 2)
            
            # Confirmed today (sum and count)
            result = await session.execute(
                select(PaymentRequest.amount).where(
                    PaymentRequest.status == "completed",
                    PaymentRequest.updated_at >= today_start
                )
            )
            confirmed_today_amounts = result.scalars().all()
            stats['confirmed_payments_today_count'] = len(confirmed_today_amounts)
            stats['confirmed_payments_today_amount'] = round(sum(parse_amount(amount) for amount in confirmed_today_amounts), 2)
            
            # Top 3 active users (by request count today)
            result = await session.execute(
                select(
                    DailyUsage.user_id,
                    DailyUsage.request_count,
                    User.username
                ).join(
                    User, DailyUsage.user_id == User.user_id
                ).where(
                    DailyUsage.usage_date == today
                ).order_by(
                    DailyUsage.request_count.desc()
                ).limit(3)
            )
            top_users = result.all()
            stats['top_users'] = [
                {
                    "user_id": row[0],
                    "requests": row[1],
                    "username": row[2] or "N/A"
                }
                for row in top_users
            ]
            
            # Cache the results for 5 minutes
            self._stats_cache = stats
            self._stats_cache_expiration = now + timedelta(minutes=5)
            
            return stats


# Async initialization helper
async def init_db(database_url: Optional[str] = None) -> Database:
    """
    Initialize database and create tables
    
    Args:
        database_url: SQLAlchemy database URL
    
    Returns:
        Initialized Database instance
    """
    db = Database(database_url)
    await db.init_db()
    return db
