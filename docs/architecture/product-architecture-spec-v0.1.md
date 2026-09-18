# TrustTunnel Control Plane

## Product & Architecture Specification v0.1

**Status:** Architecture baseline / approved for initial implementation
**Purpose:** исходная продуктовая и архитектурная спецификация для реализации через Codex.

---

# 1. Цель продукта

Создать self-hosted систему управления инфраструктурой TrustTunnel, состоящую из:

* административного control plane;
* нескольких TrustTunnel data-plane серверов;
* клиентского портала для выдачи VPN-конфигураций;
* системы управления пользователями и устройствами;
* мониторинга;
* аудита;
* фоновых операций;
* механизма автоматизации server lifecycle.

Первоначально системой управляет один владелец, однако архитектура должна позволять дальнейшее расширение.

Начальная инфраструктура:

* 2 TrustTunnel data-plane node;
* каждая: `1 vCPU / 2 GB RAM`;
* 1 отдельная control-plane node;
* минимально допустимая control-node: `1 vCPU / 2 GB RAM`;
* предпочтительно `2 vCPU / 4 GB RAM`, если стоимость отличается незначительно.

Предпочтительное размещение control plane:

* за пределами РФ;
* желательно у другого VPS-провайдера относительно data-plane серверов;
* с хорошей сетевой связностью до всех VPN nodes.

Control plane не должен находиться в datapath уже настроенного VPN-клиента.

Если control plane недоступен, ранее выданные и действующие TrustTunnel-конфигурации должны продолжать работать.

---

# 2. Основные архитектурные принципы

## 2.1 PostgreSQL — source of truth

Ни Ansible, ни Redis, ни файлы TOML не являются главным состоянием системы.

Главный источник истины:

```text
PostgreSQL
```

В БД хранятся:

* серверы;
* пользователи;
* устройства;
* права доступа;
* credentials metadata;
* config revisions;
* jobs;
* audit;
* notifications;
* authentication state.

---

## 2.2 Redis — infrastructure component, а не source of truth

Для MVP используется Redis.

Назначение:

```text
background jobs
short-lived coordination
job notifications
caching
distributed locks where required
```

Критические операции сначала фиксируются в PostgreSQL.

Приложение не должно содержать Redis-specific business logic.

Внутри backend вводятся abstractions:

```text
JobQueue
EventPublisher
LockProvider
```

Текущая реализация:

```text
Redis
```

Будущая реализация при необходимости может использовать:

```text
Kafka
```

без изменения domain/service layer.

Kafka не используется в MVP из-за текущего масштаба системы и ограниченных ресурсов control-node.

---

# 3. Общая архитектура

```text
                         Internet
                            │
                      ┌─────▼─────┐
                      │   NGINX   │
                      │ TLS/Proxy │
                      └─────┬─────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        Admin Web                  Client Portal
        static SPA                  static SPA
              │                           │
              └─────────────┬─────────────┘
                            ▼
                      FastAPI API
                            │
           ┌────────────────┼────────────────┐
           │                │                │
           ▼                ▼                ▼
      PostgreSQL          Redis           Worker
                                              │
                                      Execution Layer
                                              │
                                   ┌──────────┴─────────┐
                                   ▼                    ▼
                                Ansible            future agent
                                   │
                 ┌─────────────────┴─────────────────┐
                 ▼                                   ▼
         Frankfurt-01                           Server-02
         TrustTunnel                            TrustTunnel
         node_exporter                          node_exporter
                 │                                   │
                 └─────────────────┬─────────────────┘
                                   ▼
                            VictoriaMetrics
                                   │
                                   ▼
                                Grafana
```

VPN traffic path:

```text
VPN Client
    │
    └──────────────► TrustTunnel data-plane node
```

Control plane не участвует в передаче VPN-трафика пользователя.

---

# 4. Technology stack

## Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
```

## Database

```text
PostgreSQL
self-hosted
```

## Queue

```text
Redis
```

Конкретная worker library выбирается отдельным ADR во время bootstrap проекта.

Требование — domain layer не должен зависеть от конкретного broker API.

## Frontend

Для MVP:

```text
React
TypeScript
Vite
shadcn/ui
```

Admin и Client Portal собираются в статические bundles.

Это позволяет не держать постоянно работающий Node.js runtime на небольшой control-node.

## Reverse proxy

```text
NGINX
```

Назначение:

* TLS termination;
* HTTP → HTTPS redirect;
* reverse proxy для FastAPI;
* раздача статических Admin Web и Client Portal;
* security headers;
* rate limiting для чувствительных endpoints;
* поддержка SSE/WebSocket при необходимости.

Причины выбора:

* существующий опыт эксплуатации NGINX;
* зрелый и предсказуемый инструмент;
* низкий resource overhead;
* простое troubleshooting;
* отсутствие влияния на будущую Kubernetes-архитектуру.

## TLS

Для MVP:

```text
NGINX
+
ACME client / Certbot
```

Сертификаты автоматически обновляются.

## Observability

```text
VictoriaMetrics
node_exporter
Grafana
```

Отдельный Prometheus не используется.

Для scrape Prometheus-compatible exporters используется встроенный scraping VictoriaMetrics либо отдельный lightweight collector при необходимости.

## Deployment

Сейчас:

```text
Docker Compose
```

Будущее:

```text
Kubernetes
Helm
```

Приложения должны проектироваться Kubernetes-ready с первого дня, но Kubernetes не используется в MVP.

---

# 5. Текущий Ansible проект

Существующий проект сохраняется.

Исходная ветка:

```text
p1-usability-safe-cli
```

Она уже предоставляет:

```text
init
deploy
status
add-client
remove-client
import-credentials
uninstall
```

Текущий deploy config содержит:

* server connection;
* TrustTunnel domain/public address;
* TLS;
* firewall;
* clients;
* DNS/rules;
* monitoring-related settings.

Существующий код не выбрасывается.

Его роль меняется:

```text
Before:
Ansible = application

After:
Ansible = execution adapter
```

---

# 6. Execution architecture

Domain/service layer вызывает операции:

```text
DeployServer
UpdateServer
RestartServer
ApplyServerConfig
CreateCredential
RevokeCredential
UninstallServer
```

Domain layer не должен знать, каким механизмом фактически выполняется операция:

```text
Ansible
SSH
Node Agent
API
```

Для MVP:

```text
ExecutionPort
     │
     ▼
AnsibleExecutionAdapter
```

Позже:

```text
ExecutionPort
     │
     ├── AnsibleExecutionAdapter
     └── NodeAgentExecutionAdapter
```

Ansible остаётся предпочтительным механизмом для:

* bootstrap;
* installation;
* update;
* system configuration;
* firewall;
* TLS;
* repair/reconciliation.

Частые runtime-операции позднее могут постепенно переехать в node-agent.

---

# 7. Server onboarding

VPS создаётся вручную у provider.

Первоначальный onboarding:

```text
Provider
   │
   ▼
Create VPS
   │
   ▼
root password / SSH key
   │
   ▼
run bootstrap script once
   │
   ▼
create dedicated management user
install control-plane SSH public key
configure sudo permissions
install dependencies/exporters
perform basic hardening
   │
   ▼
Add server in Control Plane
```

Предпочтительный вариант bootstrap — не хранить root password в control plane.

Admin получает bootstrap command либо запускает скрипт из репозитория вручную.

Пример:

```text
curl .../bootstrap.sh | sudo sh ...
```

Bootstrap должен:

* проверить поддерживаемую ОС;
* проверить architecture;
* создать отдельного service user;
* установить control-plane SSH public key;
* выдать минимально необходимые sudo permissions;
* установить необходимые системные зависимости;
* установить `node_exporter`;
* подготовить узел к Ansible management;
* провести базовый hardening;
* убрать необходимость постоянного root/password доступа со стороны control plane.

Поддерживаются два onboarding-варианта:

```text
initial password
SSH key
```

Долгосрочное управление должно предпочитать SSH key.

---

# 8. Pre-flight checks

Перед deploy server должен пройти:

```text
SSH connectivity
DNS A/AAAA resolution
TCP port availability
UDP port availability
80/tcp when ACME HTTP challenge is required
443/tcp
443/udp
supported OS
supported CPU architecture
disk space
memory
time synchronization
```

Результаты отображаются в Admin UI до запуска deployment.

---

# 9. Server model

Пример:

```text
Server

id
name
hostname
public_ip
domain
location
status
enabled

ssh_user
ssh_port

trusttunnel_version
desired_trusttunnel_version

config_revision_id

created_at
updated_at
last_seen_at
```

Server names:

```text
Frankfurt-01
Finland-01
Frankfurt-02
```

В MVP пользователь выбирает конкретный server.

Автоматическая смена endpoint между одинаковыми географическими локациями не используется.

Это сохраняет для пользователя более предсказуемый внешний IP.

Future:

```text
ServerGroup
Germany
Finland
Auto
```

---

# 10. User model

VPN user отделён от admin account.

```text
VpnUser

