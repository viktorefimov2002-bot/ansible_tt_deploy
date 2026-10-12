# Руководство администратора TrustTunnel

Для системного администратора текущего MVP. Подготовка станции и VPS: [установка](deployment-guide.md). Инструкция пользователю: [личный кабинет](client-web-guide.md). Первый запуск проверяется по [чек-листу](first-deployment-checklist.md).

Admin Web сейчас использует английские подписи. Ниже они приведены точно, с русским объяснением. Перевод интерфейса, регистрация новых администраторов и reset TOTP в меню отсутствуют. Описаны существующие действия; SSH/ACME/реальное подключение VPN требуют [живой приёмки](../runbooks/staging-live-acceptance.md).

## Вход, навигация и роли

Откройте адрес **Admin**, выданный оператором, на доверенном HTTPS. В локальной разработке это `http://admin.localhost:8080/`, но HTTP Secure cookies работают не во всех браузерах. Если вход пропадает после обновления страницы, обращайтесь к оператору за HTTPS, не отключайте защиту cookies.

В **Sign in** заполните `Username` (административный login), `Password` и `Authenticator code` (6 цифр TOTP). Нажмите **Sign in**. После initial enrollment дождитесь следующего OTP. Неверные данные, срок сессии или clock drift могут помешать входу. Повторяющиеся попытки ограничиваются: при `Too many attempts` подождите. Recovery/reset через этот экран не выполняется.

Слева находятся девять разделов; сверху каждого — **Refresh** для обновления данных. **Sign out** завершает административную сессию. Периоды/даты интерфейс показывает в часовом поясе браузера. Сообщение об отправке операции не равно её завершению: дождитесь результата в Jobs.

| Раздел | Назначение |
| --- | --- |
| Dashboard | Сводка серверов, пользователей и последних задач. |
| Servers | Регистрация подготовленных VPS, диагностика, workload и ревизии. |
| VPN users | Пользователи VPN, сроки, доступ, устройства, credentials. |
| Invitations | Одноразовые входные ссылки в Client Web. |
| Jobs | Фоновые операции, результат, попытки и безопасные логи. |
| Backups | История и ручной запуск настроенных remote backups. |
| Monitoring | Здоровье Control Plane, метрики и состояние VPN service. |
| Audit | История административных и security actions. |
| Notifications | Операционные события и личный статус прочтения. |

**Administrator** (`admin`) выполняет изменения. **Viewer · read-only** (`viewer`) читает ресурсы, историю, метрики и логи; не создаёт/изменяет серверы, пользователей, приглашения, credentials, задачи или backups, не запускает диагностику и не отмечает Notifications прочитанными. Это запрет backend, а не только скрытые кнопки. VPN-пользователь — отдельная сущность, не Viewer. Создание Viewer выполняется вне Admin Web через управляемую DBA-процедуру; готового enrollment UI/CLI для него нет.

> Место для снимка A01. «Sign in: поля Username, Password, Authenticator code и кнопка Sign in». Поля пустые, никакого TOTP/пароля.
>
> Место для снимка A02. «Левая навигация с девятью разделами, роль Viewer · read-only и баннер Read-only access». Синтетический login.

## Dashboard: что означает сводка

**Servers** показывает число записей и enabled servers; **Manage servers** открывает Servers. **VPN users** показывает число пользователей и количество с действующим доступом; **View users** открывает VPN users. **Recent jobs** считает queued/running только в последних 50 jobs; **View jobs** открывает первую страницу Jobs. Это не полный счётчик всех старых задач.

**Server status** берётся из последней завершённой диагностики, рядом **Last seen**. `reachable` означает доступность управления по SSH, а не работоспособность VPN; `unknown` — отсутствие подтверждения, `pending` — требуется диагностика. `Disabled` означает запрещённые операции Control Plane. Для текущего service health переходите в Monitoring.

> Место для снимка A03. «Dashboard: три сводных карточки и Server status с Last seen одного тестового узла». Обезличить адреса/имена.

