# Control Plane bootstrap и persistence — TTCP-004/005

FastAPI API (`apps/api`), job worker (`apps/worker`) и общие settings, JSON logs,
PostgreSQL/Redis clients (`apps/shared`). Основание — разделы 4, 23, 28, 30, 33
[архитектуры](../docs/architecture/product-architecture-spec-v0.1.md) и
[план MVP](../docs/planning/mvp.md). Детальный scope TTCP-004 задан поручением:
только application/bootstrap infrastructure. TTCP-005 добавляет SQLAlchemy core models,
Alembic migrations и transaction helper в `apps/persistence`;
[схема и команды](../docs/persistence.md). CRUD, execution adapter,
и frontend отсутствуют. TTCP-007 добавляет [durable jobs и SSE logs](../docs/jobs.md).
TTCP-006 добавляет административную аутентификацию и RBAC;
см. [настройку и HTTP-контракт](../docs/authentication.md).

## Запуск

Рекомендуемый способ — [Compose](../infra/compose/README.md): API и worker собираются
одним Dockerfile, работают от UID 65534, без writable root filesystem и host ports.
API доступен через NGINX на `http://127.0.0.1:8080/api/healthz` и `/api/readyz`.

Для локальной разработки требуется Python 3.12+ и доступные PostgreSQL/Redis.
Зависимости Ansible в корневом `requirements.txt` не меняются.
Из корня проекта:

```sh
python -m venv .venv
# Linux: source .venv/bin/activate
# PowerShell: .venv/Scripts/Activate.ps1
python -m pip install -r requirements-control-plane-dev.lock
python -m apps.api
# В другом терминале с тем же окружением:
python -m apps.worker
```

Перед запуском передайте переменные через защищённое окружение. Приложение само
не читает `.env`; Compose передаёт только явно перечисленные переменные.
Пароли никогда не включайте в командную строку, source files или normal logs.

| Переменная | Значение / назначение |
| --- | --- |
| `TTCP_POSTGRES_HOST`, `TTCP_POSTGRES_DB`, `TTCP_POSTGRES_USER` | Обязательны |
| `TTCP_POSTGRES_PASSWORD`, `TTCP_REDIS_PASSWORD` | Обязательные непустые секреты |
| `TTCP_REDIS_HOST` | Обязателен |
| `TTCP_POSTGRES_PORT`, `TTCP_REDIS_PORT` | 5432, 6379 |
| `TTCP_DEPENDENCY_TIMEOUT` | 2 s на одну проверку, диапазон 0.1–10 |
| `TTCP_WORKER_CHECK_INTERVAL` | 5 s между проверками, диапазон 1–60 |
| `TTCP_API_HOST`, `TTCP_API_PORT` | 0.0.0.0, 8080 |
| `TTCP_LOG_LEVEL` | INFO; DEBUG, WARNING, ERROR также допустимы |

Compose использует прежние `POSTGRES_*`/`REDIS_PASSWORD`, отображая их в `TTCP_*`.
Отдельные поля подключения позволяют передавать спецсимволы в пароле без ручного
URL-encoding. Ошибки settings содержат только имена отсутствующих/неверных полей.

## Жизненный цикл и health

Оба процесса при старте проверяют PostgreSQL (`SELECT 1`) и Redis (`PING`). Если
проверка не прошла, процесс завершает startup с ошибкой. В Compose перезапуском
управляет `unless-stopped`. Соединения ограничены маленькими пулами и таймаутами.
SQLAlchemy pool pre-ping и новые попытки Redis позволяют восстановиться после сбоя.

| Endpoint API | Контракт |
| --- | --- |
| `/healthz`, `/api/healthz` | 200 `{"status":"ok"}`; liveness, без обращения к хранилищам |
| `/readyz`, `/api/readyz` | Проверка обоих хранилищ: 200 `ready` либо 503 `not_ready` |

Readiness не раскрывает адреса, имена БД, credentials или исключения драйверов.
Эти read-only endpoints публичны; бизнес-endpoints и документация API не включены.
`/healthz` NGINX проверяет только NGINX; для приложения используйте `/api/readyz`.

Worker ждёт SIGTERM/SIGINT, периодически проверяет подключения и логирует изменения
готовности и исполняет durable jobs согласно [контракту worker](../docs/jobs.md). Healthcheck
worker запускает отдельную проверку подключений; он **не доказывает прогресс job
consumer**. Контейнер проверяет завершение основного процесса.
API использует FastAPI lifespan; shutdown закрывает Redis и SQLAlchemy pools.
Worker закрывает их при stop и startup failure. Compose даёт 30 s на остановку.
Uvicorn может повторно поднять SIGTERM после cleanup: код 143 вместе с событием
`api_stopped` считается штатным завершением; SIGKILL/137 — ошибкой остановки.

JSON logs идут в stdout: timestamp, level, service, logger, event и безопасные поля
dependency/state/error_type. Raw driver errors/tracebacks, settings, query strings,
HTTP headers и request bodies не логируются. Uvicorn access log выключен.

## Проверки

```sh
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
bash infra/compose/tests/runtime_smoke.sh
bash automation/ansible/tests/layout_smoke.sh
```

Unit/component tests не требуют серверов. Compose smoke использует отдельные
одноразовые БД/volumes: проверяет настоящий startup обоих процессов, SQL, Redis,
ошибки settings/dependencies, потерю/восстановление подключений, SIGTERM, routing,
сохранность PostgreSQL и monitoring из TTCP-003. Не запускайте его против production.

Lock-файлы создаются из pyproject, не редактируются вручную:

```sh
uv pip compile pyproject.toml --python-version 3.12 --universal -o requirements-control-plane.lock
uv pip compile pyproject.toml --python-version 3.12 --universal --extra dev -o requirements-control-plane-dev.lock
```

## Открытые решения

Worker использует asyncio/redis-py за JobQueue/EventPublisher, с durable state и
координацией в PostgreSQL: [ADR-0013](../docs/adr/0013-durable-job-worker.md).
Redis LockProvider пока не требуется.
Миграции и SQL grants ограниченной PostgreSQL application role описаны в
[TTCP-005 persistence](../docs/persistence.md). В изолированном dev-stack без
APP-переменных сохраняется bootstrap role TTCP-003. TLS подключения
к хранилищам вне изолированной dev-сети потребует отдельной конфигурации.

API lifecycle следует [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
cleanup pools — [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
и [redis-py asyncio](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).
