# Fix: Payment Request Notifications Not Being Sent

## Problem Description

Payment request notifications were failing to be sent to the log group when users submitted payment confirmations. The error message showed:

```
Error sending log group notification: Telegram server says - Bad Request: can't parse entities: Can't find end of Italic entity at byte offset 138/140
```

## Root Cause

The issue was caused by improper escaping of user data in MarkdownV2 formatted messages. Specifically:

1. **Username field**: If a username contained underscores (e.g., `user_name`), these were interpreted as italic markers in MarkdownV2
2. **Other fields**: Several other dynamic fields like `user_id`, `request_id`, and `discount_pct` were also not properly escaped

In MarkdownV2, underscores (`_`) are used to denote italic text. When a username like `test_user` was inserted without escaping, it would create unclosed italic tags, causing Telegram to reject the message.

## Solution

The fix involved properly escaping all dynamic fields in the notification message using the `escape_markdown()` function:

### Changes in bot.py

1. **Initial payment notification** (lines 844-857):
   - Escaped `user_id` 
   - Escaped `username` (critical fix)
   - Escaped `request_id`
   - Escaped `discount_pct`

2. **Payment approval message** (lines 930-937):
   - Escaped admin identifier (username or first name)

3. **Payment rejection message** (lines 964-971):
   - Escaped admin identifier (username or first name)

## Code Changes

Before:
```python
log_text = (
    "💰 *Новая заявка на оплату подписки*\n\n"
    f"👤 *Пользователь:*\n"
    f"• ID: `{user_id}`\n"
    f"• Username: @{username}\n"  # ❌ Not escaped!
    f"• Имя: {escape_markdown(full_name)}\n\n"
    f"💎 *План:* {escape_markdown(plan_name)}\n"
    f"💰 *Сумма:* {escape_markdown(amount)} смн"
)

if discount_pct > 0:
    log_text += f"\n🎁 *Скидка:* {discount_pct}%"  # ❌ Not escaped!

log_text += f"\n\n🆔 *ID заявки:* `{request_id}`"  # ❌ Not escaped!
```

After:
```python
log_text = (
    "💰 *Новая заявка на оплату подписки*\n\n"
    f"👤 *Пользователь:*\n"
    f"• ID: `{escape_markdown(str(user_id))}`\n"
    f"• Username: @{escape_markdown(username)}\n"  # ✅ Properly escaped
    f"• Имя: {escape_markdown(full_name)}\n\n"
    f"💎 *План:* {escape_markdown(plan_name)}\n"
    f"💰 *Сумма:* {escape_markdown(amount)} смн"
)

if discount_pct > 0:
    log_text += f"\n🎁 *Скидка:* {escape_markdown(str(discount_pct))}%"  # ✅ Properly escaped

log_text += f"\n\n🆔 *ID заявки:* `{escape_markdown(str(request_id))}`"  # ✅ Properly escaped
```

## Testing

After the fix:
1. Payment requests will be successfully sent to the log group
2. Usernames with special characters (underscores, asterisks, etc.) will be properly displayed
3. Admin approval/rejection messages will also work correctly with special characters

## Prevention

To prevent similar issues in the future:
- Always use `escape_markdown()` for any user-generated content in MarkdownV2 messages
- Pay special attention to usernames, names, and other fields that may contain special characters
- Test with usernames containing underscores and other MarkdownV2 special characters
