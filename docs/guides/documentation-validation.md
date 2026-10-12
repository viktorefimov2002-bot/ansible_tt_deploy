# Проверка документации TTCP-022A

Документы сверены с кодом checkout: Compose/NGINX, bootstrap/lifecycle helpers, API schemas/services, React components/state и runbooks. Архитектурная спецификация не менялась. Задание TTCP-022A — запрос на три русских operator guides; отдельного task file в репозитории нет.

## Охват безопасной проверки

Проверяются локальные Markdown links/anchors и fences, Bash/PowerShell syntax команд руководства и генерация **одноразового синтетического env**. Значения не выводятся, пишутся только в временный каталог `.tools`, не подключаются к сервисам. Проверяется длина/различие паролей, URL-safe base64 32-byte master key, создание файла без перезаписи. Compose-проверка использует отдельный существующий CI validator и synthetic env/TLS/backup paths; не поднимает контейнеры.

Команды из корня checkout, Windows environment текущей проверки:

```powershell
.venv/Scripts/python.exe scripts/ci/documentation_validation.py --bash 'C:/Program Files/Git/bin/bash.exe' --powershell 'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe' --compose-executable .tools/docker-compose.exe
.venv/Scripts/python.exe scripts/ci/compose_validation.py --compose-executable .tools/docker-compose.exe
.venv/Scripts/python.exe -m ruff check scripts/ci/documentation_validation.py
.venv/Scripts/python.exe -m ruff format --check scripts/ci/documentation_validation.py
git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check
```

Путь `.tools/docker-compose.exe` относится к доступному локальному QA executable, а не к обязательному installer для оператора. В среде с Docker CLI используйте `python scripts/ci/compose_validation.py`; для docs validator укажите доступные Bash/PowerShell executable, без них соответствующие syntax checks явно SKIP.

## Результаты на 12 октября 2026 года

| Проверка | Результат |
| --- | --- |
| Локальные ссылки, anchors, fences изменённой документации | PASS: 205 локальных ссылок/anchors в 18 документах. |
| Bash и PowerShell command syntax | PASS: 13 Bash blocks через `bash -n`; 7 PowerShell blocks через Language.Parser, без выполнения deployment команд. |
| Compose CLI commands/long flags в блоках `dc` | PASS: 7 групп команд через help подкоманд; сами startup/exec команды не выполнялись. |
| Secret generation snippets на disposable данных | PASS: оба env writers; разные 64-символьные hex passwords, корректный 32-byte Fernet key, ACL writer; повтор отказал без перезаписи файла. |
| Base, production, backup, production+backup, production+backup+restore Compose | PASS: все пять вариантов parsed/validated; Compose v2.39.4. SQL/Redis/API private, TLS/backup mounts и origins checked. |
| Docs validator: Ruff lint/format | PASS: `ruff check` и `ruff format --check` для добавленного скрипта. |
| Рабочее дерево: whitespace/diff review | PASS: `diff --check`; docs и новый validator просмотрены; product implementation/architecture baseline не менялись. |

Внешние prerequisites сверены по официальным [Docker Windows requirements](https://docs.docker.com/desktop/setup/install/windows-install/), [Microsoft WSL install](https://learn.microsoft.com/en-us/windows/wsl/install) и [TrustTunnel clients](https://github.com/TrustTunnel/TrustTunnel#clients). Веб-ссылки на эти страницы прочитаны; массовая доступность всех внешних ссылок старой технической документации не проверялась.

## Что не подтверждено этим протоколом

Docker CLI/engine в текущем окружении проверки недоступен; WSL status возвращает отказ доступа. Standalone Compose подтверждает только config, не сборку/запуск. Поэтому `up/pull/build`, container health, actual Alembic migration/admin enrollment, runtime role provisioning, browser login на workstation, TLS certificate issue/renewal, management SSH из worker, Ubuntu bootstrap/idempotency, Data Plane deploy/update/restart/uninstall, ACME, native QR/deeplink/TOML import, трафик и remote S3/restore **не исполнялись**. Syntax parsing не доказывает внешнюю достижимость или успешное исполнение команды.

Для этих шагов оставлен [human acceptance checklist](first-deployment-checklist.md) с пустыми PASS/FAIL evidence fields. Все конкретные blockers/ограничения перечислены как [DOC/DEPLOY gaps](deployment-guide.md). Продуктовые Python/TS/Compose файлы не менялись; повтор всего продуктового test suite не является доказательством first deployment и не требуется для документационной правки. Существующие исторические validation records сохранены, не объявлены результатами TTCP-022A.

## Исправленные противоречия

- README теперь ведёт в русские guides; самостоятельный CLI помечен отдельно от managed-node workflow.
- Compose reference отражает bundles, management egress, backups и нынешние server/VPN/bootstrap/lifecycle capabilities вместо «ещё не реализованы».
- Readiness API помечена внутренней: `/api/readyz` через web edge не доступна. Используется healthcheck внутри контейнера.
- Исторические TTCP-001/006 notes явно привязаны к тому этапу; не утверждают отсутствие последующих компонентов.
- Architecture index отличает rollback неуспешного config apply от общего lifecycle rollback; ADR registry содержит 0021 без изменения его Proposed status.
- ADR-0017 сохраняет исходное bearer-only решение Admin на TTCP-014 с ссылкой на последующее cookie расширение ADR-0018.
- Alerts reference уточняет, что BACKUP_FAILED был добавлен последующим TTCP-020; технические notes TTCP-019 сохранены.

Связанные runbooks: [deployment](../runbooks/control-plane-deployment.md), [upgrades](../runbooks/upgrade-migrations.md), [incidents](../runbooks/incident-recovery.md), [backup/restore](../runbooks/backup-restore.md), [полная release acceptance](../runbooks/staging-live-acceptance.md).
