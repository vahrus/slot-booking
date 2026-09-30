# Slotly — Техническая спецификация v1.0

Версия спецификации: 1.0
Состояние реализации: Iteration 5.0 — Cancellation & Conflicts

Slotly v1.0 реализует ядро учебного MVP для онлайн-бронирования временных
слотов. Проект не является полноценной production-системой управления записью:
он фиксирует базовые доменные правила, persistence, HTML UI и критичные
инварианты бронирования.

Инструкции запуска и демонстрации см. в `README.md`.

## Назначение продукта

Slotly — простой веб-сервис онлайн-записи для малого бизнеса.

Основной сценарий:

```text
Владелец создаёт доступные временные слоты
        ↓
Клиент выбирает дату и свободное время
        ↓
Клиент вводит имя и контакт
        ↓
Создаётся Booking
        ↓
Slot становится booked
        ↓
Клиент получает подтверждение
        ↓
Booking можно отменить
        ↓
Slot снова становится free
```

## Scope v1.0

Реализовано:

- создание `Slot`;
- просмотр расписания;
- фильтрация расписания по дате;
- создание `Booking`;
- хранение имени клиента;
- хранение контакта клиента;
- статус слота `free` / `booked`;
- предотвращение duplicate `Slot`;
- предотвращение double booking;
- success page после бронирования;
- cancellation flow с confirmation step;
- возврат `Slot` в `free` после cancellation;
- повторное бронирование освобождённого `Slot`;
- public UI;
- demo admin UI;
- SQLite persistence;
- Pydantic validation;
- controlled domain/UI errors.

## Out of Scope

В v1.0 не реализованы:

- authentication;
- authorization;
- user accounts;
- admin accounts;
- multiple organizations;
- branches;
- employees;
- multiple services;
- booking rescheduling;
- booking editing;
- slot editing;
- slot deletion;
- automatic recurring schedule generation;
- email notifications;
- SMS notifications;
- Telegram notifications;
- payments;
- calendar integrations;
- waitlist;
- PostgreSQL;
- Redis;
- Docker;
- production-grade distributed concurrency controls.

`/admin` является demo-интерфейсом без защиты авторизацией.

## Technology Stack

Фактически используемый стек:

- Python 3.11+;
- FastAPI;
- Uvicorn;
- Jinja2;
- SQLAlchemy 2.x;
- SQLite;
- Pydantic;
- HTML5;
- CSS3;
- pytest.

Supporting dependencies из `requirements.txt`:

- `python-multipart` — обработка HTML form data;
- `httpx` — тестовый HTTP-клиент через FastAPI/TestClient stack.

## Architecture

Текущая архитектура — простая layered architecture:

```text
Browser
   ↓
FastAPI Router
   ↓
Service Layer
   ↓
SQLAlchemy ORM
   ↓
SQLite
```

### Router Layer

`app/routers/public.py` и `app/routers/admin.py` отвечают за:

- HTTP routes;
- path/query parameters;
- HTML form fields;
- template rendering;
- redirects;
- преобразование domain errors в HTTP/UI responses.

### Service Layer

`app/services/slots.py` и `app/services/bookings.py` отвечают за:

- business rules;
- создание `Slot`;
- создание `Booking`;
- cancellation;
- transaction boundaries;
- rollback;
- domain conflicts.

Business logic должна оставаться в service layer, а не переноситься в
templates.

### Database Layer

`app/database/` отвечает за:

- SQLAlchemy engine/session setup;
- ORM base;
- ORM models;
- relationships;
- database constraints;
- включение SQLite foreign keys.

### Templates

`app/templates/` отвечает за:

- HTML presentation;
- формы;
- пользовательскую обратную связь;
- отображение статусов и ошибок.

## Project Structure

Актуальная значимая структура:

```text
app/
  __init__.py
  main.py
  database/
    __init__.py
    database.py
    models.py
  schemas/
    __init__.py
    booking.py
    slot.py
  services/
    __init__.py
    bookings.py
    slots.py
  routers/
    __init__.py
    admin.py
    public.py
  static/
    style.css
  templates/
    admin.html
    booking.html
    cancel.html
    index.html
    not_found.html
    success.html
data/
  .gitkeep
  booking.db
docs/
  TECH_SPEC.md
tests/
  __init__.py
  test_bookings.py
  test_database.py
  test_slots.py
README.md
requirements.txt
```

Не включены служебные директории `.venv`, `__pycache__`, `.pytest_cache`.

## Application Startup

`app/main.py` создаёт `FastAPI(title="Slotly")`, монтирует static files из
`app/static`, подключает public/admin routers и на lifespan startup вызывает
`init_db()`.

