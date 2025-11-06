# Admin Panel Documentation

## Overview

The admin panel provides a role-based access control system for managing the bot. It includes three levels of admin roles with clear hierarchy and permissions.

## Admin Roles

The system implements three admin roles with clear precedence:

### 1. **Admin** (Highest)
- Full access to all admin features
- Can access all sections including Settings
- Highest privilege level

### 2. **Co-Admin** (Moderate)
- Moderate admin access
- Can access General, Clients, and Payments sections
- Cannot access Settings

### 3. **RND Admin** (Limited)
- Research & Development access
- Can only access General information section
- Limited administrative privileges

## Configuration

Admin roles are configured through environment variables in `.env`:

```env
# Full Admins (highest privileges)
ADMIN_IDS=123456789,987654321

# Co-Admins (moderate privileges)
CO_ADMIN_IDS=111111111,222222222

# R&D Admins (limited privileges)
RND_ADMIN_IDS=333333333,444444444

# Log Group ID for admin action logging (optional)
LOG_GROUP_ID=-1001234567890
```

### Role Precedence

If a user ID appears in multiple role lists, they receive the highest role:
- Admin > Co-Admin > RND Admin

## Features

### 1. Role-Based Access Control

The system automatically determines a user's role based on their Telegram ID:

```python
from admin_roles import get_user_role, AdminRole

role = get_user_role(user_id)
if role >= AdminRole.CO:
    # User is at least a Co-Admin
    pass
```

### 2. Permission Gating

Handlers can be protected with the `@require_role` decorator:

```python
from admin_roles import require_role, AdminRole

@require_role(AdminRole.CO)
async def my_admin_handler(message: Message):
    # Only Co-Admins and above can access this
    pass
```

If a user lacks permissions, they receive a friendly error message.

### 3. Admin Logging

All admin actions can be logged to a configured Telegram group:

```python
from admin_logger import log_admin_action

await log_admin_action(
    bot=bot,
    admin_id=admin_user_id,
    admin_name="John Doe",
    action="Grant Premium",
    details="30 days premium granted",
    target_user_id=target_user_id
)
```

If `LOG_GROUP_ID` is not set, logging silently no-ops.

### 4. Admin Panel Menu

The `/admin` command opens an interactive menu showing only sections the user has access to:

- **📊 General Information** (RND Admin+)
  - View system statistics
  - Monitor bot health
  
- **👥 Client Management** (Co-Admin+)
  - Manage users
  - Handle subscriptions
  
- **💰 Payments** (Co-Admin+)
  - View payment history
  - Process refunds
  
- **⚙️ Settings** (Admin only)
  - Configure system settings
  - Manage bot parameters

## Usage

### For Users

Simply send `/admin` to the bot. The system will:
1. Verify your admin role
2. Fetch your user record from the database
3. Display a menu with sections you have access to
4. Log your access to the admin panel

### For Developers

#### Protecting Routes

Use the `@require_role` decorator on any handler that requires admin access:

```python
from aiogram import Router
from aiogram.filters import Command
from admin_roles import require_role, AdminRole

admin_router = Router()

@admin_router.message(Command("sensitive_action"))
@require_role(AdminRole.ADMIN)
async def handle_sensitive_action(message: Message):
    # Only full admins can execute this
    await message.answer("Action executed!")
```

#### Checking Roles Manually

```python
from admin_roles import get_user_role, has_role, is_admin, AdminRole

user_id = message.from_user.id

# Get exact role
role = get_user_role(user_id)

# Check if user has at least a certain role
if has_role(user_id, AdminRole.CO):
    # User is at least Co-Admin
    pass

# Check if user has any admin role
if is_admin(user_id):
    # User is some kind of admin
    pass
```

#### Adding New Sections

To add a new admin section:

1. Define the section in `admin_panel.py`:
```python
class AdminSection:
    GENERAL = "general"
    CLIENTS = "clients"
    PAYMENTS = "payments"
    SETTINGS = "settings"
    MY_NEW_SECTION = "my_new_section"  # Add here
```

2. Update permissions:
```python
def get_section_permissions() -> dict[str, AdminRole]:
    return {
        # ... existing sections ...
        AdminSection.MY_NEW_SECTION: AdminRole.CO,  # Add permission
    }
```

3. Add emoji and name:
```python
def get_section_emoji(section: str) -> str:
    emojis = {
        # ... existing emojis ...
        AdminSection.MY_NEW_SECTION: "🎯",
    }
    return emojis.get(section, "📁")

def get_section_name(section: str) -> str:
    names = {
        # ... existing names ...
        AdminSection.MY_NEW_SECTION: "My New Section",
    }
    return names.get(section, section.title())
```

4. Create a handler:
```python
@admin_router.callback_query(F.data.startswith("admin_mynewsection:"))
@require_role(AdminRole.CO)
async def handle_my_new_section(callback: CallbackQuery):
    # Your implementation here
    pass
```

## Architecture

### File Structure

- `admin_roles.py` - Role definitions, configuration loading, and permission checks
- `admin_logger.py` - Centralized logging for admin actions
- `admin_panel.py` - Main admin panel router with UI and navigation handlers
- `bot.py` - Main bot file (imports and registers admin router)

### Integration

The admin panel is integrated into the main bot via:

```python
from admin_panel import admin_router, set_admin_dependencies

# In main():
await database.init_db()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Set up admin dependencies
set_admin_dependencies(database, bot)

# Register admin router
dp.include_router(admin_router)
```

## Security Considerations

1. **Environment Variables**: Keep `.env` secure and never commit it to version control
2. **Role Hierarchy**: Always use the highest role a user has (already implemented)
3. **Permission Checks**: Always verify permissions before executing sensitive actions
4. **Logging**: All admin actions should be logged for audit trails
5. **Error Messages**: Friendly error messages that don't reveal system internals

## Future Enhancements

The current implementation provides a scaffold with placeholder handlers. Future tasks will wire up:

1. **General Section**
   - System statistics
   - User analytics
   - Bot health monitoring

2. **Clients Section**
   - User search and management
   - Subscription management
   - User blocking/unblocking

3. **Payments Section**
   - Payment history
   - Refund processing
   - Revenue analytics

4. **Settings Section**
   - Bot configuration
   - Feature toggles
   - System parameters

## Testing

To test the admin panel:

1. Add your Telegram ID to the appropriate role in `.env`
2. Start the bot: `python3 bot.py`
3. Send `/admin` to the bot
4. Navigate through the available sections
5. Check the log group (if configured) for action logs

## Troubleshooting

### "Access Denied" Error

- Verify your Telegram ID is in one of the admin lists in `.env`
- Check that `.env` is loaded correctly
- Ensure the format is correct (comma-separated numbers, no spaces)

### Admin Panel Not Responding

- Check bot logs for errors
- Verify admin router is registered in `bot.py`
- Ensure `set_admin_dependencies()` is called before router registration

### Log Messages Not Appearing

- Verify `LOG_GROUP_ID` is set correctly in `.env`
- Ensure the bot is a member of the log group
- Check that the bot has permission to send messages in the group
