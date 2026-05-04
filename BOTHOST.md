# Деплой на Bothost

## Перед загрузкой

Проверьте проект локально:

```text
python -m pip install -r requirements.txt
python -m compileall -q bot db services scripts config.py
python scripts/smoke.py
```

## Настройки в Bothost

Используйте Docker-деплой из `Dockerfile`.

Переменные окружения:

```text
BOT_TOKEN=токен_от_BotFather
MASTER_TELEGRAM_IDS=123456789
TIMEZONE=Europe/Moscow
DATABASE_URL=sqlite+aiosqlite:///./data/nailsbot.db
```

Для SQLite обязательно подключите постоянное хранилище к `/app/data`.

## Проверка после запуска

1. В логах контейнера должна быть нормальная загрузка без traceback.
2. В Telegram отправьте боту `/start`.
3. С Telegram ID из `MASTER_TELEGRAM_IDS` должна появиться кнопка `Мастер`.
4. Через `Мастер` добавьте услугу и несколько слотов.
5. С другого Telegram-аккаунта сделайте запись.
6. Проверьте уведомление мастеру.
7. Проверьте `Мои записи` и перенос.
8. Проверьте статистику в панели мастера.

## Бэкапы SQLite

Перед обновлениями и периодически запускайте:

```text
python scripts/backup_sqlite.py
```

Копии сохраняются в `data/backups/`.

## Следующая итерация

- Настроить регулярный бэкап через возможности Bothost.
- Добавить напоминания клиентам за день/час до записи.
- Перейти на PostgreSQL, если записей станет много или нужен более надёжный продакшен.
