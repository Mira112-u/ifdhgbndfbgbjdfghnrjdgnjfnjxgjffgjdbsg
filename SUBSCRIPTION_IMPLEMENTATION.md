# Subscription Implementation Summary

This document describes the implementation of the Intellex Premium subscription purchase and approval workflow.

## Overview

The subscription system allows users to purchase Intellex Premium subscriptions with different durations (1, 3, or 12 months), applies mode-based discounts (20% or 50%), and provides an admin approval workflow for payment verification.

## Components

### 1. Subscription Service (`subscription_service.py`)

A new service module that manages subscription-related functionality:

**Subscription Plans:**
- **1 month**: 50 TJS (30 days)
- **3 months**: 130 TJS (90 days)  
- **12 months**: 450 TJS (365 days)

**Key Functions:**
- `get_plan_price(plan_id, mode_service)`: Calculates final price with discount
- `calculate_discount_multiplier(mode)`: Returns multiplier based on bot mode (0.5 for 50%, 0.8 for 20%, 1.0 for normal)
- `generate_subscription_payment_url(amount, plan_id, user_id)`: Generates payment link
- `calculate_new_expiry(current_expiry, plan_id)`: Handles subscription stacking (extends from current expiry if active)
- `format_expiry_date(expiry)`: Formats dates for display

### 2. Database Extensions (`database.py`)

Added new methods to the `Database` class:

**Payment Request Management:**
- `create_payment_request(...)`: Creates new payment request with status "pending"
- `get_payment_request(request_id)`: Retrieves payment request details
- `update_payment_request_status(request_id, status, payment_method)`: Updates request status
- `get_pending_payment_requests(limit)`: Gets all requests with status "awaiting_approval"

**Subscription Management:**
- `create_subscription(...)`: Creates subscription record
- `get_active_subscription(user_id)`: Gets user's active subscription

### 3. Bot Updates (`bot.py`)

**Menu Changes:**
- Added "💎 Подписка" button to main menu (visible to all users)

**New Handlers:**
- `subscription_button(message)`: Entry point - shows active subscription info or purchase options
- `show_subscription_plans(message)`: Displays available plans with current prices and discounts
- `handle_subscription_callback(callback)`: Routes subscription-related callbacks
- `handle_plan_selection(callback, user_id, plan_id)`: Creates payment request and sends payment instructions
- `handle_payment_confirmation(callback, user_id, request_id)`: Processes "Я оплатил" button click

**Subscription Flow:**
1. User clicks "💎 Подписка" button
2. If active subscription exists: Shows expiry info and "Продлить" button
3. If no subscription: Shows plan selection with prices
4. User selects plan → Payment request created → Shows payment URL and "Я оплатил" button
5. User clicks "Я оплатил" → Status changes to "awaiting_approval" → Admin notified in log group

### 4. Admin Panel Extensions (`admin_panel.py`)

**Payments Section:**
- Accessible to Co-Admins and Admins (role: CO+)
- Shows pending payment requests (status: "awaiting_approval")
- For each request, displays: User info, plan details, amount, discount

**Payment Actions:**
- `handle_payments_section(callback)`: Main payments section view
- `handle_payment_approval(callback)`: 
  - Creates/extends subscription
  - Updates user premium status
  - Changes request status to "approved"
  - Notifies user: "Платеж обработан..."
  - Logs admin action
- `handle_payment_rejection(callback)`:
  - Changes request status to "rejected"
  - Notifies user: "Оплата не найдена"
  - Logs admin action

## User Flow

### Purchasing a Subscription

1. User opens bot and clicks "💎 Подписка"
2. Bot shows available plans with prices (including active discounts)
3. User selects a plan
4. Bot creates payment request and shows:
   - Payment link
   - Amount with discount info
   - "✅ Я оплатил" button
   - "❌ Отменить" button
5. User completes payment and clicks "Я оплатил"
6. Bot confirms submission and notifies admin group
7. Admin reviews and approves/rejects in admin panel
8. User receives notification of approval or rejection

### Extending Active Subscription

1. User with active subscription clicks "💎 Подписка"
2. Bot shows current expiry date and benefits
3. User clicks "➕ Продлить подписку"
4. Same flow as purchasing, but new expiry stacks on current expiry

## Admin Workflow

### Reviewing Payment Requests

1. Admin opens admin panel (`/admin`)
2. Selects "💰 Платежи и подписки" section
3. Views list of pending requests with user and plan details
4. For each request, can click:
   - "✅ Одобрить": Activates subscription
   - "❌ Отклонить": Rejects payment

### Approval Process

When admin approves:
1. Subscription record created in database
2. User's `is_premium` and `premium_expires_at` updated
3. Payment request marked as "approved"
4. User notified with confirmation message
5. Action logged to admin log group and database

### Rejection Process

When admin rejects:
1. Payment request marked as "rejected"
2. User notified that payment wasn't found
3. Action logged to admin log group and database

## Discount Modes

The system respects active bot modes:

- **NORMAL**: No discount (100% price)
- **DISCOUNT20**: 20% off (80% price)
- **DISCOUNT50**: 50% off (50% price)
- **TEST**: No discount (but all features free)
- **DISABLED**: Bot unavailable

Discounts are:
- Calculated when user views plans
- Stored in payment request metadata
- Displayed in admin panel
- Logged in admin actions

## Edge Cases Handled

1. **Overlapping Purchases**: If user has active subscription, new subscription extends from current expiry (stacking)
2. **Expired Subscriptions**: If current subscription is expired, new one starts from now
3. **Cancelled Requests**: User can cancel before confirming payment
4. **Missing Log Group**: System continues to work if LOG_GROUP_ID not configured
5. **Already Processed Requests**: Admin panel prevents double-processing of requests
6. **Invalid Request Data**: Proper error handling for missing or invalid metadata

## Payment URL Format

Subscription payments use the same gateway as fines:
```
https://pay.dc.tj/pay.php?a=SUB{user_id}&s={amount}&c=&f1=346&f2=#kortiMilli
```

The identifier format `SUB{user_id}` distinguishes subscription payments from fine payments.

## Database Schema

The implementation uses existing tables:

**PaymentRequest** (already existed):
- `payment_type`: Set to "subscription"
- `payment_metadata`: Stores plan_id, plan_name, discount info
- `status`: "pending" → "awaiting_approval" → "approved"/"rejected"/"cancelled"

**Subscription** (already existed):
- `subscription_type`: Set to "premium"
- `starts_at`: Subscription start time
- `expires_at`: Subscription expiry time
- `payment_amount`: Amount paid
- `payment_method`: Set to "manual_approval"

**User** (already existed):
- `is_premium`: Updated on approval
- `premium_expires_at`: Updated on approval

## Testing

A comprehensive test suite (`test_subscriptions.py`) validates:
- Subscription plan constants
- Discount calculations
- Payment URL generation
- Expiry date calculations (including stacking)
- Date formatting
- Plan name retrieval
- Async price calculations

All tests pass successfully.

## Security & Permissions

- Payment section requires CO role (Co-Admin) or higher
- All admin actions are logged to database and log group
- Payment requests track admin who approved/rejected
- User notifications include relevant details without sensitive info

## Future Enhancements

Potential improvements:
- Automatic payment verification integration
- Payment method selection (card, cash, etc)
- Subscription auto-renewal
- Subscription pause/resume
- Gift subscriptions
- Promo codes
- Subscription analytics dashboard
