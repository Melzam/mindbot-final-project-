import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import AsyncOpenAI

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DIRECTIONS = ["🌱 Эмоции", "🤝 Отношения", "🌟 Самооценка", "🕊 Стресс-менеджмент"]
MENU_TEXTS = [
    "🧠 Психологический совет",
    "🎨 Иллюстрация эмоции",
    "🧘 Практика осознанности",
    "📤 Отправить задание",
    "✉️ Обратная связь",
    "🔄 Сменить направление",
]


class MoodStates(StatesGroup):
    waiting_for_homework = State()
    waiting_for_feedback = State()


@dataclass
class Settings:
    telegram_token: str
    openai_api_key: str
    admin_chat_id: int
    database_url: str
    openai_text_model: str = "gpt-4o-mini"
    openai_image_model: str = "gpt-image-1"


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        await self.init_schema()

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def init_schema(self):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    direction TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS homework_submissions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    content_type TEXT NOT NULL,
                    text_content TEXT,
                    telegram_file_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS feedback_messages (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    feedback_text TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

    async def upsert_user(self, message: Message, direction: str | None = None):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, direction, is_active, updated_at)
                VALUES ($1, $2, $3, $4, $5, TRUE, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    direction = COALESCE(EXCLUDED.direction, users.direction),
                    is_active = TRUE,
                    updated_at = NOW();
                """,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                message.from_user.last_name,
                direction,
            )

    async def set_direction(self, user_id: int, direction: str):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET direction = $2, updated_at = NOW() WHERE user_id = $1",
                user_id,
                direction,
            )

    async def get_direction(self, user_id: int) -> str | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT direction FROM users WHERE user_id = $1", user_id)

    async def get_active_user_ids(self) -> list[int]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM users WHERE is_active = TRUE")
            return [row["user_id"] for row in rows]

    async def save_homework(self, message: Message):
        assert self.pool is not None
        content_type = "text"
        text_content = message.text or message.caption
        telegram_file_id = None

        if message.photo:
            content_type = "photo"
            telegram_file_id = message.photo[-1].file_id
        elif message.document:
            content_type = "document"
            telegram_file_id = message.document.file_id

        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO homework_submissions (
                    user_id, username, first_name, last_name, content_type, text_content, telegram_file_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                message.from_user.last_name,
                content_type,
                text_content,
                telegram_file_id,
            )

    async def save_feedback(self, message: Message):
        assert self.pool is not None
        return await self.pool.fetchval(
            """
            INSERT INTO feedback_messages (user_id, username, first_name, last_name, feedback_text)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name,
            message.text or "",
        )


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    return Settings(
        telegram_token=get_required_env("TELEGRAM_TOKEN"),
        openai_api_key=get_required_env("OPENAI_API_KEY"),
        admin_chat_id=int(get_required_env("ADMIN_CHAT_ID")),
        database_url=get_required_env("DATABASE_URL"),
        openai_text_model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
    )


settings = load_settings()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("psybot")

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(
    token=settings.telegram_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
client = AsyncOpenAI(api_key=settings.openai_api_key)
db = Database(settings.database_url)
scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)


def direction_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=DIRECTIONS[0]), KeyboardButton(text=DIRECTIONS[1])],
            [KeyboardButton(text=DIRECTIONS[2]), KeyboardButton(text=DIRECTIONS[3])],
        ],
        resize_keyboard=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_TEXTS[0]), KeyboardButton(text=MENU_TEXTS[1])],
            [KeyboardButton(text=MENU_TEXTS[2]), KeyboardButton(text=MENU_TEXTS[3])],
            [KeyboardButton(text=MENU_TEXTS[4]), KeyboardButton(text=MENU_TEXTS[5])],
        ],
        resize_keyboard=True,
    )


async def ensure_user(message: Message):
    await db.upsert_user(message)


async def show_directions(message: Message):
    await message.answer(
        "Выберите направление, с которым хотите поработать сейчас:",
        reply_markup=direction_keyboard(),
    )