id
display_name

enabled
expires_at

device_limit = 3

access_mode:
    selected
    all

created_at
updated_at
```

Поддерживаются:

```text
selected servers
```

и:

```text
all current and future servers
```

---

# 11. Device model

Пользователь самостоятельно создаёт устройства до установленного лимита.

```text
Device

id
user_id
name
platform

enabled

last_seen_at
last_server_id

created_at
revoked_at
```

Пример:

```text
Ivan

iPhone
Windows PC
MacBook
```

Default limit:

```text
3 devices
```

---

# 12. Credentials model

Credentials уникальны для:

```text
Device × Server
```

Пример:

```text
Ivan / iPhone / Frankfurt-01
Ivan / iPhone / Finland-01

Ivan / Windows / Frankfurt-01
Ivan / Windows / Finland-01
```

При revoke одного устройства удаляются только credentials данного device.

Другие устройства пользователя продолжают работать.

---

# 13. Expiration

При:

```text
expires_at <= now
```

background job должен:

```text
disable VPN user

revoke all active credentials

remove credentials from all affected servers

invalidate downloadable configs

invalidate client sessions where appropriate

create audit event

create notification
```

---

# 14. Disable vs Delete

## Disable

Обратимая операция.

```text
user.disabled = true
credentials revoked
metadata retained
audit retained
```

## Delete

Destructive operation.

Удаление или anonymize/archive связанных данных определяется отдельной retention policy.

---

# 15. Client authentication

Не используются:

```text
Telegram login
email/password
```

MVP использует invitation / magic-link model.

Flow:

```text
Admin creates user
      │
      ▼
Generate invitation
      │
      ▼
one-time random token
      │
      ▼
user opens client portal
      │
      ▼
token exchanged for authenticated session
```

В БД invitation token хранится только как hash.

Token имеет:

```text
expires_at
used_at
revoked_at
```

---

# 16. Client UX

Главный принцип:

> VPN должен устанавливаться человеком, который ничего не знает о VPN.

Mobile flow:

```text
Open invite
   ↓
Choose server
   ↓
Add this device
   ↓
Install TrustTunnel app if needed
   ↓
Open configuration using tt://
```

Advanced section:

```text
Download TOML
Show QR
Copy configuration
Choose another server
Technical details
```

Control plane availability не должна быть необходима для продолжения работы уже установленного VPN-профиля.

---

# 17. Configuration revisions

Server configuration имеет версии.

```text
ServerConfigRevision

id
server_id
revision
config_json
created_by
created_at
applied_at
status
```

Apply flow:

```text
validate
   ↓
render candidate
   ↓
backup previous
   ↓
apply
   ↓
restart/reload
   ↓
healthcheck
   ↓
success
```

При ошибке:

```text
rollback
```

---

# 18. Basic + Advanced configuration

Basic UI управляет структурированными настройками.

Advanced UI предоставляет расширенные TrustTunnel options.

Raw configuration не должна напрямую заменять production file без validation.

---

# 19. Jobs

Все долгие операции представлены сущностью Job.

```text
Job

id
type
target_type
target_id

status:
    queued
    running
    succeeded
    failed
    cancelled

progress
created_by

created_at
started_at
finished_at

error_code
error_message
```

Например:

```text
server.deploy
server.update
server.uninstall
server.restart

credential.create
credential.revoke

user.expire

backup.run
```

---

# 20. Job logs

UI должен показывать полноценный live log.

Backend хранит job metadata в PostgreSQL.

Log stream может использовать:

```text
worker
   ↓
Redis
   ↓
API
   ↓
SSE
   ↓
Admin UI
```

SSE является предпочтительным вариантом для однонаправленного live job log.

WebSocket используется только если появится реальная необходимость в двустороннем realtime protocol.

Финальный bounded log сохраняется для troubleshooting.

---

# 21. Admin authentication

MVP:

```text
username/password
+
TOTP
```

Roles:

```text
admin
viewer
```

`admin`:

```text
read
write
deploy
restart
configure
manage users
manage devices
manage credentials
```

`viewer`:

```text
read
monitoring
logs
audit
```

---

# 22. Audit log

Audit обязателен с первой версии.

```text
AuditEvent

id
actor_id
actor_type

