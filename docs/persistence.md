# PostgreSQL core — TTCP-005

Основание: разделы 9–12, 17, 21–23, 32–33
[архитектуры](architecture/product-architecture-spec-v0.1.md),
[план](planning/mvp.md) и scope TTCP-005 из задания.
SQLAlchemy models находятся в `apps/persistence/models.py`, Alembic — в `migrations/`.
TTCP-007 добавляет [durable jobs, worker и inspection API](jobs.md).

TTCP-006 добавляет [administrative authentication](authentication.md): MFA/lockout
поля `admins` и таблицу `admin_sessions` (FK на admins, уникальный SHA-256 digest,
expiry/revocation). Пароль остаётся nullable для unenrolled identities; такие записи
не допускаются к login. TTCP-007 добавляет `jobs` с durable intent, fenced attempts
и bounded history. Действующая миграция — `0004_jobs`.

## Состав baseline

| Таблица | Назначение и ограничения |
| --- | --- |
| `admins` | Отдельные операторы, уникальный case-sensitive username, role `admin`/`viewer`. По умолчанию viewer и disabled; password_hash nullable до TTCP-006. |
| `vpn_users` | VPN identity, enabled/expiration, `device_limit >= 0` (default 3), access_mode `selected`/`all`. |
| `servers` | Managed node, уникальное имя, hostname, PostgreSQL INET, SSH user/port, версии и текущая ревизия. SSH secrets не хранятся. |
| `server_access` | Уникальная пара VPN user × server для selected access. `all` означает все текущие и будущие серверы, без материализации дополнительных строк. |
| `devices` | FK владельца, имя/platform, enabled, last_seen/last_server, revoked_at. Имена устройств не являются идентификаторами и могут повторяться. |
| `device_credentials` | Уникальная Device × Server, уникальный username внутри сервера, nullable secret_ciphertext, revoked_at. Ротация обновляет ту же связь. |
| `server_config_revisions` | Уникальный номер ревизии внутри сервера, JSON settings, creator, timestamps/status. Composite FK не позволяет выбрать текущую ревизию другого сервера. |
| `audit_events` | Actor/action/target/request/result, JSON metadata, timestamp. UPDATE/DELETE/TRUNCATE запрещены триггером. |

UUID и timezone-aware timestamps создаются PostgreSQL. `updated_at` обновляется
SQLAlchemy при ORM/Core UPDATE; прямые SQL writers обязаны обновлять его явно.
Status серверов/ревизий пока остаётся строкой: полный lifecycle определят профильные
задачи. Неизвестные роли/access_mode запрещены CHECK constraints.

FK по умолчанию запрещают удаление связанных родителей. ORM не выполняет скрытых
cascade deletes: retention и удаление определяются отдельным workflow. Audit actor/target
— исторические polymorphic UUID без FK; история сохраняется после удаления объекта.
Триггер не защищает от владельца схемы/superuser, способного удалить таблицу или отключить
триггер. Runtime role поэтому должна быть отдельной от migration owner.

Раздел 32 архитектуры перечисляет также jobs, notifications, invitations, sessions.
Этот baseline намеренно инкрементальный согласно узкому scope TTCP-005: их миграции
добавляются с TTCP-006/007 и Client Portal/notification workflows, вместе с контрактами
retention, authentication и доставки. Это отложенная часть database roadmap, не изменение
архитектуры. Нового ADR не требуется.

## Квота устройств

Миграция `0002_guards` проверяет `vpn_users.device_limit` в PostgreSQL.
Неотозванные устройства (`revoked_at IS NULL`) занимают слот, даже если temporarily disabled.
Отзыв освобождает слот; восстановление и перенос к другому владельцу снова проверяют квоту.
Снижение device_limit ниже текущего использования запрещено; 0 запрещает новые устройства.
Credentials/configurations на дополнительных серверах не расходуют слоты.

Перед выделением слота выполняется UPDATE строки владельца, сериализующий конкурирующие
выделения и изменения лимита. На REPEATABLE READ/SERIALIZABLE возможен SQLSTATE 40001:
будущий service layer обязан повторить всю транзакцию. Возможные deadlocks при массовых
операциях также требуют retry; порядок блокировки нескольких владельцев определит CRUD.
Ошибки quota имеют SQLSTATE 23514 и имена `ck_devices_user_quota` /
`ck_vpn_users_allocated_devices`. `metadata.create_all()` не устанавливает эти триггеры:
создавать схему нужно только миграциями.

## Секреты и транзакции

Шифрование и выпуск credentials не реализованы. `secret_ciphertext` — только контейнер
для будущего application-level encrypted payload; NULL допустим для metadata-only записи.
Нельзя записывать туда plaintext. Master key хранится вне PostgreSQL. Секретные поля
загружаются ORM только явно. JSON config_json/metadata содержат исключительно несекретные
настройки/ссылки и безопасные audit details; готовые TOML, passwords, tokens и ключи туда
записывать нельзя. Будущие writers должны валидировать это, сама БД не распознаёт секреты.