## Servers: регистрация уже подготовленного узла

Сначала выполните [bootstrap отдельного VPS](deployment-guide.md), проверьте host key через provider console и новое SSH-соединение `ttcp`. Кнопки установки bootstrap из Admin Web нет. Provider/root password никогда не вводится в форму.

Откройте **Add managed server**:

| Поле | Что вводить |
| --- | --- |
| Server name | Уникальное имя, 1–128 латинских букв/цифр/`_`/`-`, начинается с буквы/цифры. Например, `Test-01`. |
| SSH hostname | Достижимое из worker DNS-имя или IP узла, без scheme/path. |
| Public IP | IP-адрес узла для проверки DNS; это одно семейство адресов. |
| VPN domain | DNS-имя VPN endpoint, без `https://` и порта. |
| Location | Необязательная понятная пользователю локация. |
| Management SSH user | `ttcp` после repository bootstrap; `root` отклоняется. |
| SSH port | 1–65535; по умолчанию 22. |
| Check HTTP ACME readiness | Проверять свободный TCP 80; при deploy использовать HTTP ACME. По умолчанию включено. |
| Pinned SSH host public key | Проверенный полный public key в виде `тип BASE64`, без комментария. Не fingerprint и не результат непроверенного scan. |
| Management OpenSSH private key | Matching unencrypted private key управления; вводить только в доверенный интерфейс, не provider key. |

**Create server** сохраняет запись/зашифрованную identity, но не устанавливает VPN. Форма очищает SSH material после отправки; при отказе потребуется безопасный повторный ввод. Карточка показывает **SSH host**, **Public IP**, **Management key** (`Configured`/`Missing`), **Pinned host fingerprint**, **Last seen**.

**Edit server details → Save server** изменяет метаданные. Изменения target/pin требуют свежей диагностики и запрещаются при активной server job. **Replace management SSH identity** содержит `Replacement host public key`, `Replacement private key` и **Replace SSH identity**. Сначала отдельно подготовьте новый ключ на узле, сверяйте новый pin через доверенный канал и дождитесь idle jobs; форма сама не устанавливает public key на VPS.

**Disable server** запрещает новые provision/delivery, но не останавливает service, не удаляет workload и не отзывает уже выданные credentials. **Enable server** включает запись; после этого нужна новая диагностика. Кнопки физического удаления сервера в UI нет.

> Место для снимка A04. «Servers: раскрытая Add managed server с полями подключения и пустыми полями SSH keys». Использовать reserved `.invalid` host и синтетический IP, приватный ключ не показывать.

## Pre-flight и жизненный цикл VPN

**Run pre-flight** создаёт job и открывает Jobs. В **Diagnostic checks** смотрите **Ready/Not ready** и каждую проверку. Значения: `pass` — пройдено; `fail` — отказ; `unknown` — не удалось установить; `skipped` — допустимо пропущено. Диагностика может завершиться `succeeded` с **Not ready**.

Проверяются SSH, DNS A/AAAA, свободные TCP 80 (если ACME), TCP/UDP 443, поддерживаемые ОС/CPU, диск, RAM и NTP. Проверка свободного порта на VPS не проверяет внешний firewall. См. [точные пороги](../servers.md). Fresh passing result действует **15 минут**, target/key edits сбрасывают допуск. После deploy занятый 443 — нормальное следствие работающего workload, используйте **Check status** и Monitoring.

Карточка отдельно показывает **Workload**, **Installed version**, **Desired version**. `*_pending` означает queued/running operation; `installed` — подтверждённая установка; `uninstalled` — удалён workload; `failed` — ошибка; `unknown` — исход не подтверждён. Installed version — последняя подтверждённая версия, а не текущая гарантия health. **View lifecycle job** открывает связанную задачу.