`GET /health` возвращает:

```json
{"status": "ok", "app": "Slotly"}
```

## Database

Production SQLite database:

```text
data/booking.db
```

Фактическое поведение:

- директория `data/` создаётся автоматически;
- таблицы создаются через `Base.metadata.create_all(bind=engine)`;
- `data/booking.db` исключён из Git;
- `SessionLocal` использует `autoflush=False` и `expire_on_commit=False`;
- SQLite engine создаётся с `check_same_thread=False`;
- foreign key enforcement включается на каждое подключение через
  `PRAGMA foreign_keys=ON`;
- tests используют отдельные SQLite databases в `tmp_path` и не используют
  `data/booking.db`.

## Model: Slot

SQLAlchemy model: `app.database.models.Slot`

Table: `slots`

| Field | Type | Constraints |
| --- | --- | --- |
| `id` | Integer | primary key, autoincrement |
| `date` | Date | not null |
| `time` | Time | not null |
| `status` | String(20) | not null, Python default `free`, server default `free` |

ORM relationship:

```text
Slot.booking
```

`Slot.booking` is `uselist=False`, so the ORM model represents:

```text
Slot 1 → 0..1 Booking
```

## Slot Status

Допустимые бизнес-состояния в текущей реализации:

```text
free   → слот доступен для бронирования
booked → слот занят
```

Не существует статусов `cancelled`, `pending`, `reserved`.

## Slot Uniqueness

Database constraint:

```text
UNIQUE(slots.date, slots.time)
```

Фактическое имя:

```text
uq_slots_date_time
```

Два `Slot` с одинаковыми `date` и `time` создать нельзя. Service layer
перехватывает `IntegrityError`, выполняет `rollback` и поднимает
`SlotAlreadyExistsError`.

## Model: Booking

SQLAlchemy model: `app.database.models.Booking`

Table: `bookings`

| Field | Type | Constraints |
| --- | --- | --- |
| `id` | Integer | primary key, autoincrement |
| `slot_id` | Integer | not null, foreign key to `slots.id` |
| `client_name` | String(200) | not null |
| `client_contact` | String(200) | not null |
| `created_at` | DateTime | not null, server default `current_timestamp()` |

ORM relationship:

```text
Booking.slot
```

## Booking Constraints

Database constraints:

```text
UNIQUE(bookings.slot_id)
FOREIGN KEY bookings.slot_id → slots.id
```

Фактическое имя unique constraint:

```text
uq_bookings_slot_id
```

`UNIQUE(bookings.slot_id)` является database-level последней линией защиты от
double booking. `FOREIGN KEY` защищает от бронирования несуществующего `Slot`;
в SQLite enforcement включён через `PRAGMA foreign_keys=ON`.

## Data Invariants

После завершённой business operation допустимы только согласованные состояния.

### Free

```text
Slot.status = free
Booking отсутствует
```

### Booked

```text
Slot.status = booked
Booking существует
```

Недопустимые устойчивые состояния:

```text
Slot.status = free
Booking существует
```

```text
Slot.status = booked
Booking отсутствует
```

`create_booking()` и `cancel_booking()` поддерживают эти invariants
транзакционно.

## Validation

### SlotCreate

Schema: `app.schemas.slot.SlotCreate`

Fields:

- `date: datetime.date`;
- `time: datetime.time`.

Pydantic выполняет parsing/validation даты и времени. Ошибка формы создания
слота возвращается в admin UI со статусом `400`.

### BookingCreate

Schema: `app.schemas.booking.BookingCreate`

Fields:

- `client_name: str`;
- `client_contact: str`.

Фактические ограничения:

- оба поля required;
- значения перед validation обрезаются через `strip()`;
- whitespace-only значения invalid;
- `client_name` maximum length: `100`;
- `client_contact` maximum length: `100`;
- user-facing validation messages возвращаются в HTML form.

Database columns имеют `String(200)`, но Pydantic schema ограничивает input до
100 символов для каждого поля.

## Slot Creation Flow

Фактический flow:

```text
POST /admin/slots
    ↓
Pydantic validation через SlotCreate
    ↓
create_slot(session, slot_data)
    ↓
INSERT Slot(status="free")
    ↓
commit
    ↓
refresh
    ↓
303 redirect to /admin?...created=1
```

Duplicate handling:

```text
UNIQUE(date, time) violation
    ↓
IntegrityError
    ↓
rollback
    ↓
SlotAlreadyExistsError
    ↓
303 redirect to /admin?...error=duplicate
    ↓
controlled UI error
```

