# monitor.py
import asyncio
import logging
from datetime import datetime
from typing import Optional
import re

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from database import Database
from scraper import RbdaScraper

logger = logging.getLogger(__name__)

class FineMonitor:
    def __init__(self, bot: Bot, scraper: RbdaScraper, database: Database, 
                 poll_interval: int = 1800, rate_limit_delay: float = 5.0):
        """
        Initialize the fine monitor
        
        Args:
            bot: Telegram bot instance
            scraper: RBDA scraper instance
            database: Database instance
            poll_interval: Polling interval in seconds (default: 1800 = 30 minutes)
            rate_limit_delay: Delay between requests in seconds (default: 5.0)
        """
        self.bot = bot
        self.scraper = scraper
        self.database = database
        self.poll_interval = poll_interval
        self.rate_limit_delay = rate_limit_delay
        self.monitoring_task: Optional[asyncio.Task] = None
        self.shutdown_event = asyncio.Event()
        
    def escape_markdown(self, text: str) -> str:
        """Escape special characters for Markdown V2"""
        if not isinstance(text, str):
            text = str(text)
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
    
    async def send_fine_notification(self, user_id: int, fine_data: dict, plate: str, 
                                     vehicle_info: dict):
        """Send notification about a new fine to the user"""
        try:
            # Build notification header
            notification_text = (
                "🚨 *НОВЫЙ ШТРАФ\\!*\n\n"
                "📋 *Информация об автомобиле:*\n"
                f"🚗 *Номер:* `{self.escape_markdown(plate)}`\n"
            )
            
            # Add vehicle info if available
            if vehicle_info.get('brand'):
                notification_text += f"🏷 *Марка:* {self.escape_markdown(vehicle_info['brand'])}\n"
            if vehicle_info.get('model'):
                notification_text += f"🏎 *Модель:* {self.escape_markdown(vehicle_info['model'])}\n"
            
            # Add fine details
            notification_text += (
                f"\n📋 *Детали штрафа:*\n"
                f"📄 *Ордер:* `{self.escape_markdown(fine_data['order'])}`\n"
                f"📅 *Дата нарушения:* {self.escape_markdown(fine_data['date'])}\n"
                f"⚠️ *Нарушение:* _{self.escape_markdown(fine_data['violation'])}_\n"
                f"💰 *Сумма:* *{self.escape_markdown(fine_data['amount'])}*\n"
            )
            
            # Build inline keyboard with media and payment buttons
            builder = InlineKeyboardBuilder()
            media_links = fine_data.get('media_links', {})
            
            # Add media buttons (we'll send media separately)
            media_count = len(media_links)
            if media_count > 0:
                notification_text += f"\n📸 *Медиафайлы:* {media_count} шт\\.\n"
            
            # Add payment button
            amount_numeric = re.sub(r'[^0-9]', '', fine_data['amount'])
            payment_url = f"https://pay.dc.tj/pay.php?a={fine_data['order']}&s={amount_numeric}&c=&f1=346&f2=#kortiMilli"
            builder.button(text=f"💳 Оплатить {fine_data['amount']}", url=payment_url)
            
            # Send main notification
            await self.bot.send_message(
                user_id,
                notification_text,
                reply_markup=builder.as_markup(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            # Send all media files
            for media_key, viewer_link in media_links.items():
                try:
                    await self._send_media_to_user(user_id, viewer_link, media_key, fine_data['order'])
                except Exception as e:
                    logger.error(f"Failed to send media {media_key} to user {user_id}: {e}")
            
            logger.info(f"Sent fine notification for order {fine_data['order']} to user {user_id}")
            return True
            
        except TelegramBadRequest as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending notification to user {user_id}: {e}")
            return False
    
    async def _send_media_to_user(self, user_id: int, viewer_link: str, media_key: str, order: str):
        """Download and send media file to user"""
        try:
            # Get direct media link
            direct_link = await asyncio.to_thread(self.scraper.get_direct_media_link, viewer_link)
            if not direct_link:
                logger.warning(f"Could not get direct link for {media_key}")
                return
            
            # Download media
            media_content = await asyncio.to_thread(self.scraper.download_media, direct_link)
            if not media_content:
                logger.warning(f"Could not download media {media_key}")
                return
            
            # Prepare file
            filename = direct_link.split('/')[-1].split('?')[0] or f"{media_key}_{order}"
            file = BufferedInputFile(media_content, filename=filename)
            caption = f"Медиа для штрафа `{self.escape_markdown(order)}`"
            
            # Send based on media type
            if any(ext in direct_link.lower() for ext in ['.jpg', '.jpeg', '.png']):
                await self.bot.send_photo(user_id, file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
            elif media_key == "видео" or '.mp4' in direct_link.lower() or 'video.mycar.tj' in direct_link.lower():
                await self.bot.send_video(user_id, file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await self.bot.send_document(user_id, file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
                
        except Exception as e:
            logger.error(f"Error sending media to user {user_id}: {e}")
    
    def parse_outstanding_info(self, fine_data: dict) -> Optional[str]:
        """
        Extract outstanding payment information from fine data if available
        e.g., "9 из 100 оплачено"
        """
        # This would depend on the actual structure of the fine data
        # For now, we'll use the amount field as a proxy
        return fine_data.get('amount')
    
    async def check_fines_for_user(self, binding_id: int, user_id: int, plate_number: str,
                                   tracked_orders: list, tracked_initialized: bool):
        """Check fines for a specific user and plate"""
        try:
            logger.info(f"Checking fines for user {user_id}, plate {plate_number}")
            
            # Fetch fines from scraper
            search_result = await asyncio.to_thread(
                self.scraper.search_fines_by_plate, 
                plate_number
            )
            
            if search_result.get("error"):
                logger.error(f"Error searching fines for {plate_number}: {search_result['error']}")
                return
            
            fines = search_result.get("fines", [])
            vehicle_info = search_result.get("vehicle_info", {})
            
            logger.info(f"Found {len(fines)} fines for plate {plate_number}")
            
            # Get current order numbers from API
            current_orders = [fine.get('order') for fine in fines if fine.get('order')]
            
            # If tracked_orders is not initialized (old binding or first time), initialize it
            if not tracked_initialized:
                logger.info(f"Initializing tracked orders for binding {binding_id}: {len(current_orders)} orders")
                await self.database.update_tracked_orders(binding_id, current_orders)
                return
            
            # Compare current orders with tracked orders
            tracked_set = set(tracked_orders)
            current_set = set(current_orders)
            new_orders = current_set - tracked_set
            
            logger.info(f"Tracked: {len(tracked_set)}, Current: {len(current_set)}, New: {len(new_orders)}")
            
            # Send notifications for new fines only
            if new_orders:
                new_fines = [fine for fine in fines if fine.get('order') in new_orders]
                for idx, fine in enumerate(new_fines):
                    order_number = fine.get('order')
                    logger.info(f"New fine detected: {order_number} for user {user_id}")
                    
                    success = await self.send_fine_notification(
                        user_id=user_id,
                        fine_data=fine,
                        plate=plate_number,
                        vehicle_info=vehicle_info
                    )
                    
                    if success:
                        violation_type = fine.get('violation', 'N/A')
                        violation_date = fine.get('date', 'N/A')
                        amount = fine.get('amount', 'N/A')
                        media_links = fine.get('media_links', {})
                        outstanding_info = self.parse_outstanding_info(fine)
                        
                        await self.database.add_or_update_fine_order(
                            order_number=order_number,
                            user_id=user_id,
                            plate_number=plate_number,
                            violation_type=violation_type,
                            violation_date=violation_date,
                            amount=amount,
                            outstanding_info=outstanding_info,
                            media_links=media_links
                        )
                        await self.database.mark_order_notified(order_number, user_id)
                    
                    if idx < len(new_fines) - 1:
                        await asyncio.sleep(2)
            else:
                logger.debug(f"No new fines for user {user_id}, plate {plate_number}")
            
            # Update tracked orders with current list (this handles paid/removed fines)
            await self.database.update_tracked_orders(binding_id, current_orders)
                    
        except Exception as e:
            logger.error(f"Error checking fines for user {user_id}, plate {plate_number}: {e}")
    
    async def monitoring_loop(self):
        """Main monitoring loop that runs periodically"""
        logger.info(f"Starting fine monitoring loop (interval: {self.poll_interval}s)")
        
        while not self.shutdown_event.is_set():
            try:
                # Remove expired bindings
                await self.database.remove_expired_bindings()
                
                # Get active premium bindings
                active_bindings = await self.database.get_active_premium_bindings()
                logger.info(f"Found {len(active_bindings)} active premium bindings to check")
                
                # Check fines for each binding with rate limiting
                for binding in active_bindings:
                    if self.shutdown_event.is_set():
                        break
                    
                    user_id = binding['user_id']
                    plate_number = binding['plate_number']
                    binding_id = binding['binding_id']
                    tracked_orders = binding.get('tracked_orders', [])
                    tracked_initialized = binding.get('tracked_initialized', False)
                    
                    try:
                        await self.check_fines_for_user(
                            binding_id=binding_id,
                            user_id=user_id,
                            plate_number=plate_number,
                            tracked_orders=tracked_orders,
                            tracked_initialized=tracked_initialized
                        )
                    except Exception as e:
                        logger.error(f"Error processing binding for user {user_id}: {e}")
                    
                    # Rate limiting delay
                    await asyncio.sleep(self.rate_limit_delay)
                
                # Wait for next poll interval or shutdown
                try:
                    await asyncio.wait_for(
                        self.shutdown_event.wait(),
                        timeout=self.poll_interval
                    )
                except asyncio.TimeoutError:
                    # Normal timeout, continue to next iteration
                    pass
                    
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                # Wait a bit before retrying
                await asyncio.sleep(60)
        
        logger.info("Fine monitoring loop stopped")
    
    def start(self):
        """Start the monitoring task"""
        if self.monitoring_task is None or self.monitoring_task.done():
            self.shutdown_event.clear()
            self.monitoring_task = asyncio.create_task(self.monitoring_loop())
            logger.info("Fine monitoring task started")
        else:
            logger.warning("Fine monitoring task is already running")
    
    async def stop(self):
        """Stop the monitoring task gracefully"""
        logger.info("Stopping fine monitoring task...")
        self.shutdown_event.set()
        
        if self.monitoring_task:
            try:
                await asyncio.wait_for(self.monitoring_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Monitoring task did not stop gracefully, cancelling...")
                self.monitoring_task.cancel()
                try:
                    await self.monitoring_task
                except asyncio.CancelledError:
                    pass
        
        logger.info("Fine monitoring task stopped")