| Действие | Условия и результат |
| --- | --- |
| Deploy | Enabled/configured node, нет конфликта jobs, fresh passing pre-flight, явная `TrustTunnel release version` в формате `major.minor.patch`. При ACME — `Certificate contact email`. Без ACME сертификаты готовит оператор. Создаёт workload с пустыми credentials; дождитесь job/health. |
| Update TrustTunnel | Установленный workload, явная новая версия. Сохраняет конфигурацию; возможен перерыв VPN. Не является rollback конфигурации. |
| Restart TrustTunnel | Перезапускает установленный workload; активные VPN connections могут прерваться. При потере ответа не повторяйте вслепую: сверяйте реальное состояние. |
| Uninstall TrustTunnel | Сначала revoke всех credentials на сервере и завершение jobs. Удаляет принадлежащие workload/unit/renewal hook; management доступ/exporter/certificates/firewall остаются. |

В диалогах потенциально разрушительных действий **Keep unchanged** отменяет, **Confirm** подтверждает. Deploy не имеет такого диалога. Lifecycle actions открывают Jobs. Одновременные операции на одном сервере ограничены; при conflict обновите состояние и дождитесь завершения. Timeout/worker loss не обещает автоматического отката lifecycle. Подробнее: [lifecycle recovery](../server-lifecycle.md).

> Место для снимка A05. «Servers: свежий pre-flight, TrustTunnel release version, Certificate contact email, Deploy и View lifecycle job». Версия только тестовая, email обезличен.
>
> Место для снимка A06. «Jobs: pre-flight succeeded с Not ready и конкретным fail/unknown check». Без raw SSH output.

## Ревизии конфигурации и rollback

В карточке Servers → **Server configuration → Configuration revisions** откройте историю; **Close configuration history** закрывает её. **Current revision** может показывать `Deployment baseline`, `Recorded` либо номер загруженной ревизии. Переход на другую страницу истории не меняет конфигурацию узла.

**Create a revision** содержит:

| Настройка | Значение и границы |
| --- | --- |
| Allow IPv6 destinations | Разрешить назначения IPv6; default включено. Это не настройка доступности IPv6 VPS. |
| Allow connections to private networks | Разрешить private destinations; default выключено. Включать только при явной необходимости доступа к внутренней сети. |
| Advanced connection timeouts → TLS handshake timeout (seconds) | 1–120, default 10. |
| Client listener timeout (seconds) | 1–86400, default 600. |
| Connection establishment timeout (seconds) | 1–600, default 30. |
| TCP connection timeout (seconds) | 1–2592000, default 604800. |
| UDP connection timeout (seconds) | 1–86400, default 300. |

**Create revision** валидирует и сохраняет неизменяемую запись, не применяет её. Новая форма начинается с defaults, не с автоматически подставленных активных settings. Для основы выберите **Use revision N settings**, затем проверьте все поля и создайте новую ревизию. **Revision settings** показывает сохранённые значения. История по 50 записей: **Newer revisions**, **Older revisions**.

**Apply revision N** доступна для validated revision без связанной job на установленном enabled workload. После **Confirm** settings применяются с restart и healthcheck, credentials/users сохраняются. **View configuration job** или **View revision N job** открывает результат. Статусы:

| Статус | Значение/действие |
| --- | --- |
| idle / No configuration changes | Нет изменений; baseline. |
| validated / Validated · ready to apply | Сохранено и проверено; ожидает отдельного apply. |
| apply_pending / Apply queued or running | Не запускать новую конфликтующую операцию. |
| applied / Applied successfully | Применение подтверждено. Проверить настоящий VPN-трафик. |
| rolled_back / Apply failed · previous configuration restored | Candidate не принят, предыдущая конфигурация восстановлена; job остаётся failed. |
| rollback_failed | Apply и rollback не подтверждены; нужна проверка узла оператором. |
| unknown | Outcome unconfirmed; сверить реальное состояние прежде нового apply. |
| failed / cancelled | Apply не завершён успешно; смотреть связанную job. |

Отдельной кнопки **Rollback** нет. Для намеренного возврата используйте settings предыдущей подтверждённой ревизии → **Create revision** → **Apply** новой ревизии. Automatic rollback относится к неуспешному apply, не к любой lifecycle failure. Указатель Current — последнее подтверждение PostgreSQL; при unknown это не гарантия фактического состояния. [Технические границы](../server-configuration.md).

