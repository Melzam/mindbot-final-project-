from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import openai
import os

# Инициализация переменных окружения
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # integer string

# Настройка клиентов
openai.api_key = OPENAI_API_KEY
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Состояния
class MoodStates(StatesGroup):
    waiting_for_reflection = State()
    waiting_for_homework = State()
    waiting_for_feedback = State()

# Главное меню
async def show_main_menu(message: Message):
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🧠 Психологический совет"), KeyboardButton(text="🎨 Иллюстрация эмоции")],
        [KeyboardButton(text="🧘 Практика осознанности"), KeyboardButton(text="📤 Отправить задание")],
        [KeyboardButton(text="✉️ Обратная связь")]
    ], resize_keyboard=True)
    await message.answer("Выберите действие из меню:", reply_markup=keyboard)

# Генерация через GPT
async def generate_gpt_text(prompt: str) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("GPT Error:", e)
        return "Не удалось получить ответ. Попробуйте позже."

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("Привет! Я твой помощник по психологическому самопознанию. 🧠")
    await show_main_menu(message)

# Ответ на команды меню
@dp.message(F.text.in_(["🧠 Психологический совет", "🎨 Иллюстрация эмоции", "🧘 Практика осознанности", "📤 Отправить задание", "✉️ Обратная связь"]))
async def handle_text(message: Message, state: FSMContext):
    command = message.text

    if command == "🧠 Психологический совет":
        direction = "саморазвитие"
        age = "взрослый"
        context = "работает в стрессовой среде"
        stress_level = "3 из 5"

        prompt = f"""
        Роль: Клинический психолог‑практик и коуч по личностному росту.
        Цель: дать один конкретный, безопасный и этически нейтральный совет на тему «{direction}»
        Структура ответа:
        (1) Короткий крючок (1–2 предложения)
        (2) Объяснение «почему это работает» (научно‑популярно, без диагнозов)
        (3) Практический шаг (1 упражнение на сегодня, 3–7 минут)
        (4) Мягкое подбадривание
        Тон: тёплый, поддерживающий, без медицинских рекомендаций и без обещаний результата.
        Персонализация: использовать ответы пользователя на уточняющие вопросы (если есть): {age}, {context}, {stress_level}.
        Ограничения: не давать мед.советов, не упоминать лекарства, избегать триггеров.
        Формат: 120–180 слов.
        """
        tip = await generate_gpt_text(prompt)
        await message.answer(tip)

    elif command == "🎨 Иллюстрация эмоции":
        await message.answer("Пожалуйста, подождите немного. Генерирую иллюстрацию… 🎨")
        prompt = "Abstract emotional illustration, flat style, soft warm colors, happiness and calmness, no text"
        try:
            response = openai.Image.create(
                prompt=prompt,
                n=1,
                size="1024x1024"
            )
            image_url = response['data'][0]['url']
            await message.answer_photo(image_url, caption="Вот ваша иллюстрация эмоции")
        except Exception as e:
            await message.answer("Не удалось сгенерировать изображение. Попробуйте позже.")
            print("DALL·E error:", e)

    elif command == "🧘 Практика осознанности":
        practice = await generate_gpt_text(
            """
            Роль: Инструктор MBSR.
            Цель: одно упражнение на 3–5 минут.
            Структура: (1) Подготовка, (2) Пошаговые инструкции (5–7 шагов), (3) Как завершить, (4) Что заметить.
            Безопасность: no medical claims; мягкие формулировки «обратите внимание», «если удобно».
            """
        )
        await message.answer(practice)

    elif command == "📤 Отправить задание":
        await message.answer("Отправьте текст или изображение вашего задания. Я передам его администратору 🧡")
        await state.set_state(MoodStates.waiting_for_homework)

    elif command == "✉️ Обратная связь":
        await message.answer("Напишите, что бы вы хотели улучшить в боте или что вам понравилось")
        await state.set_state(MoodStates.waiting_for_feedback)

# Обработка рефлексии (если нужно будет расширить)
@dp.message(MoodStates.waiting_for_reflection)
async def handle_reflection(message: Message, state: FSMContext):
    await message.answer(f"Я рядом. Это нормально — чувствовать '{message.text}'. Спасибо, что поделились. 🧡")
    await state.clear()
    await show_main_menu(message)

# Обработка домашнего задания
@dp.message(MoodStates.waiting_for_homework)
async def handle_homework(message: Message, state: FSMContext):
    if ADMIN_CHAT_ID:
        await bot.copy_message(chat_id=int(ADMIN_CHAT_ID), from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer("Задание получено. Спасибо! 📬")
    else:
        await message.answer("Администратор не настроен. Задание не отправлено.")
    await state.clear()
    await show_main_menu(message)

# Обработка обратной связи
@dp.message(MoodStates.waiting_for_feedback)
async def handle_feedback(message: Message, state: FSMContext):
    if ADMIN_CHAT_ID:
        await bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=f"📬 Новый отзыв:\n{message.text}")
        await message.answer("Спасибо за обратную связь! 🧡")
    else:
        await message.answer("Администратор не настроен. Отзыв не отправлен.")
    await state.clear()
    await show_main_menu(message)

# Обработка всего прочего (fallback)
@dp.message()
async def fallback(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, выберите действие из меню ⬇️")

# Ежедневная цитата
async def send_daily_quote():
    if ADMIN_CHAT_ID:
        quote = await generate_gpt_text("Короткая психологическая цитата, вдохновляющая, до 200 символов")
        await bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=f"📟️ {quote}")

# Старт бота
async def main():
    scheduler.add_job(send_daily_quote, trigger='cron', hour=10, minute=0)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
