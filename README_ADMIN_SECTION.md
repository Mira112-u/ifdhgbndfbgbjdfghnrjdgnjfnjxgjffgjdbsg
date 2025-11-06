# Admin System Section for README.md

> This content can be added to the main README.md file

---

## 🔐 Admin Panel

The bot includes a comprehensive role-based admin system for managing users, payments, and system settings.

### Admin Roles

Three levels of admin access with clear hierarchy:

- **Admin** - Full system access (highest privileges)
- **Co-Admin** - Moderate access (user and payment management)
- **RND Admin** - Limited access (read-only analytics)

### Configuration

Add admin user IDs to your `.env` file:

```env
# Full admins (comma-separated Telegram user IDs)
ADMIN_IDS=123456789,987654321

# Co-admins
CO_ADMIN_IDS=111111111,222222222

# R&D admins
RND_ADMIN_IDS=333333333,444444444

# Optional: Log group for admin action logging
LOG_GROUP_ID=-1001234567890
```

### Usage

Send `/admin` to the bot to open the admin panel. You'll see a menu with sections based on your role:

- 📊 **General Information** - System stats and analytics (RND Admin+)
- 👥 **Client Management** - User management and subscriptions (Co-Admin+)
- 💰 **Payments** - Payment history and refunds (Co-Admin+)
- ⚙️ **Settings** - System configuration (Admin only)

### For Developers

Protect admin-only features with the `@require_role` decorator:

```python
from admin_roles import require_role, AdminRole

@router.message(Command("admin_action"))
@require_role(AdminRole.CO)  # Co-Admin or higher
async def admin_action(message: Message):
    # Only Co-Admins and Admins can execute this
    await message.answer("Action completed!")
```

Log admin actions for audit trails:

```python
from admin_logger import log_admin_action

await log_admin_action(
    bot=bot,
    admin_id=admin_id,
    admin_name="Admin Name",
    action="Grant Premium",
    details="30 days premium",
    target_user_id=user_id
)
```

### Documentation

- **[Admin Panel Guide](ADMIN_PANEL.md)** - Complete setup and usage guide
- **[Usage Examples](ADMIN_USAGE_EXAMPLES.md)** - Code examples and patterns
- **[Implementation Details](ADMIN_IMPLEMENTATION_SUMMARY.md)** - Technical documentation

---