> Место для снимка A07. «Configuration revisions: Create a revision, Advanced connection timeouts, Current, validated/applied/rolled_back и View revision job». Только тестовые настройки.

## VPN users: доступ, устройства и credentials

**Add VPN user** раскрывает `Display name`, `Device limit` (целое ≥0, default 3), `Access expires (your local time)` (пусто — без срока), **Create VPN user**. Затем выберите пользователя в списке `VPN user`. **Edit user → Save user** меняет эти же параметры. Просроченный или disabled user не получает новые подключения.

**Disable user** отзывает все устройства/credentials, сохраняя историю. **Enable user** не восстанавливает отозванные устройства и credentials; их нужно создать заново. Delete user в UI отсутствует.

В **Server access** выберите `Access mode`: **Selected servers** с checkboxes или **All servers** (все enabled, включая будущие). **Save access → Confirm** может отозвать credentials на удалённых из доступа серверах. Убедитесь, что оставлен нужный сервер; новый пользователь изначально может иметь пустой selected access.

В **Devices and credentials** видно `Devices: used of device_limit`. Лимит относится к enabled устройствам; несколько конфигураций одного устройства на разных серверах не расходуют дополнительные устройства. `New device name` и необязательный `Platform` → **Add device**. Квота и права проверяются сервером, при conflict обновите экран. Лимит нельзя уменьшить ниже числа активных устройств: backend вернёт 409; сначала явно отзовите ненужные устройства.

Устройство отображается как **Enabled** или **Revoked**. **Revoke device <name>** отзывает credentials этого устройства на всех серверах. Другие устройства сохраняют доступ. Отзыв применяется на узлах фоновыми jobs; проверяйте завершение и отказ нового VPN reconnect.

Для пары «устройство × сервер» **Provision <server>** заказывает credential/configuration; **Retry <server>** повторяет после failed; **Ensure configuration <server>** обеспечивает конфигурацию active credential. Во время `pending`/`revoking` повтор заблокирован. Credentials отображают `pending`, `active`, `failed`, `revoking`, `revoked`; кнопка **Revoke credential** отзывает только эту пару. Pending/active в БД не заменяют проверку действующего VPN. Admin Web не показывает QR/TOML/private VPN secrets; выдача конфигурации пользователю происходит в Client Web.

> Место для снимка A08. «VPN users: user с Device limit 1, сроком, Selected servers, одним устройством и active credential». Без credential password/configuration.

## Invitations: передача доступа пользователю

Выберите `VPN user`; установите **Invitation lifetime**: **1 hour**, **1 day** (default) или **7 days**. **Create invitation** доступна для enabled/unexpired пользователя. **Private invitation link** показывается **один раз**: **Copy link** копирует, **Dismiss link** скрывает. Copy может отказать вне secure browser context; выделите поле и скопируйте вручную в защищённый канал. Скрытие не отзывает приглашение.

Ссылка содержит секрет во fragment `#invite=...`; не отправляйте её в общий чат, screenshots или журнал приёмки. Admin и Client используют разные origins. В локальном сценарии ссылка `vpn.localhost` доступна на той же станции; для телефона нужен подготовленный mobile ingress. Встроенной отправки email/SMS нет, передача — вручную.

Список показывает **Available** (можно использовать), **Used** (вход уже состоялся), **Expired**, **Revoked** и дату истечения. **Revoke invitation** доступна для ещё не использованного/не отозванного приглашения; это не отзыв уже выданного VPN credential. После выхода/истечения Client session создайте новое приглашение; старую Used ссылку восстановить нельзя.

> Место для снимка A09. «Invitations: lifetime, Create invitation, состояния Available/Used/Expired/Revoked». Блок Private invitation link полностью скрыт; не размывать частично.

## Jobs: контролировать завершение операций

