# bot_vin.py
import asyncio
import logging
import os
import re
from typing import Callable, Dict, Any, Awaitable
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, TelegramObject, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from scraper_vin import ClientCardScraper

# --- Конфигурация ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_IDS = {int(user_id) for user_id in os.getenv("ALLOWED_USER_IDS", "").split(',')}

# --- FSM для ожидания ввода ---
class SearchState(StatesGroup):
    waiting_for_input = State()

# --- Инициализация ---
router = Router()
scraper = ClientCardScraper(login="neruadmin", password="neru900876")

# --- Middleware для проверки доступа ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        if event.from_user.id not in ALLOWED_USER_IDS:
            print(f"🚫 Отклонен доступ для пользователя {event.from_user.id} ({event.from_user.username})")
            return
        return await handler(event, data)

# --- Вспомогательные функции ---
def escape_markdown(text: str) -> str:
    if not isinstance(text, str): text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def format_phone_number(phone: str) -> str:
    """Форматирует номер телефона и сразу его экранирует."""
    if not phone: return "N/A"
    cleaned_phone = re.sub(r'\D', '', phone)
    if cleaned_phone.startswith('992'):
        formatted = '+' + cleaned_phone
    else:
        formatted = '+992' + cleaned_phone
    # ✅ ИСПРАВЛЕНИЕ ЗДЕСЬ: Экранируем результат
    return escape_markdown(formatted)

def format_client_card(data: dict) -> str:
    """Форматирует данные карты клиента в красивое сообщение."""
    car_info = data.get('car', {})
    driver_info = data.get('driver', {})
    docs_info = data.get('docs', {})
    photos = data.get('photos', [])

    text = "📄 **Результаты поиска:**\n\n"
    
    if car_info:
        text += "🚗 **Автомобиль**\n"
        for key, value in car_info.items():
            text += f" \\- _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
        text += "\n"

    if driver_info:
        text += "👤 **Водитель**\n"
        for key, value in driver_info.items():
            if key.lower() == 'телефон':
                # Функция уже возвращает экранированный номер
                formatted_phone = format_phone_number(value)
                # Выводим без ```, так как он уже экранирован
                text += f" \\- _{escape_markdown(key)}:_ {formatted_phone}\n"
            else:
                text += f" \\- _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
        text += "\n"

    if docs_info:
        text += "📋 **Документы**\n"
        for key, value in docs_info.items():
            text += f" \\- _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
    
    if photos:
        text += "\n🖼️ **Фото**\n"
        for i, link in enumerate(photos):
            # Ссылки в Markdown не нужно экранировать
            text += f" [Фото {i+1}]({link})\n"

    return text

# --- Клавиатура ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Проверить авто")]],
    resize_keyboard=True
)

# --- Обработчики ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Здравствуйте\\! 👋\n\nНажмите кнопку 'Проверить авто', чтобы начать поиск\\.",
        reply_markup=main_kb,
        parse_mode=ParseMode.MARKDOWN_V2
    )

@router.message(F.text.lower() == "проверить авто")
async def start_search(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, введите номер автомобиля или VIN-код:")
    await state.set_state(SearchState.waiting_for_input)


@router.message(StateFilter(SearchState.waiting_for_input))
async def handle_vin_or_plate(message: Message, state: FSMContext):
    search_query = message.text.strip()
    await state.clear()
    
    wait_message = await message.answer("🔍 Ищу информацию, пожалуйста, подождите...", reply_markup=main_kb)
    
    search_result = await asyncio.to_thread(scraper.get_client_card_info, search_query)
    
    await wait_message.delete()

    if search_result.get("error"):
        await message.answer(f"😕 *Ошибка:* {escape_markdown(search_result['error'])}", parse_mode=ParseMode.MARKDOWN_V2)
        return
        
    formatted_text = format_client_card(search_result)
    await message.answer(formatted_text, parse_mode=ParseMode.MARKDOWN_V2)

# --- Запуск бота ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    dp.message.outer_middleware.register(AccessMiddleware())
    dp.include_router(router)
    
    print(f"Бот запущен! Разрешен доступ для ID: {ALLOWED_USER_IDS}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
