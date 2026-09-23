# Architecture Decision Records

ADR фиксирует контекст, выбранное решение и его последствия для устойчивого архитектурного выбора. Источник исходных ограничений — [спецификация v0.1](../architecture/product-architecture-spec-v0.1.md); рабочие правила — [AGENTS.md](../../AGENTS.md).

## Оформление и жизненный цикл

1. Использовать [шаблон](template.md) для одного конкретного решения. Файл именовать `NNNN-short-kebab-case-title.md`; номер уникален и не переиспользуется.
2. Для базовых решений учитывать перечень и номера 0001–0011 из раздела 29 спецификации. Шаблон и этот README не занимают номера ADR.
3. Новое предложение имеет статус `Proposed`. Указать задачу, релевантные разделы спецификации, альтернативы, обоснование, последствия, влияние на безопасность и эксплуатацию.
4. Статус `Accepted` ставить только после явного согласования решения владельцем проекта; записать дату и ссылку на источник согласования. Наличие файла или шаблона само по себе не означает принятия решения.
5. Отклонённое предложение получает статус `Rejected`. Принятое решение, заменённое другим ADR, получает статус `Superseded` со взаимными ссылками. Не переписывать историю принятого решения для скрытой смены архитектуры.
6. Добавлять ADR в реестр ниже. Явно обозначать расхождения со спецификацией; предложение не отменяет её автоматически. Для обычных локальных правок ADR не требуется.

Не включать секреты, production credentials или персональные данные. Рассматривать безопасность, миграцию и rollback только в пределах конкретного решения; не считать описание желаемого поведения свидетельством его реализации.

## Реестр

| ADR | Status |
| --- | --- |
| [0004 — Ansible execution adapter](0004-ansible-execution-adapter.md) | Proposed; implemented in TTCP-008, owner acceptance pending |
| [0012 — Administrative authentication and sessions](0012-admin-auth-sessions.md) | Proposed; implemented in TTCP-006, owner acceptance pending |
| [0013 — Durable job worker](0013-durable-job-worker.md) | Proposed; implemented in TTCP-007, owner acceptance pending |
| [0014 — Server SSH trust](0014-server-ssh-trust.md) | Accepted under TTCP-009 delegated decision authority |
| [0015 — Managed-node bootstrap](0015-managed-node-bootstrap.md) | Proposed; implemented in TTCP-010, owner acceptance pending |
| [0016 — Observability transport](0016-observability-transport.md) | Proposed; implemented in TTCP-011, live-node acceptance pending |

Решения, уже установленные спецификацией, сохраняют силу независимо от отсутствия отдельных ADR; их последующее оформление не должно выдумывать историю согласования.

## Намеренно открытые вопросы

| Вопрос | Когда возвращаться к нему |
| --- | --- |
| Enrollment и ротация SSH host keys | Решено в [ADR-0014](0014-server-ssh-trust.md): out-of-band проверка, строгий pinning, явная аудируемая замена администратором. |
| Удаление исторических jobs | TTCP-007 ограничивает history одной job; автоматическое удаление metadata требует отдельного решения. |

TTCP-001 не выбирает варианты по этим вопросам. Состояние jobs в PostgreSQL и использование Redis для асинхронного взаимодействия уже определены спецификацией; открытые вопросы не меняют этих ограничений.

См. также [индекс архитектуры](../architecture/README.md) и [план MVP](../planning/mvp.md).