**Operations** обновляется каждые 5 секунд, страницы по 50: **Previous jobs**, **Older jobs**. Таблица: **Operation**, **Status**, **Progress**, **Created**, **Details → Inspect <первые 8 символов UUID>**. В деталях видны полный Job ID, attempts, ошибка, diagnostic checks/config apply result и **Live job logs**.

`queued` — ждёт worker; `running` — исполняется; `succeeded` — handler завершён; `failed` — неуспех; `cancelled` — отмена завершена. Progress — ход job, не процент доступности VPN. Для pre-flight обязательно смотрите **Ready**, для lifecycle/config — service/node evidence.

**Cancel job → Confirm** немедленно отменяет queued работу; running job останавливается только если операция cancellable и достигает безопасной точки. Running workload mutations/backups не отменяются. **Cancellation requested; waiting for a safe checkpoint** ещё не означает cancelled. Не закрывайте сервер ради «отмены» неизвестной операции.

Live logs передаются через SSE. При gap старые записи могли быть удалены по retention: **Refresh** загружает сохранённую историю. При network failure обновите экран; не запускайте вторую mutation, пока не выяснили результат первой. UI не имеет универсальной кнопки Retry job: повтор выполняется конкретным действием ресурса после устранения причины. [Jobs reference](../jobs.md).

> Место для снимка A10. «Jobs: одна queued и одна terminal operation, Inspect, details, Attempts и Live job logs». Только safe messages/IDs.

## Monitoring: измерения и их возраст

**System health** обновляется каждые 30 секунд. **Recent period**: **Last hour**, **Last 6 hours**, **Last 24 hours**. Видны **Overall health**, Database (PostgreSQL), Job coordination (Redis), Metrics storage (VictoriaMetrics), Health collector. При ошибке обновления UI может показывать последнюю удачную запись с её временем; это устаревшее наблюдение.

Карточка узла: CPU/Memory/Disk usage, Load (1 minute), Network received/sent, CPU/Memory trends с пропусками, **VPN service**, **VPN process**, **Service observation**, **Automatic restarts**. **Management diagnostic** раскрывает последний management status/Last seen. `healthy` — подтверждённые измерения; `degraded` — требуется внимание; `unavailable` — наблюдение недоступно; `unknown` — недостаточно данных; `disabled` — запись отключена. `Unavailable`, `Unknown` и gap не означают ноль нагрузки или работающий сервис.

Grafana — отдельный интерфейс на локальном порту 3000, не пункт Admin Web; dashboard **TrustTunnel managed nodes**. Collector читает exporter/service по pinned SSH, exporter не нужно открывать Internet. [Monitoring](../admin-visibility.md), [alerts и grace periods](../operational-alerts.md). Не все предполагаемые классы alert реализованы; отсутствие уведомления не является проверкой работоспособности.

Реализованные health alerts: недоступное/устаревшее наблюдение узла (**NODE_OFFLINE**) и service/process/helper failure (**SERVICE_DOWN**) требуют устойчивого подтверждения 60 секунд; disk/memory usage ≥90% (**DISK_HIGH**, **MEMORY_HIGH**) — 300 секунд. Восстановление также требует свежих измерений; пропажа collector/метрик не закрывает incident. Сроки являются debounce, а не обещанием доставки за точно такое время. Отдельных автоматических alerts о сроке сертификата, насыщении канала и новой доступной версии сейчас нет. **BACKUP_FAILED** добавлен backup jobs в TTCP-020; это не правило на основе node metrics. Telegram-уведомления отсутствуют.

> Место для снимка A11. «Monitoring: System health, Last hour, одна карточка с CPU/Memory trends и VPN service/process». Даты свежие, имена тестовые, missing sample показан как gap.

## Audit и Notifications

**Audit log**: новые события сначала, обновление каждые 30 секунд. Фильтры `Action`, `Result`, `Actor type` (All actors/Administrator/VPN user/System), `Actor ID`, `From (local time)`, `Until (local time)`. **Filter by target or request** раскрывает `Target type`, `Target ID`, `Request ID`. **Apply filters** применяет, **Clear filters** сбрасывает; **Previous audit events**, **Older audit events** листают по 50. Таблица: Time, Action/result, Actor, Target, Request. Указывайте точные action/result из событий, например `server.create`/`success`; UI не удаляет и не редактирует audit.

