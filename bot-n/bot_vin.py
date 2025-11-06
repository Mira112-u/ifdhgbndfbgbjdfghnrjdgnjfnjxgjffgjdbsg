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
            username = event.from_user.username or "без username"
            full_name = f"{event.from_user.first_name or ''} {event.from_user.last_name or ''}".strip()
            print(f"🚫 Отклонен доступ | ID: {event.from_user.id} | @{username} | {full_name}")
            
            # Отправляем сообщение пользователю
            await event.answer(
                "⛔ *Доступ запрещён*\n\n"
                "Этот бот доступен только авторизованным пользователям\\.\n\n"
                "_Для получения доступа свяжитесь с администратором\\._",
                parse_mode=ParseMode.MARKDOWN_V2
            )
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

    text = "✅ *Результаты поиска*\n\n"
    
    if car_info:
        text += "🚗 *АВТОМОБИЛЬ*\n"
        for key, value in car_info.items():
            text += f"• _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
        text += "\n"

    if driver_info:
        text += "👤 *ВОДИТЕЛЬ*\n"
        for key, value in driver_info.items():
            if key.lower() == 'телефон':
                # Функция уже возвращает экранированный номер
                formatted_phone = format_phone_number(value)
                # Выводим без ```, так как он уже экранирован
                text += f"• _{escape_markdown(key)}:_ {formatted_phone}\n"
            else:
                text += f"• _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
        text += "\n"

    if docs_info:
        text += "📋 *ДОКУМЕНТЫ*\n"
        for key, value in docs_info.items():
            text += f"• _{escape_markdown(key)}:_ `{escape_markdown(value or 'N/A')}`\n"
    
    if photos:
        text += "\n🖼️ *ФОТОГРАФИИ*\n"
        for i, link in enumerate(photos):
            # Ссылки в Markdown не нужно экранировать
            text += f"[📷 Фото {i+1}]({link})\n"

    return text

# --- Клавиатура ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🚗 Проверить авто")]],
    resize_keyboard=True,
    input_field_placeholder="Введите номер или VIN"
)

# --- Обработчики ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    welcome_text = (
        "👋 *Добро пожаловать в БДА Поиск\\!*\n\n"
        "🔍 Я помогу вам найти информацию об автомобиле "
        "по номеру или VIN\\-коду\\.\n\n"
        "Нажмите кнопку *\"🚗 Проверить авто\"* или сразу введите "
        "номер автомобиля или VIN\\-код\\."
    )
    
    await message.answer(
        welcome_text,
        reply_markup=main_kb,
        parse_mode=ParseMode.MARKDOWN_V2
    )

@router.message(F.text == "🚗 Проверить авто")
async def start_search(message: Message, state: FSMContext):
    await message.answer(
        "📋 *Введите данные для поиска:*\n\n"
        "• Номер автомобиля \\(например: 0000AA01\\)\n"
        "• VIN\\-код автомобиля\n\n"
        "_Просто отправьте текстом\\._",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(SearchState.waiting_for_input)


@router.message(StateFilter(SearchState.waiting_for_input))
async def handle_vin_or_plate(message: Message, state: FSMContext):
    search_query = message.text.strip()
    await state.clear()
    
    wait_message = await message.answer(
        "🔍 *Идёт поиск\\.\\.\\.*\n\n"
        "_Пожалуйста, подождите\\.\\.\\._",
        reply_markup=main_kb,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    search_result = await asyncio.to_thread(scraper.get_client_card_info, search_query)
    
    await wait_message.delete()

    if search_result.get("error"):
        await message.answer(
            f"❌ *Ошибка поиска*\n\n"
            f"_{escape_markdown(search_result['error'])}_\n\n"
            "Попробуйте ещё раз или проверьте корректность введённых данных\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
        
    formatted_text = format_client_card(search_result)
    await message.answer(formatted_text, parse_mode=ParseMode.MARKDOWN_V2)

@router.message(F.text)
async def handle_direct_input(message: Message, state: FSMContext):
    """Обработчик прямого ввода номера или VIN без использования кнопки"""
    search_query = message.text.strip()
    
    # Простая проверка: если текст содержит буквы и цифры, считаем это потенциальным номером/VIN
    if len(search_query) >= 4 and any(c.isdigit() for c in search_query):
        wait_message = await message.answer(
            "🔍 *Идёт поиск\\.\\.\\.*\n\n"
            "_Пожалуйста, подождите\\.\\.\\._",
            reply_markup=main_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        search_result = await asyncio.to_thread(scraper.get_client_card_info, search_query)
        
        await wait_message.delete()

        if search_result.get("error"):
            await message.answer(
                f"❌ *Ошибка поиска*\n\n"
                f"_{escape_markdown(search_result['error'])}_\n\n"
                "Попробуйте ещё раз или проверьте корректность введённых данных\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
            
        formatted_text = format_client_card(search_result)
        await message.answer(formatted_text, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await message.answer(
            "❓ *Не понял запрос*\n\n"
            "Пожалуйста, нажмите кнопку *\"🚗 Проверить авто\"* "
            "или отправьте номер автомобиля\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

# --- Запуск бота ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    dp.message.outer_middleware.register(AccessMiddleware())
    dp.include_router(router)
    
    print("\n" + "="*50)
    print("🚀 БДА Поиск Бот запущен!")
    print("="*50)
    print(f"✅ Разрешен доступ для ID: {ALLOWED_USER_IDS}")
    print(f"🔐 Авторизованных пользователей: {len(ALLOWED_USER_IDS)}")
    print("="*50 + "\n")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n" + "="*50)
        print("⛔ Бот остановлен пользователем")
        print("="*50)
