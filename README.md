# TrustTunnel

Текущая реализация — Ansible/CLI для установки и обслуживания TrustTunnel Endpoint.
Автоматизация находится в [automation/ansible](automation/ansible/README.md).
Локальная инфраструктура Control Plane: [Docker Compose runtime](infra/compose/README.md)
(TTCP-003/004). [API и worker](apps/README.md) включают PostgreSQL persistence,
административную аутентификацию и [durable jobs с SSE logs](docs/jobs.md).
Ansible execution adapter и управление серверами через API ещё не реализованы.

Документация: [продукт](docs/product/product-spec-v0.1.md),
[архитектура](docs/architecture/README.md), [ADR](docs/adr/README.md),
[план MVP](docs/planning/mvp.md).

Команды выполняются из корня репозитория:

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
