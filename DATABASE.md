# Database Documentation

## Overview

The bot uses SQLAlchemy 2.x with async support for database operations. By default, it uses SQLite with the aiosqlite driver, but can be configured to use PostgreSQL, MySQL, or other databases.

## Configuration

### Environment Variables

Set the `DATABASE_URL` environment variable in your `.env` file:

```bash
# SQLite (default)
DATABASE_URL=sqlite+aiosqlite:///bot_data.db

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:password@localhost/dbname

# MySQL
DATABASE_URL=mysql+aiomysql://user:password@localhost/dbname
```

If `DATABASE_URL` is not set, the bot will default to `sqlite+aiosqlite:///bot_data.db`.

## Database Models

The database contains the following tables:

### Users (`users`)
Stores user information and premium status.
- `user_id` (int, primary key): Telegram user ID
- `username` (str, nullable): Telegram username
- `first_name` (str, nullable): User's first name
- `last_name` (str, nullable): User's last name
- `is_blocked` (bool): Whether user is blocked
- `is_premium` (bool): Whether user has premium subscription
- `premium_expires_at` (datetime, nullable): Premium expiration timestamp
- `created_at` (datetime): User creation timestamp
- `updated_at` (datetime): Last update timestamp

### Subscriptions (`subscriptions`)
Tracks subscription history (for future payment features).
- `id` (int, primary key, auto-increment)
- `user_id` (int, indexed): User ID
- `subscription_type` (str): Type of subscription (e.g., "premium", "basic")
- `starts_at` (datetime): Subscription start date
- `expires_at` (datetime): Subscription expiration date
- `is_active` (bool): Whether subscription is currently active
- `payment_amount` (str, nullable): Payment amount
- `payment_method` (str, nullable): Payment method used
- `created_at` (datetime): Record creation timestamp
- `updated_at` (datetime): Last update timestamp

### Vehicle Bindings (`vehicle_bindings`)
Stores vehicle plate bindings for premium users.
- `id` (int, primary key, auto-increment)
- `user_id` (int, unique, indexed): User ID (one binding per user)
- `plate_number` (str): Vehicle plate number
- `subscription_expires_at` (datetime): Binding expiration timestamp
- `tracked_orders` (text, nullable): JSON array of tracked order numbers for monitoring
- `created_at` (datetime): Binding creation timestamp

### Fine Orders (`fine_orders`)
Stores detected fines for monitoring and notifications.
- `id` (int, primary key, auto-increment)
- `order_number` (str, indexed): Fine order number
- `user_id` (int, indexed): User ID
- `plate_number` (str): Vehicle plate number
- `violation_type` (str): Type of violation
- `violation_date` (str): Date of violation
- `amount` (str): Fine amount
- `outstanding_info` (str, nullable): Outstanding payment info
- `media_links` (json, nullable): Links to violation media files
- `notified` (bool): Whether user was notified
- `created_at` (datetime): Record creation timestamp
- `updated_at` (datetime): Last update timestamp

### Payment Requests (`payment_requests`)
Tracks payment requests and their status (for future payment integration).
- `id` (int, primary key, auto-increment)
- `user_id` (int, indexed): User ID
- `order_number` (str, nullable): Related fine order number
- `payment_type` (str): Type of payment ("fine", "subscription", etc.)
- `amount` (str): Payment amount
- `status` (str): Payment status ("pending", "completed", "failed")
- `payment_url` (str, nullable): Payment URL
- `payment_method` (str, nullable): Payment method
- `metadata` (json, nullable): Additional payment metadata
- `created_at` (datetime): Record creation timestamp
- `updated_at` (datetime): Last update timestamp

### Daily Usage (`daily_usage`)
Tracks daily API usage for quota enforcement.
- `id` (int, primary key, auto-increment)
- `user_id` (int, indexed): User ID
- `usage_date` (date, indexed): Usage date
- `request_count` (int): Number of requests made
- `created_at` (datetime): Record creation timestamp
- `updated_at` (datetime): Last update timestamp

### Bot Settings (`bot_settings`)
Stores configurable bot settings.
- `id` (int, primary key, auto-increment)
- `key` (str, unique, indexed): Setting key
- `value` (str, nullable): Setting value (stored as string)
- `value_type` (str): Type of value ("string", "int", "bool", "json")
- `description` (str, nullable): Setting description
- `created_at` (datetime): Record creation timestamp
- `updated_at` (datetime): Last update timestamp

