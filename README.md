# Intellex Mobility Bot

A Telegram bot for checking vehicle fines in Tajikistan with premium monitoring features.

## Features

- 🔍 Check vehicle fines by plate number
- 📸 View violation photos and videos
- 💳 Quick payment links for fines
- 🔔 **Premium: Automatic fine monitoring**
- 🔗 **Premium: Vehicle binding with auto-notifications**
- ⏱️ **Daily request quotas** (5 free, 100 premium)
- 🚫 **Admin controls** for user management
- 👤 User profile management

## Premium Features

### Daily Request Quotas

All users have daily limits to prevent abuse:
- **Free Users**: 5 requests per day
- **Premium Users**: 100 requests per day

Quotas automatically reset at midnight. When your quota is exhausted, you'll see a countdown to the next reset.

### Vehicle Binding (Premium Only)

Premium users can bind one vehicle for automatic monitoring:

1. **Activate Premium**: Use `/bind_PLATE_DAYS` or ask an admin
   - Example: `/bind_01ABC123_30` (grants premium + binds plate for 30 days)

2. **Bind via Menu**: Premium users see additional buttons:
   - 🔗 **Привязать машину** - Bind your vehicle
   - ❓ **Что такое привязка машины?** - Learn about vehicle binding

3. **Automatic Monitoring**: Every 30 minutes, the bot checks your vehicle for new fines

4. **Instant Notifications**: When a new fine is detected, you receive:
   - Complete fine details (order number, violation type, date, amount)
   - Vehicle information
   - All available media files (photos/videos)
   - Direct payment link

5. **Smart Detection**: The system distinguishes between:
   - **New fines**: You get notified immediately
   - **Updated fines**: Payment status changes are tracked silently

### Subscription Management

- Premium status is automatically checked on every interaction
- When your subscription expires:
  - Premium status is revoked
  - Vehicle binding is automatically removed
  - You return to free tier (5 requests/day)
  - Premium menu buttons disappear

### Configuration

Edit `.env` file to configure:

```env
# Bot token
BOT_TOKEN=your_bot_token_here

# Admin user IDs (comma-separated)
ADMIN_IDS=123456789,987654321

# Polling interval in seconds (default: 1800 = 30 minutes)
MONITOR_POLL_INTERVAL=1800

# Rate limit delay between requests in seconds (default: 5.0)
MONITOR_RATE_LIMIT=5.0
```

### User Commands

- `/start` - Start the bot and show main menu
- `/bind_PLATE_DAYS` - Bind a plate and get premium (e.g., `/bind_01ABC123_30`)
- `/my_bindings` - Show your active vehicle binding
- 🔗 **Привязать машину** - Premium: Bind/replace vehicle (button)
- ❓ **Что такое привязка машины?** - Learn about binding (button)

### Admin Commands

Restricted to users in `ADMIN_IDS`:

- `/grant_premium USER_ID DAYS` - Grant premium to a user
- `/revoke_premium USER_ID` - Revoke premium and remove binding
- `/block_user USER_ID` - Block a user completely
- `/unblock_user USER_ID` - Unblock a user
- `СНЯТЬ ЛИМИТ USER_ID` - Reset daily quota for a user

### Database

The bot uses SQLite (`bot_data.db`) to store:
- **Users**: Premium status, expiration dates, blocked flags
- **Premium Bindings**: Single vehicle per user with expiration
- **Fine Orders**: Historical fine data for comparison
- **Daily Usage**: Request counts per user per day

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure `.env`:
```bash
cp .env.example .env
# Edit .env and add your BOT_TOKEN
```

3. Run the bot:
```bash
python bot.py
```

## Architecture

- **bot.py**: Main bot logic, handlers, and user interface
- **scraper.py**: RBDA website scraper with session management
- **database.py**: SQLite database operations
- **monitor.py**: Background task for fine monitoring

## Technical Details

### Rate Limiting

To avoid overloading the external service:
- Default delay of 5 seconds between requests
- Configurable via `MONITOR_RATE_LIMIT` environment variable

### Graceful Shutdown

The monitoring task properly shuts down when the bot stops:
- Waits for current check to complete
- Timeout of 10 seconds before force cancellation

### Error Handling

- Session expiration is automatically handled with re-authentication
- Failed checks are logged without stopping the monitoring loop
- Expired subscriptions are automatically removed

### Logging

All monitoring activities are logged with timestamps:
- New fine detections
- Database operations
- API errors
- Notification successes/failures

## Development

The bot is built with:
- **Aiogram 3**: Modern async Telegram bot framework
- **BeautifulSoup**: HTML parsing for scraper
- **SQLite**: Lightweight database for data persistence
- **asyncio**: Asynchronous task management