async def show_main_menu(message: Message, direction: str | None = None):
    if direction:
        text = f"Направление выбрано: <b>{direction}</b>\n\nВыберите действие из меню:"
    else:
        text = "Выберите действие из меню:"
    await message.answer(text, reply_markup=main_menu_keyboard())


async def get_user_direction(user_id: int) -> str:
    direction = await db.get_direction(user_id)
    return direction or "🌱 Эмоции"


async def generate_gpt_text(prompt: str) -> str:
    try:
        response = await client.chat.completions.create(
            model=settings.openai_text_model,
            messages=[
                {
                    "role": "system",
                    "content": "Ты тёплый, бережный психологический ассистент. Не ставишь диагнозы, не даёшь медицинских назначений и не обещаешь результатов.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("GPT generation failed")
        return "Не удалось получить ответ. Попробуйте чуть позже."


async def generate_image_bytes(prompt: str) -> bytes:
    response = await client.images.generate(
        model=settings.openai_image_model,
        prompt=prompt,
        size="1024x1024",
    )
    b64_json = response.data[0].b64_json
    if not b64_json:
        raise RuntimeError("Image generation returned no image data")
    return base64.b64decode(b64_json)


def build_insight_prompt(direction: str) -> str:
    return f"""
Роль: психологический ассистент.
Тема: {direction}.
Задача: дать один конкретный, безопасный, поддерживающий совет.
Структура ответа:
1. Короткий тёплый заход.
2. Простое объяснение, почему этот подход может помочь.
3. Один практический шаг на 3–7 минут.
4. Мягкое подбадривание.
Ограничения: без диагнозов, без медикаментов, без триггерных формулировок, без обещаний результата.
Формат: 120–180 слов.
""".strip()


def build_mindfulness_prompt(direction: str) -> str:
    return f"""
Роль: инструктор по мягким практикам осознанности.
Тема: {direction}.
Задача: дать одно упражнение на 3–5 минут.
Структура:
1. Подготовка.
2. Пошаговые инструкции — 5–7 коротких шагов.
3. Как завершить.
4. Что заметить после практики.
Ограничения: мягкий тон, никаких медицинских советов.
""".strip()


def build_quote_prompt() -> str:
    return "Короткая вдохновляющая психологическая мысль до 200 символов, тёплая, без пафоса и без клише."


async def send_admin_notification(text: str):
    try:
        await bot.send_message(settings.admin_chat_id, text)
    except Exception:
        logger.exception("Failed to notify admin")


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.upsert_user(message)
    await message.answer(
        "Привет! Я бот для психологического проекта. Помогаю с короткими инсайтами, практиками, иллюстрациями эмоций и приёмом заданий."
    )
    await show_directions(message)


@dp.message(F.text.in_(DIRECTIONS))
async def set_direction(message: Message, state: FSMContext):
    await ensure_user(message)
    await db.set_direction(message.from_user.id, message.text)
    await state.clear()
    await show_main_menu(message, message.text)


@dp.message(Command("daily_insight"))
@dp.message(F.text == "🧠 Психологический совет")
async def daily_insight(message: Message):
    await ensure_user(message)
    direction = await get_user_direction(message.from_user.id)
    text = await generate_gpt_text(build_insight_prompt(direction))
    await message.answer(text)


@dp.message(Command("mindfulness_exercise"))
@dp.message(F.text == "🧘 Практика осознанности")
async def mindfulness_exercise(message: Message):
    await ensure_user(message)
    direction = await get_user_direction(message.from_user.id)
    text = await generate_gpt_text(build_mindfulness_prompt(direction))
    await message.answer(text)


@dp.message(Command("emotion_card"))
@dp.message(F.text == "🎨 Иллюстрация эмоции")
async def emotion_card(message: Message):
    await ensure_user(message)
    direction = await get_user_direction(message.from_user.id)
    await message.answer("Генерирую иллюстрацию эмоции… 🎨")
    prompt = (
        f"Abstract emotional illustration for psychological self-reflection, theme: {direction}, "
        "soft expressive colors, modern editorial style, calming composition, no text"
    )
    try:
        image_bytes = await generate_image_bytes(prompt)
        from io import BytesIO

        photo = BytesIO(image_bytes)
        photo.name = f"emotion_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')}.png"
        await message.answer_photo(photo=photo, caption=f"Вот иллюстрация по теме {direction}")
    except Exception:
        logger.exception("Image generation failed")
        await message.answer("Не удалось сгенерировать изображение. Попробуйте чуть позже.")


@dp.message(Command("submit_homework"))
@dp.message(F.text == "📤 Отправить задание")
async def submit_homework(message: Message, state: FSMContext):
    await ensure_user(message)
    await state.set_state(MoodStates.waiting_for_homework)
    await message.answer("Пришлите следующим сообщением текст, фото или документ с заданием. Я сохраню его и отправлю администратору. 🧡")


@dp.message(Command("feedback"))
@dp.message(F.text == "✉️ Обратная связь")
async def feedback(message: Message, state: FSMContext):
    await ensure_user(message)
    await state.set_state(MoodStates.waiting_for_feedback)
    await message.answer("Напишите, что понравилось в боте или что вы бы хотели улучшить.")


@dp.message(F.text == "🔄 Сменить направление")
async def change_direction(message: Message, state: FSMContext):
    await ensure_user(message)
    await state.clear()
    await show_directions(message)


@dp.message(MoodStates.waiting_for_homework)
async def handle_homework(message: Message, state: FSMContext):
    await ensure_user(message)
    submission_id = await db.save_homework(message)

    admin_caption = (
        f"📥 Новое задание #{submission_id}\n"
        f"От: {message.from_user.full_name}"
        f" (@{message.from_user.username})" if message.from_user.username else f"📥 Новое задание #{submission_id}\nОт: {message.from_user.full_name}"
    )

    try:
        if message.photo:
            await bot.send_photo(settings.admin_chat_id, photo=message.photo[-1].file_id, caption=admin_caption)
        elif message.document:
            await bot.send_document(settings.admin_chat_id, document=message.document.file_id, caption=admin_caption)
        else:
            text = message.text or message.caption or "—"
            await bot.send_message(settings.admin_chat_id, f"{admin_caption}\n\n{text}")
    except Exception:
        logger.exception("Failed to forward homework to admin")

    await message.answer("Задание получено, сохранено и передано администратору. Спасибо! 📬")
    await state.clear()
    await show_main_menu(message, await get_user_direction(message.from_user.id))


@dp.message(MoodStates.waiting_for_feedback)
async def handle_feedback(message: Message, state: FSMContext):
    await ensure_user(message)
    feedback_id = await db.save_feedback(message)
    text = (
        f"📬 Новый отзыв #{feedback_id}\n"
        f"От: {message.from_user.full_name}"
        + (f" (@{message.from_user.username})" if message.from_user.username else "")
        + f"\n\n{message.text or '—'}"
    )
    await send_admin_notification(text)
    await message.answer("Спасибо за обратную связь! 🧡")
    await state.clear()
    await show_main_menu(message, await get_user_direction(message.from_user.id))


@dp.message()
async def fallback(message: Message):
    await ensure_user(message)
    await message.answer(
        "Я пока не поняла это сообщение. Выберите действие через меню или используйте команды:\n"
        "/daily_insight\n/emotion_card\n/mindfulness_exercise\n/submit_homework\n/feedback"
    )


async def send_daily_quote():
    user_ids = await db.get_active_user_ids()
    if not user_ids:
        logger.info("No active users for daily quote")
        return

    quote = await generate_gpt_text(build_quote_prompt())
    delivered = 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, f"☀️ Утренний инсайт\n\n{quote}")
            delivered += 1
        except Exception:
            logger.exception("Failed to send quote to user_id=%s", user_id)
    logger.info("Daily quote delivered to %s users", delivered)


async def on_startup():
    await db.connect()
    scheduler.add_job(send_daily_quote, trigger="cron", hour=10, minute=0, id="daily_quote", replace_existing=True)
    scheduler.start()
    logger.info("Bot started successfully")


async def on_shutdown():
    scheduler.shutdown(wait=False)
    await db.close()
    await bot.session.close()
    logger.info("Bot stopped")


async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