### Admin Action Logs (`admin_action_logs`)
Logs admin actions for auditing.
- `id` (int, primary key, auto-increment)
- `admin_user_id` (int, indexed): Admin user ID who performed action
- `action_type` (str, indexed): Type of action performed
- `target_user_id` (int, nullable, indexed): Target user ID (if applicable)
- `action_details` (json, nullable): Additional action details
- `created_at` (datetime, indexed): Action timestamp

## Migration Strategy

### Current Approach: Auto-create Tables

The bot currently uses SQLAlchemy's `create_all()` method to automatically create tables at startup. This is suitable for development and small deployments.

**Pros:**
- Simple and automatic
- No manual migration steps required
- Good for development and testing

**Cons:**
- Cannot handle schema changes in production
- No migration history
- Cannot roll back changes

### Future: Alembic Migrations

For production deployments with schema changes, we recommend implementing Alembic migrations:

1. **Install Alembic:**
   ```bash
   pip install alembic
   ```

2. **Initialize Alembic:**
   ```bash
   alembic init alembic
   ```

3. **Configure Alembic:**
   Edit `alembic.ini` to set your database URL or use environment variables.

4. **Create Migrations:**
   ```bash
   alembic revision --autogenerate -m "Description of changes"
   ```

5. **Apply Migrations:**
   ```bash
   alembic upgrade head
   ```

6. **Rollback (if needed):**
   ```bash
   alembic downgrade -1
   ```

## Repository Methods

The `Database` class provides async repository methods for common operations:

### User Management
- `get_or_create_user(user_id, username, first_name, last_name)`: Get or create user
- `update_user_premium(user_id, is_premium, expires_at)`: Update premium status
- `is_user_blocked(user_id)`: Check if user is blocked
- `is_user_premium(user_id)`: Check if user has active premium
- `block_user(user_id)`: Block a user
- `unblock_user(user_id)`: Unblock a user

### Vehicle Bindings
- `get_vehicle_binding(user_id)`: Get active vehicle binding
- `set_vehicle_binding(user_id, plate_number, expires_at)`: Set/update binding (returns binding_id)
- `add_premium_binding(user_id, plate_number, expires_at)`: Alias for set_vehicle_binding
- `remove_vehicle_binding(user_id)`: Remove binding
- `get_active_premium_bindings()`: Get all active bindings (for monitoring)
- `remove_expired_bindings()`: Remove expired bindings
- `update_tracked_orders(binding_id, order_numbers)`: Update tracked order numbers for a binding
- `get_tracked_orders(binding_id)`: Get tracked order numbers for a binding

### Fine Orders
- `get_fine_order(order_number, user_id)`: Get specific fine order
- `add_or_update_fine_order(...)`: Add or update fine order
- `mark_order_notified(order_number, user_id)`: Mark order as notified

### Daily Usage
- `get_daily_usage(user_id)`: Get today's request count
- `increment_daily_usage(user_id)`: Increment request count
- `reset_daily_usage(user_id)`: Reset usage (admin)

### Bot Settings
- `get_setting(key)`: Get a setting value
- `set_setting(key, value, value_type, description)`: Set/update setting

### Admin Logs
- `log_admin_action(admin_user_id, action_type, target_user_id, action_details)`: Log action
- `get_admin_actions(admin_user_id, action_type, limit)`: Get action logs

## Backup and Restore

### SQLite Backup
```bash
# Backup
cp bot_data.db bot_data_backup_$(date +%Y%m%d).db

# Restore
cp bot_data_backup_YYYYMMDD.db bot_data.db
```

### PostgreSQL Backup
```bash
# Backup
pg_dump -U user dbname > backup.sql

# Restore
psql -U user dbname < backup.sql
```

## Performance Considerations

1. **Connection Pooling**: The async engine uses connection pooling by default
2. **Indexes**: Key fields are indexed for faster queries
3. **Session Management**: Uses context managers for proper session handling
4. **Error Handling**: All database operations include error handling and rollback

## Troubleshooting

### Database Locked (SQLite)
If you encounter "database is locked" errors with SQLite:
- Ensure only one bot instance is running
- Consider using PostgreSQL for production
- Check for long-running transactions

### Connection Issues
- Verify DATABASE_URL is correct
- Check database server is running (for PostgreSQL/MySQL)
- Ensure required drivers are installed (aiosqlite, asyncpg, aiomysql)

### Migration Conflicts
- Back up your database before migrations
- Test migrations in a staging environment
- Use Alembic for version control in production
