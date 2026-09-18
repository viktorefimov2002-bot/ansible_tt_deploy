# Control Plane: локальный runtime (TTCP-003/004)

Основание: [архитектура](../../docs/architecture/product-architecture-spec-v0.1.md),
разделы 4, 23–24, 28, 32–33, и [план MVP](../../docs/planning/mvp.md).
Это среда разработки на Linux containers, не готовое production-развёртывание.
Нужны Docker Engine / Docker Desktop и Docker Compose v2.20+ (`up --wait`).
Существующий [Ansible/CLI](../../automation/ansible/README.md) работает независимо.

## Запуск

Все команды ниже выполняются из `infra/compose/`:

```sh
cp .env.example .env
# PowerShell: Copy-Item .env.example .env
```

Заполните три разных случайных пароля в `.env` (например, 32 случайных байта
в hex для каждого). В Bash можно использовать `openssl rand -hex 32`.
Ограничьте доступ к файлу: `chmod 600 .env` в Linux; в Windows — ACL владельца.
Не сохраняйте секреты в истории команд. Пустые пароли блокируют Compose.

```sh
docker compose config --quiet
docker compose pull --ignore-buildable
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
docker compose logs --tail=100
docker compose logs -f redis
docker compose stop
docker compose start --wait
docker compose down
```

API/worker собираются из `apps/Dockerfile`; Python dependencies зафиксированы в
`requirements-control-plane.lock`. Остальные образы используют явные версии.
`stop` и `down` сохраняют named volumes. `depends_on: service_healthy` управляет
порядком старта, но не восстанавливает соединения приложений при последующих
сбоях; API/worker повторно проверяют подключения и восстанавливаются при возврате
зависимостей. `unless-stopped` перезапускает
завершившийся процесс, но само по себе состояние `unhealthy` не перезапускает его.

## Конфигурация

| Переменная | По умолчанию / назначение |
| --- | --- |
| `POSTGRES_DB` | `ttcp`, начальная БД |
| `POSTGRES_USER` | `ttcp`, bootstrap superuser только для локального runtime |
| `POSTGRES_PASSWORD` | Обязательный секрет, без значения по умолчанию |
| `REDIS_PASSWORD` | Обязательный секрет, без значения по умолчанию |
| `GRAFANA_ADMIN_USER` | `admin`, начальный администратор Grafana |
| `GRAFANA_ADMIN_PASSWORD` | Обязательный секрет, без значения по умолчанию |
| `HTTP_PORT` | `8080`, порт NGINX на loopback |
| `GRAFANA_PORT` | `3000`, порт Grafana на loopback |
| `VM_RETENTION` | `7d`, retention метрик |
| `TTCP_LOG_LEVEL` | `INFO`, JSON application logs |

`.env` игнорируется Git. Передавайте переменные через защищённое окружение
при необходимости; не публикуйте вывод `docker compose config` без `--quiet`
или `docker inspect`: они могут содержать секреты. Docker-администратор имеет
доступ к environment контейнеров. API/worker получают PostgreSQL/Redis credentials
через общую секцию environment. Ограниченная application role остаётся TTCP-005;
bootstrap использует существующую dev-role. Для production это требует пересмотра.

PostgreSQL применяет `POSTGRES_*` только при первом запуске на пустом томе.
Изменение `.env` не меняет пароль существующей роли и не переименовывает БД.
Для ротации нужен `ALTER ROLE` через защищённую административную сессию;
для одноразовой dev-среды можно явно сбросить данные (ниже).
Начальный пароль Grafana также не сбрасывает существующего администратора.

## Топология

| Сервис | Внутренний порт | Доступ с хоста | Сеть | Зависимости |
| --- | --- | --- | --- | --- |
| `nginx` | 8080 | `127.0.0.1:8080` | entrypoint, application | healthy api |
| `api` (FastAPI) | 8080 | Нет | application, database, queue | healthy postgres, redis |
| `worker` (bootstrap) | Нет | Нет | database, queue | healthy postgres, redis |
| `postgres` | 5432 | Нет | database | Нет |
| `redis` | 6379 | Нет | queue | Нет |
| `victoriametrics` | 8428 | Нет | metrics | Нет |
| `grafana` | 3000 | `127.0.0.1:3000` | entrypoint, metrics | healthy victoriametrics |

Все сети кроме `entrypoint` имеют `internal: true`. Сети database и queue
доступны только соответствующему хранилищу и API/worker. NGINX и Grafana
не подключены к ним. Сети не являются защитой от администратора Docker.
API/worker проверяют SQL `SELECT 1` и Redis `PING`, без business state.
Доступ worker к managed nodes добавляется с execution adapter.