action

target_type
target_id

request_id

result

metadata

created_at
```

Примеры:

```text
server.deploy
server.update
server.restart

user.create
user.disable

device.create
device.revoke

credential.create
credential.revoke

config.apply
```

Audit events immutable через обычные application flows.

---

# 23. Secrets

HashiCorp Vault не используется в MVP.

Sensitive values шифруются application-level encryption.

Master key находится:

```text
outside PostgreSQL
```

MVP:

```text
Docker secret / protected environment
```

Future:

```text
Kubernetes Secret
External Secrets
Vault/KMS
```

Шифровать необходимо:

```text
SSH private material
VPN credentials
sensitive config fields
recovery secrets
TOTP seeds
```

Root provider password не должен превращаться в постоянный control-plane secret.

---

# 24. Monitoring

Data-plane nodes:

```text
node_exporter
```

Control-plane monitoring stack:

```text
VictoriaMetrics
Grafana
```

Metrics:

```text
CPU
memory
load
disk
filesystem
uptime

network RX
network TX
packets
errors
drops
interface utilization

TrustTunnel service state
process state
version
restart count

future:
active sessions
authentication events
TT-specific metrics
```

На небольшой control-node:

```text
limited retention
container memory limits
minimal Grafana setup
no unnecessary observability services
```

---

# 25. Notifications

Notification entity хранится в PostgreSQL.

MVP transport:

```text
Admin UI
```

Future:

```text
Telegram
```

Event types:

```text
NODE_OFFLINE
SERVICE_DOWN
DISK_HIGH
MEMORY_HIGH
NETWORK_SATURATION
CERTIFICATE_EXPIRING
UPDATE_AVAILABLE
DEPLOY_FAILED
BACKUP_FAILED
USER_EXPIRED
```

---

# 26. Backup strategy

Backup должен находиться не на том же VPS.

MVP:

```text
PostgreSQL logical backup
   ↓
compression
   ↓
encryption
   ↓
S3-compatible remote storage
```

Admin UI:

```text
last backup
status
age
history
run backup now
```

Restore не выполняется из обычного Web UI.

Restore:

```text
CLI / maintenance runbook
```

Позже:

```text
pgBackRest
WAL archive
PITR
restore verification
```

---

# 27. Control-plane deployment principles

Предпочтительная topology:

```text
Provider A
├── Data Plane Frankfurt-01
└── Data Plane Server-02


Provider B
└── Control Plane
```

Это не является обязательным требованием MVP, но считается предпочтительным deployment pattern.

Control plane предпочтительно размещается за пределами РФ.

Причины:

* стабильная управляющая связность с зарубежными data-plane nodes;
* уменьшение зависимости management traffic от международной связности РФ;
* разделение failure domains;
* сохранение возможности управления VPN nodes при проблемах основного provider.

Client Portal и Admin Portal логически разделяются:

```text
admin.example.com
vpn.example.com
```

даже если физически обслуживаются одним NGINX.

---

# 28. Repository layout

Целевой monorepo:

```text
trusttunnel-control/
│
├── AGENTS.md
├── README.md
│
├── apps/
│   ├── api/
│   ├── worker/
│   ├── admin-web/
│   └── client-web/
│
├── automation/
│   └── ansible/
│
├── infra/
│   ├── compose/
│   ├── nginx/
│   ├── monitoring/
│   │   ├── victoriametrics/
│   │   └── grafana/
│   └── kubernetes/
│
├── migrations/
│
├── scripts/
│   └── bootstrap-node.sh
│
└── docs/
    ├── product/
    ├── architecture/
    ├── adr/
    ├── api/
    ├── runbooks/
    └── exec-plans/
