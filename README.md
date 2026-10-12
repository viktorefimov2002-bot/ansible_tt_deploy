# TrustTunnel

Репозиторий содержит Control Plane MVP и сохранённый Ansible/CLI для установки
и обслуживания TrustTunnel Endpoint. Реальный запуск на VPS требует живой приёмки.

## Getting started — с чего начать

1. [Установка и первый запуск](docs/guides/deployment-guide.md): Windows/Docker Desktop/WSL2,
   отдельный Ubuntu VPS, секреты, миграции, TOTP, HTTPS, bootstrap и первый deploy.
2. [Руководство администратора](docs/guides/admin-web-guide.md): существующие экраны,
   права, серверы, пользователи, задачи, мониторинг и восстановление.
3. [Руководство пользователя VPN](docs/guides/client-web-guide.md): приглашение,
   устройство, QR/deeplink/TOML и подключение.
4. [Чек-лист первого развёртывания](docs/guides/first-deployment-checklist.md):
   ручные PASS/FAIL и безопасные доказательства. [Протокол проверки документации](docs/guides/documentation-validation.md)
   отделяет статические проверки от неподтверждённого live deployment.

Локальный HTTP не гарантирует работу Secure cookies; localhost-ссылки недоступны
с телефона. Подготовка доверенного HTTPS и остальные DOC/DEPLOY gaps описаны в руководстве.

## Техническая документация и сохранённый CLI

Автоматизация находится в [automation/ansible](automation/ansible/README.md).
Локальная инфраструктура Control Plane: [Docker Compose runtime](infra/compose/README.md)
(TTCP-003/004). [API и worker](apps/README.md) включают PostgreSQL persistence,
административную аутентификацию и [durable jobs с SSE logs](docs/jobs.md).
[Ansible execution adapter](docs/execution.md) добавляет безопасную границу выполнения
и интеграцию с jobs. [Управление серверами](docs/servers.md) включает CRUD,
зашифрованные SSH identities и durable pre-flight/status jobs (TTCP-009).
Отдельный [managed-node bootstrap](docs/node-bootstrap.md) подготавливает VPS
для управления без сохранения provider/root credentials (TTCP-010).

[Client Portal](docs/client-portal.md) предоставляет клиентский путь от приглашения
до конфигурации TrustTunnel (TTCP-014).

[Server configuration revisions](docs/server-configuration.md) provide validated,
immutable settings and durable apply with automatic rollback (TTCP-018).
[Admin Web](docs/admin-web.md) предоставляет управление серверами, VPN users,
приглашениями и jobs на отдельном origin (TTCP-015).
[Мониторинг, аудит и уведомления](docs/admin-visibility.md) завершают операционную
видимость Admin Web (TTCP-016).
[Durable operational alerts](docs/operational-alerts.md) turn trusted node/service
and sustained disk/memory observations into PostgreSQL-backed incidents (TTCP-019).
[Remote backups](docs/backups.md) add encrypted off-host storage and Admin Web history
(TTCP-020), with [backup/restore](docs/runbooks/backup-restore.md),
[deployment](docs/runbooks/control-plane-deployment.md),
[upgrade](docs/runbooks/upgrade-migrations.md) and
[incident recovery](docs/runbooks/incident-recovery.md) runbooks.

[Release validation](docs/release-readiness.md) adds mandatory Linux CI, disposable
integration/backup recovery tests and separate-origin browser E2E (TTCP-021).
Live release sign-off uses the explicit [staging acceptance checklist](docs/runbooks/staging-live-acceptance.md).

Документация: [продукт](docs/product/product-spec-v0.1.md),
[архитектура](docs/architecture/README.md), [ADR](docs/adr/README.md),
[план MVP](docs/planning/mvp.md).

Следующие команды относятся к самостоятельному историческому Ansible/CLI workflow,
а не к регистрации managed node через Admin Web. Выполняются из корня репозитория:

```bash
./scripts/install_requirements.sh --check-only
cp automation/ansible/deploy.example.yml deploy.yml
# Отредактируйте deploy.yml для своего сервера.
./ttctl init --config deploy.yml
./ttctl deploy
./ttctl status
```

Для SSH-пароля используйте `./ttctl init --config deploy.yml --ask-ssh-pass`.
Полная инструкция: [Ansible/CLI workflow](automation/ansible/README.md).

Корневые команды, плейбуки и ранее сгенерированные deploy/uninstall helpers
сохранены через обёртки. Перед обновлением рабочего checkout прочитайте
[изменения путей и перенос локальных файлов](automation/ansible/README.md#размещение-после-ttcp-002).

Проверка переноса без подключения к серверам:

```bash
bash automation/ansible/tests/layout_smoke.sh
```