`transaction(engine)` предоставляет AsyncSession с commit при успехе, rollback при
ошибке/cancellation и обязательным закрытием. Engine принадлежит lifecycle вызывающего
компонента. URL собирается через SQLAlchemy URL из отдельных полей, без ручного encoding;
SQL параметры скрыты. Redis не участвует в persistence или migrations.

## Миграции

Python 3.12+, зависимости из `requirements-control-plane-dev.lock`; команды из корня:

```sh
python -m alembic history
python -m alembic upgrade head --sql
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Online-команды используют только `TTCP_POSTGRES_HOST/PORT/DB/USER/PASSWORD` и необязательный
`TTCP_DEPENDENCY_TIMEOUT` из защищённого окружения. Redis settings не требуются.
Offline SQL не требует credentials. `alembic.ini` не содержит DSN или паролей.
Не запускайте несколько migrators одновременно; миграции выполняются одним deployment step.
Приложения автоматически схему не создают и не изменяют, bootstrap readiness остаётся
проверкой подключения, а не версии схемы.

Порядок: `0001_core` → `0002_guards` → `0003_admin_auth` → `0004_jobs`. Upgrade выполняется транзакционно. Повторный
`upgrade head` безопасен. Новые изменения — только следующей ревизией:

```sh
python -m alembic revision --autogenerate -m "describe schema change"
```

Обязательно проверяйте результат: Alembic autogenerate не сравнивает trigger bodies
и все CHECK constraints. Для guards нужна явная миграция и regression tests.
Для одноразовой БД `python -m alembic downgrade base` удаляет **все таблицы baseline**;
не выполнять против нужных данных. В эксплуатации предпочтителен forward-fix с backup
и проверенным restore; downgrade `0002_guards` также убирает quota/audit enforcement.

Compose из `infra/compose/`:

```sh
docker compose up -d --wait postgres
docker compose run --rm --build migrate
docker compose run --rm migrate python -m alembic check
docker compose up -d --build --wait
```

`migrate` — отдельный одноразовый tools-profile service, только database network,
без Redis и без автоматического рестарта. Он получает owner `POSTGRES_*`; API/worker
могут использовать отдельные `POSTGRES_APP_USER/POSTGRES_APP_PASSWORD`.

## Ограниченная runtime role

Для совместимости одноразовый dev-stack без APP-переменных пока использует bootstrap
owner. Для реальной эксплуатации создайте отдельный login; миграции выполняет owner.
Из административной psql-сессии после upgrade:

```sql
CREATE ROLE ttcp_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
\password ttcp_app
\set app_role ttcp_app
\i infra/compose/postgres/runtime-grants.sql
```

Путь `\i` дан от корня репозитория на хосте. `\password` запрашивает пароль интерактивно;
не добавляйте пароль в SQL/history. Login не должен владеть БД/схемой и не должен состоять
в owner/superuser ролях. На старых/нестандартных БД отдельно проверьте отсутствие CREATE
для PUBLIC в схеме public. Скрипт даёт явные DML grants только core tables, audit — SELECT/INSERT;
не даёт DDL, TRUNCATE и доступ к alembic_version. Повторное применение grants безопасно.

В защищённом Compose `.env` задайте **обе** APP-переменные и пересоздайте API/worker.
Owner credentials остаются у migrator/PostgreSQL. Новые таблицы требуют осознанного
обновления grants. Эти SQL роли не являются продуктовым RBAC admin/viewer:
серверную авторизацию реализует TTCP-006.

## Проверки

```sh
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
python -m compileall -q apps migrations tests
```

Для PostgreSQL integration tests передайте `TTCP_TEST_POSTGRES=1` и `TTCP_POSTGRES_*`
к отдельной тестовой БД PostgreSQL 17, затем `python -m pytest -q`. Без opt-in интеграционные
тесты явно skipped. Fixture создаёт только случайные `ttcp_test_*` schemas и удаляет их
в finally; существующая public schema не изменяется. Для проверки grants тестовая роль
должна иметь CREATEROLE (или быть superuser). Не указывайте production DB.

Покрытие: empty upgrade, повторный upgrade, downgrade/reupgrade, ORM drift/defaults/relations,
uniqueness/FK/CHECK, revision ownership, quotas 0/1/3/5, multi-server credentials, revoke/restore,
изменение лимита/владельца, concurrent allocation, transaction rollback/cancellation,
audit retention/immutability и отрицательные проверки runtime permissions.
`bash infra/compose/tests/runtime_smoke.sh` дополнительно проверяет миграции и полный
runtime TTCP-004 в отдельном одноразовом Docker-проекте.
