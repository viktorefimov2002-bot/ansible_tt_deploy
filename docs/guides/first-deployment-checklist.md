# Приёмка первого развёртывания TTCP-022A

Ручная проверка оператором, не CI и не инструкция автоматически менять реальные ресурсы. Выполняйте на **отдельном одноразовом VPS**, подготовленном по [deployment guide](deployment-guide.md), с тестовыми пользователями. Для release/production дополнительно обязателен полный [staging/live checklist](../runbooks/staging-live-acceptance.md); эта таблица его не заменяет.

Заполняйте копию таблицы в защищённом журнале оператора вне checkout. PASS требует наблюдения; FAIL требует причины/ответственного. Пустое поле означает **не выполнено**. Для неприменимого шага запишите N/A с причиной и ограничением охвата, не PASS. Не сохраняйте реальные адреса, секреты, invite/QR/TOML, cookies, credentials, raw DB/SSH output или private keys в документации/публичном отчёте. Safe evidence: время, job/audit/request ID, обезличенное наблюдение и редакторски очищенный screenshot без secret content.

## Паспорт проверки

| Поле | Значение оператора |
| --- | --- |
| Дата/часовой пояс, оператор | ____ |
| Checkout revision / image digests | ____ |
| Windows, WSL, Docker, Compose, браузер | ____ |
| Ubuntu release/CPU, условное имя VPS | ____ |
| Ссылка на закрытую inventory/fingerprint запись | ____ |
| Два Admin/Client origins и доверие TLS проверены (без private key) | ____ |
| Явно выбранная TrustTunnel release / native client platform+version | ____ |
| Alembic current/head | ____ |
| Только disposable ресурсы, recovery console доступна | ____ |
| Согласованные backup RPO/RTO / teardown decision | ____ |

## Выполнить по порядку

| № | Действие и критерий PASS | PASS/FAIL/N/A | Безопасное доказательство и время | Причина FAIL / следующий шаг / ответственный |
| --- | --- | --- | --- | --- |
| 01 | Сверить checkout, чистоту секретов, ресурсы и команды по guide; target действительно disposable. | ____ | ____ | ____ |
| 02 | Docker Server доступен, Linux containers/WSL2, Compose ≥2.24.4. Нет конфликтов портов. | ____ | ____ | ____ |
| 03 | Env/master key создан без console output; ACL/0600 и независимый escrow проверены. | ____ | ____ | ____ |
| 04 | `config --quiet`, pull/build, Postgres/Redis startup без ошибок; миграции завершены, current=head. | ____ | ____ | ____ |
| 05 | `ps` и API/worker/collector/NGINX healthchecks успешны; SQL/Redis/metrics не публикуются. | ____ | ____ | ____ |
| 06 | Interactive admin enrollment с TOTP успешен; повтор отказывает без перезаписи. | ____ | ____ | ____ |
| 07 | Оба HTTPS origins доверены браузером; login/refresh/logout работают, Client/Admin сессии раздельны. HTTP-only фиксируется как ограничение, не полная приёмка. | ____ | ____ | ____ |
| 08 | Admin/Client cross-origin privileged routes и неизвестные hosts закрыты; Viewer mutation получает 403. Viewer account заранее подготовлен DBA. | ____ | ____ | ____ |
| 09 | Management Ed25519 и out-of-band pin подготовлены; wrong pin отвергается на disposable соединении. Provider secrets не переданы CP. | ____ | ____ | ____ |
| 10 | Bootstrap с provider password, затем отдельно provider key проверен на разрешённых disposable resources; оба новых management connections работают. Один проверенный способ не подтверждает второй. | ____ | ____ | ____ |
| 11 | Повтор bootstrap с тем же ключом безопасен; arbitrary sudo/helper arguments отказаны; exporter только loopback; recovery access сохранён. | ____ | ____ | ____ |
| 12 | Registration сохранена, private key не возвращается; worker pre-flight Ready, все checks pass/legitimate skipped, fresh <15 min. | ____ | ____ | ____ |
| 13 | DNS A/AAAA, provider/host firewall, SSH egress и внешний TCP/UDP 443 проверены отдельно; ACME TCP80 доступен извне. | ____ | ____ | ____ |
| 14 | Deploy выбранной release успешен: job ID, installed version, service/process health; сертификат и renewal подтверждены. | ____ | ____ | ____ |
| 15 | User с non-default device_limit создан, selected access сохранён, приглашение передано приватно и работает один раз. | ____ | ____ | ____ |
| 16 | Device создан; превышение реального лимита отвергается; новый server credential того же device не расходует квоту (при втором staging server). | ____ | ____ | ____ |
| 17 | Provision достигает ready; QR/deeplink/TOML получены; после refresh/смены выбора secrets очищены, сессия сохранена. | ____ | ____ | ____ |
| 18 | Реальный клиент импортировал профиль; VPN подключён, ожидаемый выход/DNS/IPv4/IPv6 и обычный web traffic подтверждены. | ____ | ____ | ____ |
| 19 | Mobile portal login проверен на достижимом доверенном HTTPS origin либо записан DEPLOY-02; QR-import со станции не заменяет mobile portal test. | ____ | ____ | ____ |
| 20 | `dc stop` не прерывает ранее настроенный действующий VPN; `dc start --wait` восстанавливает CP, очередь и monitoring. | ____ | ____ | ____ |
| 21 | Другой тестовый device revoked: job завершён, delivery denied, новое VPN reconnect отказано; измерена задержка/поведение старой сессии. | ____ | ____ | ____ |
| 22 | Disabled/expired user и удалённый server access не дают нового provision/delivery; logout не разрешает повтор Used invitation. | ____ | ____ | ____ |
| 23 | Valid revision Create/Apply подтверждена на VPS; контролируемая apply failure на disposable узле вызывает truthful rollback/job failure. | ____ | ____ | ____ |
| 24 | Update/restart по подтверждению: версия/health/прежний client traffic проверены; uncertain failure сверена до retry. | ____ | ____ | ____ |
| 25 | Monitoring freshness, пропуски, audit и Notifications наблюдаются; mark read действует только для аккаунта, Viewer только читает. | ____ | ____ | ____ |
| 26 | Backup overlay/reader/S3/keyring подготовлены; verified backup UUID и off-host readback; нет записи secrets в job/audit. | ____ | ____ | ____ |
| 27 | Restore в новую пустую isolated DB по runbook; sessions/jobs quarantined, head/counts/decryption проверены, RPO/RTO записаны. | ____ | ____ | ____ |
| 28 | Teardown: credentials revoked → jobs завершены → Uninstall проверен; `down` сохраняет CP volumes. `down --volumes` только при записанном решении уничтожить disposable данные. | ____ | ____ | ____ |
| 29 | Итоговый evidence не содержит secrets/live configs; gaps, N/A и владелец следующего действия записаны. | ____ | ____ | ____ |

Не вводите произвольные неправильные settings в production для проверки rollback. Процедуры fault injection, второго VPS и restore drill требуют отдельного согласованного disposable scope по staging runbook. Пока scope/ресурс не подготовлен, шаг остаётся pending, а не предполагаемо успешным.

## Решение оператора

- Результат: ____ (локальная проверка / staging принят / не принят).
- Открытые [DOC/DEPLOY gaps](deployment-guide.md), FAIL/N/A и их влияние: ____.
- Ответственный/срок устранения: ____.
- Последний проверенный backup и план восстановления: ____.
- Подпись/дата владельца: ____.

Ни эта таблица, ни статическая проверка документации не объявляют систему готовой к production автоматически.