```

`infra/kubernetes/` первоначально может содержать только placeholder/readme.

---

# 29. Architecture Decision Records

Минимальный набор:

```text
0001-monorepo.md
0002-postgresql-source-of-truth.md
0003-redis-for-mvp.md
0004-ansible-execution-adapter.md
0005-victoriametrics.md
0006-device-level-credentials.md
0007-docker-compose-first.md
0008-kubernetes-ready-architecture.md
0009-client-magic-link-auth.md
0010-application-secret-encryption.md
0011-nginx-reverse-proxy.md
```

ADR меняются только отдельным осознанным решением.

---

# 30. Kubernetes readiness

Kubernetes не используется в MVP.

Запрещены архитектурные решения, усложняющие миграцию:

```text
local application state
hardcoded localhost dependencies
in-memory durable queues
manual mutable container state
backend-owned ad-hoc cron loops
host-specific paths in business logic
```

Используются:

```text
environment configuration
external PostgreSQL abstraction
external queue abstraction
container healthchecks
graceful shutdown
stateless API
stateless workers
```

Scheduled business operations должны иметь переносимую abstraction и не зависеть от конкретного host cron implementation.

---

# 31. MVP non-goals

Не входят в первую версию:

```text
multi-tenant commercial SaaS
provider API provisioning
automatic geographic load balancing
Kafka cluster
Kubernetes deployment
Vault
billing
payments
traffic accounting/billing
automatic VPN client subscription URL
multi-region HA control-plane
```

Архитектура не должна препятствовать их будущему появлению.

---

# 32. Implementation roadmap

## Phase 0 — Repository foundation

Создать integration branch:

```text
control-plane-v0
```

и подготовить:

```text
AGENTS.md
docs/product/
docs/architecture/
docs/adr/
docs/exec-plans/
docs/runbooks/
```

Existing Ansible переместить в:

```text
automation/ansible/
```

без функциональных изменений.

---

## Phase 1 — Development platform

Поднять Docker Compose:

```text
postgres
redis
api
worker
nginx
victoriametrics
grafana
```

Admin/client frontend добавляются как static bundles по мере появления.

---

## Phase 2 — Database core

Реализовать schema:

```text
admins
vpn_users
devices
servers
server_access
device_credentials
server_config_revisions
jobs
audit_events
notifications
invitations
sessions
```

Добавить Alembic migrations.

---

## Phase 3 — Admin security

Реализовать:

```text
admin login
password hashing
TOTP
RBAC admin/viewer
session lifecycle
audit middleware
```

---

## Phase 4 — Server management foundation

Реализовать:

```text
server CRUD
pre-flight checks
SSH connectivity
bootstrap workflow
server status
```

---

## Phase 5 — Job infrastructure

Реализовать:

```text
PostgreSQL job state
Redis queue
worker
retries
idempotency
live logs over SSE
job cancellation where safe
```

---

## Phase 6 — Ansible adapter

Подключить существующую automation:

```text
deploy
status
restart
update
uninstall
credentials
```

Не переписывать сразу рабочие Ansible roles.

---

## Phase 7 — Observability

Добавить:

```text
node_exporter deployment
VictoriaMetrics
Grafana
server health aggregation
network metrics
notifications
```

---

## Phase 8 — VPN identity

Реализовать:

```text
VPN users
server access
device limit
device self-service
credential lifecycle
expiration
disable/re-enable
```

---

## Phase 9 — Client Portal

Реализовать:

```text
invite links
magic login
device management
server selection
TT deep link
QR
config download
```

---

## Phase 10 — Admin UI

Реализовать:

```text
dashboard
servers
users
devices
jobs
monitoring
notifications
audit
configuration
```

---

## Phase 11 — Operations

Добавить:

```text
remote backups
restore runbook
deployment runbook
upgrade runbook
incident/troubleshooting docs
CI
integration tests
E2E
```

---

# 33. First Codex tasks

```text
TTCP-001
Create architecture/documentation skeleton and AGENTS.md

TTCP-002
Reorganize existing Ansible project into monorepo layout

TTCP-003
Create Docker Compose development/control stack

TTCP-004
Bootstrap FastAPI application and worker

TTCP-005
Create PostgreSQL schema and Alembic baseline

TTCP-006
Implement admin authentication and RBAC

TTCP-007
Implement durable jobs + Redis worker + live logs

TTCP-008
Create Ansible execution adapter

TTCP-009
Implement server CRUD + pre-flight validation

TTCP-010
Implement bootstrap-node onboarding

TTCP-011
Integrate VictoriaMetrics/node_exporter/Grafana

TTCP-012
Implement VPN users/devices/access/credentials
```

После этого:

```text
client portal
admin frontend
notification UX
backups
E2E
```

---

# 34. Definition of Done for architecture phase

Architecture v0.1 считается готовой для начала реализации, когда в репозитории присутствуют:

```text
AGENTS.md

docs/product/product-spec-v0.1.md

docs/architecture/system-architecture-v0.1.md

docs/adr/0001...
docs/adr/...

docs/exec-plans/mvp.md
```

и создана integration branch:

```text
control-plane-v0
```

После этого Codex может начинать TTCP-002 и последующие implementation tasks.