**Notifications → Operational and security events** обновляется каждые 30 секунд, показывает severity `info`, `warning`, `critical`, дату, **Read/Unread**, текст и **Event context**. **Unread only** фильтрует; счётчик в навигации — личный unread count. **Mark read**, **Mark unread** меняют статус только текущего admin account; Viewer только читает. **Inspect job** появляется для job target; **Previous notifications**, **Older notifications** листают историю. Прочтение не устраняет причину инцидента и не подтверждает здоровье узла.

> Место для снимка A12. «Audit: фильтр Action, actor/time/target/request и безопасная строка события». Без secrets/реальных идентификаторов пользователей.
>
> Место для снимка A13. «Notifications: unread critical event, Event context, Inspect job и Mark read». Только синтетический инцидент.

## Backups и типовые операции восстановления

**Remote PostgreSQL backups** показывает **Latest backup**, **Last verified backup**, **Snapshot age** и **History** с attempts/status. **Run backup now** доступна admin при настроенном backup и отсутствии активной backup job. **Inspect backup job** открывает Jobs; **Previous backups**, **Next backups** листают по 50. Статусы queued/running/succeeded/failed/unknown/cancelled; `unknown` может означать persisted upload без подтверждения — нужен maintenance verify, не перезапись объекта.

**Remote backups are not configured. Contact the operator.** — base Compose не подключает backup автоматически. Кнопок настройки S3, скачивания архива, Restore и расписания в UI нет. Оператор готовит [protected backup config](../backups.md), роли БД, S3 permissions, key escrow и overlay; выполняет [verify/restore drill](../runbooks/backup-restore.md). Dump не содержит global roles, master key, TLS, monitoring volumes и файлов VPS. Restore выполняется только в новую пустую isolated DB с quarantine старых jobs/sessions. Успешная backup job без restore drill не доказывает recoverability.

> Место для снимка A14. «Backups: last verified backup, Snapshot age, Run backup now и History». Никаких storage credentials/архивов.

| Ситуация | Последовательность действий |
| --- | --- |
| Первый пользователь | Servers: установленный здоровый узел → VPN users: создать/дать доступ → Invitations: приватная ссылка → Client Web: device/provision/import → реальный traffic test. |
| Потерян телефон | VPN users: выбрать устройство → Revoke device → дождаться jobs → проверить reconnect отказ. При необходимости новое приглашение и новый device. |
| Смена версии | Выбрать approved explicit version → Update TrustTunnel/Confirm → Jobs/Monitoring → проверка прежних клиентов; неизвестный исход сверить на VPS. |
| Возврат настроек | Use предыдущие settings → Create revision → Apply/Confirm → Jobs/Monitoring/VPN. При rollback_failed — node inspection/recovery. |
| Нет server access | VPN users: срок/enabled/access; Servers: enabled/installed; Client обновляет список. Не пересоздавать device для каждого сервера. |
| Ошибка 409 | Refresh; active job, quota, stale pre-flight или изменившийся ресурс. Устранить конкретное условие, затем повторить действие. |
| Длительная queued/нет метрик | Оператор проверяет dependency health, worker/collector/SSH. Не сбрасывать SQL/Redis и не удалять volumes. |
| Удаление тестового сервера | Revoke credentials и завершить jobs → Uninstall → проверка workload → отдельно provider teardown. Disable не выполняет uninstall. |

Для инфраструктурных сбоев: [incident recovery](../runbooks/incident-recovery.md); upgrades: [migrations runbook](../runbooks/upgrade-migrations.md). В обращении оператору передайте время, действие, safe job/request ID и ошибку без ключей/ссылок/конфигураций. Полный список ограничений: [DOC/DEPLOY gaps](deployment-guide.md).