NGINX `/healthz` — HTTP 200; `/` — HTTP 503 до появления frontend.
`/api/healthz` — liveness API; `/api/readyz` — HTTP 200 при доступности PostgreSQL
и Redis, иначе 503. Несуществующие API routes — 404. Healthcheck API проверяет
readiness; worker — подключения отдельным probe-процессом (не прогресс jobs).
Контракты, settings и lifecycle описаны в [apps/README.md](../../apps/README.md).
TLS/ACME, реальные UI bundles и публичный доступ не входят в локальный runtime.
Для production потребуется отдельная настройка NGINX/TLS и секретов.

## Хранение и лимиты

По умолчанию Compose использует проект `ttcp-dev` и тома:

- `ttcp-dev_postgres_data` → `/var/lib/postgresql/data`, durable source of truth;
- `ttcp-dev_victoriametrics_data` → `/victoria-metrics-data`, метрики;
- `ttcp-dev_grafana_data` → `/var/lib/grafana`, локальная SQLite/config Grafana.

Redis использует tmpfs, без RDB/AOF. `maxmemory=256mb`, `noeviction`: при
переполнении записи отклоняются, существующие ключи не вытесняются автоматически.
ACL требует пароль, сохраняет только его SHA-256 в tmpfs; исходный пароль
не передаётся в аргументах redis-server. Начальная ACL разрешает все команды
аутентифицированному клиенту в изолированной dev-сети. Разделение ACL/TTL и
поведение при утрате очереди определяются TTCP-007. До внедрения jobs необходимо
реализовать восстановление из PostgreSQL и решить, нужна ли AOF; Redis не является
source of truth. Данные Redis теряются при пересоздании контейнера.

Контейнеры ограничены по памяти: PostgreSQL/Redis/VictoriaMetrics — по 384 MiB,
Grafana — 256 MiB, NGINX — 64 MiB, API — 192 MiB, worker — 128 MiB. Логи ротируются
(3 × 10 MiB на контейнер). Для Docker Desktop выделите минимум 2 GiB памяти;
для дальнейшего развития предпочтительны 4 GiB. Retention VM ограничивает время,
но не абсолютный размер диска. Named volumes не заменяют резервную копию.
Remote encrypted backups и restore workflow остаются эксплуатационным этапом.

Grafana включена согласно фазе 1 спецификации. Единственный provisioned datasource
использует встроенный Prometheus-compatible тип и `http://victoriametrics:8428`.
VictoriaMetrics собирает только собственные метрики; exporters, дашборды,
алерты и метрики приложения оставлены TTCP-011.

## Проверки и сброс

```sh
docker compose exec postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT current_database(), current_user;"'
docker compose exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
docker compose exec redis redis-cli ping
# Без пароля ожидается NOAUTH.
docker compose exec victoriametrics wget -qO- http://127.0.0.1:8428/health
docker compose exec grafana wget -qO- http://127.0.0.1:3000/api/health
docker compose exec nginx nginx -t
docker compose exec api python -m apps.shared.healthcheck api
docker compose exec worker python -m apps.shared.healthcheck worker
```

HTTP с хоста: `http://127.0.0.1:8080/healthz`; Grafana:
`http://127.0.0.1:3000` (логин из `.env`). Для PowerShell в командах выше
сохраняйте одинарные кавычки, чтобы переменные раскрывались внутри контейнера.

Автоматическая runtime-проверка из корня репозитория, Bash + Docker Compose:

```sh
bash infra/compose/tests/runtime_smoke.sh
```

Тест создаёт отдельный проект `ttcp004-test-*`, временный env и собственные тома;
проверяет startup/shutdown API и worker, readiness при сбое/восстановлении хранилищ,
отказ без секретов, Redis auth, VM, Grafana, SQL через TCP
и сохранение записи после пересоздания PostgreSQL. По завершении удаляет **только
свой тестовый проект и его данные**. Основной `ttcp-dev` не затрагивается.

Сброс **всех локальных данных основного dev-стека**, только по явному намерению,
из `infra/compose/`:

```sh
docker compose down --volumes
docker compose up -d --wait --wait-timeout 180
```

Обновления image tags выполняйте осознанно, с проверкой release notes и backup.
Нельзя просто заменить major PostgreSQL поверх старого тома: нужен pg_upgrade
или dump/restore. Версии зафиксированы тегами, не digest; pull/build и реальные
healthchecks должны быть проверены в Docker-окружении перед использованием.

## Границы задачи

Нет business endpoints, auth/RBAC, схемы приложения/migrations, JobQueue,
EventPublisher, LockProvider, Ansible adapter, CRUD, node bootstrap, VPN users/devices.
Это TTCP-005–012 и последующие этапы. Worker library остаётся открытым ADR-решением
для TTCP-007. Kafka, Vault и Kubernetes не добавляются.
Новых архитектурных решений вне baseline нет; отдельный ADR для локальной
конфигурации не требуется. Нумерованных принятых ADR пока нет в реестре.

Справочные первоисточники: [PostgreSQL image](https://hub.docker.com/_/postgres),
[Redis image](https://hub.docker.com/_/redis),
[NGINX image](https://hub.docker.com/_/nginx),
[VictoriaMetrics](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/),
[Grafana Docker](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/).
