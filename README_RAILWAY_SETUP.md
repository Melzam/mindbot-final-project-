# Психологический Telegram-бот — production версия для GitHub + Railway

Эта версия собрана под нормальный деплой через GitHub + Railway:
- Python + aiogram 3
- OpenAI API
- PostgreSQL через `DATABASE_URL`
- Railway Variables
- ежедневная рассылка по Москве
- сохранение пользователей, заданий и отзывов в PostgreSQL

## Что реализовано

- приветствие и выбор направления: эмоции / отношения / самооценка / стресс-менеджмент
- команды:
  - `/daily_insight`
  - `/emotion_card`
  - `/mindfulness_exercise`
  - `/submit_homework`
  - `/feedback`
- кнопочное меню тоже сохранено
- генерация текстов через OpenAI
- генерация изображения эмоции
- сохранение домашних заданий в БД
- пересылка домашних заданий админу
- сохранение обратной связи в БД
- уведомление администратора о новых отзывах
- ежедневный инсайт пользователям в 10:00 по Москве

## Какие файлы заменить в проекте

- `main.py` → взять из `psybot_production_main.py`
- `requirements.txt` → взять из `requirements_production.txt`

## Railway Variables

Нужно добавить:

- `TELEGRAM_TOKEN`
- `OPENAI_API_KEY`
- `ADMIN_CHAT_ID`
- `DATABASE_URL`
- `OPENAI_TEXT_MODEL` — опционально, по умолчанию `gpt-4o-mini`
- `OPENAI_IMAGE_MODEL` — опционально, по умолчанию `gpt-image-1`
- `LOG_LEVEL` — опционально, например `INFO`

## PostgreSQL в Railway

Лучше подключить отдельный PostgreSQL service внутри Railway.
После подключения Railway обычно сам подставляет `DATABASE_URL`.

## Start Command

Если Railway не определит автоматически, укажи:

```bash
python main.py
```

## Procfile

Если нужен Procfile, можно использовать:

```Procfile
worker: python main.py
```

## Что показать заказчику как кейс

- бот на Python
- async-архитектура
- интеграция с OpenAI
- генерация текстов и изображений
- пользовательские сценарии через FSM
- хранение данных в PostgreSQL
- деплой через GitHub + Railway
- ежедневные рассылки по расписанию

## Ограничения

- бот не ставит диагнозы и не даёт медицинских рекомендаций
- для изображений нужен доступ OpenAI-аккаунта к image generation
- если бот должен работать в production на большой аудитории, дальше можно добавить:
  - Alembic для миграций
  - отдельный модуль конфигурации
  - разбиение по папкам `handlers/`, `services/`, `db/`
  - Dockerfile
  - healthcheck
  - sentry/log aggregation