Database `UNIQUE(date, time)` остаётся окончательной защитой от duplicate slot.

## Booking Creation Flow

Service function:

```text
create_booking(session, slot_id, booking_data)
```

Фактический алгоритм:

```text
Получить Slot по slot_id
    ↓
Slot существует?
    ↓
Slot.status == free?
    ↓
создать Booking
+
Slot.status = booked
    ↓
один commit
    ↓
refresh Booking
```

Если `Slot` отсутствует, service поднимает `SlotNotFoundError`.

Если `Slot.status != "free"`, service поднимает `SlotNotAvailableError`.

Если database constraint ловит conflict, service выполняет `rollback` и
поднимает `BookingConflictError`.

## Double-Booking Protection

Double booking предотвращается двумя слоями.

### Application Level

`create_booking()` проверяет:

```text
Slot.status == "free"
```

Если слот уже занят, пользователь получает controlled conflict response.

### Database Level

Database constraint:

```text
UNIQUE(bookings.slot_id)
```

Conflict handling:

```text
IntegrityError
    ↓
rollback
    ↓
BookingConflictError
    ↓
409 controlled HTTP/UI response
```

SQLite не используется как distributed locking/concurrency architecture. Для
учебного MVP текущая защита состоит из application-level availability check,
database unique constraint и controlled rollback/error handling.

## Cancellation Flow

Service function:

```text
cancel_booking(session, booking_id)
```

Фактический алгоритм:

```text
Получить Booking вместе со Slot
      ↓
Booking существует?
      ↓
DELETE Booking
+
Slot.status = free
      ↓
один commit
      ↓
refresh Slot
```

Если `Booking` отсутствует, service поднимает `BookingNotFoundError`.

Если commit cancellation не выполнен, service выполняет `rollback` и поднимает
`BookingCancellationError`.

Cancellation не удаляет `Slot`. После успешной отмены исходный `Slot` остаётся
в базе и снова доступен для бронирования.

## Cancellation HTTP Semantics

Confirmation route:

```text
GET /booking/{booking_id}/cancel
```

GET route только отображает `cancel.html` и не изменяет данные.

Mutation route:

```text
POST /booking/{booking_id}/cancel
```

После успешной cancellation используется PRG:

```text
POST
 ↓
cancel_booking()
 ↓
303 redirect
 ↓
GET schedule
```

Redirect target зависит от ограниченного internal source:

```text
source=public → /?date=YYYY-MM-DD&cancelled=1
source=admin  → /admin?date=YYYY-MM-DD&cancelled=1
```

`source` нормализуется только к заранее разрешённым значениям `public` и
`admin`; произвольный redirect URL не принимается.

## Rebooking Invariant

Поддерживаемый сценарий:

```text
Slot free
 ↓
Booking A
 ↓
Slot booked
 ↓
cancel Booking A
 ↓
Slot free
 ↓
Booking B
 ↓
Slot booked
```

В каждый момент после завершённой операции для одного `Slot` существует максимум
один актуальный `Booking`.

## Transaction Boundaries

### Booking

```text
INSERT Booking
+
Slot.status = booked
→ one commit
```

При `IntegrityError`:

```text
rollback
```

### Cancellation

```text
DELETE Booking
+
Slot.status = free
→ one commit
```

При `SQLAlchemyError` во время commit:

```text
rollback
```

## Routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` | Public schedule page, optional `date` query filter |
| `GET` | `/health` | Application health-check |
| `GET` | `/admin` | Demo admin page for slot creation and schedule overview |
| `POST` | `/admin/slots` | Validate and create a new `Slot` |
| `GET` | `/booking/{slot_id}` | Show booking form for a free slot |
| `POST` | `/booking/{slot_id}` | Validate form and create `Booking` |
| `GET` | `/booking/{booking_id}/success` | Show saved booking confirmation |
| `GET` | `/booking/{booking_id}/cancel` | Show cancellation confirmation |
| `POST` | `/booking/{booking_id}/cancel` | Cancel booking and redirect |

## HTTP Error Behavior

Фактически реализованные responses:

- `GET /` с некорректной датой: `200` with inline error message;
- `GET /booking/{slot_id}` для missing slot: `404`;
- `GET /booking/{slot_id}` для booked slot: `409`;
- `POST /booking/{slot_id}` с invalid client form: `400`, если слот свободен;
- `POST /booking/{slot_id}` с invalid client form для занятого слота: `409`;
- `POST /booking/{slot_id}` для missing slot: `404`;
- `POST /booking/{slot_id}` для unavailable/conflict slot: `409`;
- `GET /booking/{booking_id}/success` для missing booking: `404`;
- `GET /booking/{booking_id}/cancel` для missing booking: `404`;
- `POST /booking/{booking_id}/cancel` для missing/already cancelled booking: `404`;
- `POST /booking/{booking_id}/cancel` при cancellation commit error: `409`;
- `POST /admin/slots` с invalid date/time: `400`;
- duplicate slot через admin form: `303` redirect with controlled error flag.

