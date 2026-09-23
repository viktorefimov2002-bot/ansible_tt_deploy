# Архитектура TrustTunnel Control Plane

## Источники и навигация

- [Product & Architecture Specification v0.1](product-architecture-spec-v0.1.md) — основной источник архитектурных и продуктовых требований.
- [Product baseline](../product/product-spec-v0.1.md) — краткое описание аудитории, сценариев и границ MVP.
- [ADR: правила и реестр](../adr/README.md) — оформление архитектурных решений; [шаблон](../adr/template.md).
- [План MVP](../planning/mvp.md) — этапы реализации и границы TTCP-001.
- [AGENTS.md](../../AGENTS.md) и [навыки проекта](../../.agents/skills/README.md) — правила работы с репозиторием.

## Текущая реализация

TTCP-003 добавляет [локальный Compose runtime](../../infra/compose/README.md):
PostgreSQL, Redis, VictoriaMetrics, минимальную Grafana, NGINX и заглушки API/worker.
Это инфраструктурная основа; приложения и интеграции следующих задач ещё не реализованы.

На этапе TTCP-001 репозиторий содержит Ansible roles/playbooks и CLI `ttctl`: установку, настройку TLS/firewall, управление файловыми credentials, клиентскими конфигурациями и локальную диагностику. Эксплуатационные инструкции находятся в [Ansible README](../../automation/ansible/README.md), [P1 workflow](../../automation/ansible/docs/P1_USABILITY.md) и [описании мониторинга](../../automation/ansible/docs/MONITORING.md).

Существующий `bootstrap.sh` относится к CLI/Ansible workflow. Отдельный [managed-node bootstrap](../node-bootstrap.md) реализован в TTCP-010 в `scripts/bootstrap-node.sh` для подготовки управляемого узла. Полный автоматический rollback Data Plane пока не реализован; [существующее руководство rollback](../ROLLBACK.md) не подтверждает наличие этой функции.

## Целевая архитектура MVP

FastAPI API и worker обслуживают управление; PostgreSQL хранит долговременное авторитетное состояние, Redis обеспечивает асинхронное взаимодействие и координацию через `JobQueue`, `EventPublisher`, `LockProvider`. Ansible должен стать execution adapter за `ExecutionPort` с сохранением существующей автоматизации.

NGINX обслуживает статические Admin Web и Client Portal и проксирует API. Мониторинг строится на node_exporter, VictoriaMetrics и Grafana. Для Control Plane выбран Docker Compose; Kubernetes остаётся вне MVP. Эти компоненты описывают целевое состояние и на этапе TTCP-001 ещё не присутствуют как реализованный стек.

Control Plane не входит в путь VPN-трафика. Управление и proxy/runtime обязанности остаются разделёнными; детали границ и потоков приведены в разделах 2–6 основной спецификации.

## Будущая документация компонентов

Отдельные документы создаются в рамках соответствующих задач и добавляются в этот индекс после появления файлов. Пока точками входа служат разделы [основной спецификации](product-architecture-spec-v0.1.md):

| Будущий документ | Исходные разделы спецификации |
| --- | --- |
| API, доменная модель и PostgreSQL | 2, 4, 9–14, 17, 19, 21–23 |
| Worker, jobs и Redis adapters | 2.2, 19–20, 30 |
| Ansible adapter и managed-node onboarding | 5–8, 17 |
| Admin Web и Client Portal | 15–16, 21, 25, 27 |
| Observability, deployment и эксплуатация | 24–28, 30 |

Нерешённые вопросы перечислены в [правилах ADR](../adr/README.md). Индекс не выбирает решения вместо профильных задач.