Все эти responses возвращают HTML, не JSON API contract.

## Post/Redirect/Get

PRG используется для успешных mutating operations:

- `POST /admin/slots` → `303` → `GET /admin?...`;
- `POST /booking/{slot_id}` → `303` → `GET /booking/{booking_id}/success`;
- `POST /booking/{booking_id}/cancel` → `303` → schedule page.

Цель — предотвратить повторную отправку POST при refresh.

## Public UI

`GET /` отображает public schedule:

- пользователь выбирает дату;
- отображаются слоты выбранной даты;
- слоты сортируются по времени ascending;
- статус отображается как `Свободно` / `Занято`;
- action `Записаться` доступен только для `free` slots;
- booked slots не имеют активного booking action;
- public schedule не показывает `client_name` или `client_contact`.

После cancellation public schedule может показать message:

```text
Запись отменена. Время снова доступно для бронирования.
```

## Booking UI

Фактические templates:

- `booking.html` — форма ввода имени и контакта для свободного слота; также
  отображает conflict state для занятого слота.
- `success.html` — подтверждение сохранённой записи; показывает дату, время,
  имя, контакт и action `Отменить запись`.
- `cancel.html` — confirmation page для destructive cancellation; показывает
  дату, время, имя, кнопку `Оставить запись` и POST form `Отменить запись`.
- `not_found.html` — общая HTML-страница controlled not found/error states.

## Admin UI

`GET /admin` — demo admin interface without authentication.

Доступные действия:

- создать `Slot` через HTML form;
- выбрать дату для просмотра расписания;
- увидеть статусы `Свободно` / `Занято`;
- для booked slot перейти к cancellation confirmation через
  `/booking/{booking_id}/cancel?source=admin`;
- после admin cancellation вернуться в `/admin?date=...&cancelled=1`.

Admin UI не является защищённой административной панелью.

## Security and Privacy

Текущие базовые меры:

- HTML rendering выполняется через Jinja2 templates;
- autoescape включён для `.html` templates;
- в templates не используется `|safe` для client data;
- public schedule не показывает `client_name` и `client_contact`;
- success page загружает booking по `booking_id`, а не получает client data
  через query parameters;
- cancellation не выполняется GET-запросом;
- destructive action требует server-rendered confirmation page и POST form;
- redirect/source для cancellation ограничен значениями `public` и `admin`.

Ограничения MVP:

- `/admin` не защищён authentication;
- booking success/cancel pages доступны по знанию numeric `booking_id`;
- нет CSRF protection;
- нет user/session ownership model.

## Testing

Фактический regression result на момент синхронизации:

```text
48 passed
```

Категории тестов:

- database tests;
- slot service tests;
- slot route tests;
- booking service tests;
- booking route/integration tests;
- cancellation tests;
- conflict tests;
- rollback/invariant tests.

Tests используют временные SQLite databases через `tmp_path`,
`create_sqlite_engine()` и отдельные `sessionmaker` instances. Production
database `data/booking.db` не используется тестами.

## Critical Tested Scenarios

Покрытые критичные сценарии:

- `Slot` creation;
- duplicate `Slot`;
- сортировка слотов по времени;
- `Booking` creation;
- nonexistent `Slot`;
- booked `Slot`;
- double booking prevention;
- transaction rollback;
- cancellation;
- `Slot` survives cancellation;
- `GET` cancellation does not mutate data;
- repeated cancellation;
- rebooking after cancellation;
- public schedule state;
- admin state.

## Current Implementation Status

| Iteration | Status |
| --- | --- |
| Iteration 1.0 — Foundation | Done |
| Iteration 2.0 — Database | Done |
| Iteration 3.0 — Slots | Done |
| Iteration 4.0 — Booking | Done |
| Iteration 5.0 — Cancellation & Conflicts | Done |
| Iteration 6.0 — Client UI | Planned |
| Iteration 7.0 — Admin UI | Planned |
| Iteration 8.0 — QA & Documentation | Planned |

## Future Iterations

### Iteration 6.0

Client UI polish.

### Iteration 7.0

Admin UI polish.

### Iteration 8.0

Final QA, documentation, screenshots, submission readiness.
