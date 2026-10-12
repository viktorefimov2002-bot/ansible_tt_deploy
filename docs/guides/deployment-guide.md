# Установка и первый запуск TrustTunnel Control Plane

Руководство TTCP-022A для оператора. Сценарий: Control Plane на рабочей станции Windows с Docker Desktop/WSL2, отдельный **одноразовый Ubuntu VPS** для VPN, оба веб-интерфейса сначала открываются на рабочей станции. Команды VPS не выполняйте на WSL или на существующем VPN-сервере.

Продолжение: [Admin Web](admin-web-guide.md), [личный кабинет](client-web-guide.md), [чек-лист первого развёртывания](first-deployment-checklist.md). Архитектурный источник: [спецификация v0.1](../architecture/product-architecture-spec-v0.1.md). Задание TTCP-022A задано запросом на документацию; отдельной спецификации задачи в checkout нет.

## Что реализовано и что нужно проверить вживую

В коде есть API, PostgreSQL, Redis, worker, Admin Web, Client Web, SSH bootstrap, pre-flight, deploy/update/restart/uninstall, ревизии конфигурации, мониторинг, аудит, уведомления и подключаемые зашифрованные удалённые резервные копии. PostgreSQL хранит долговременное состояние; Redis помогает доставлять задачи. VPN-трафик идёт непосредственно на VPS, минуя Control Plane.

Наличие реализации не означает успешной приёмки на вашем оборудовании. Сборка контейнеров, Secure cookies в выбранном браузере, SSH из worker, apt/systemd, ACME, импорт в реальное приложение и VPN-трафик проверяются по чек-листу. Результаты безопасной проверки документации: [протокол](documentation-validation.md).

| Среда | Как использовать |
| --- | --- |
| Локальная разработка | Базовый Compose, HTTP на loopback, синтетические/тестовые данные. Проверка страниц возможна; вход зависит от поддержки Secure cookies браузером. |
| Первый staging-запуск | Тот же код, одноразовый VPS, отдельные тестовые пользователи, доверенные HTTPS origins для полной браузерной приёмки. Данные и ключи хранить защищённо. |
| Production | Отдельный постоянно доступный хост, DNS/TLS, firewall, отдельные роли БД, off-host backup и проверенный restore; [deployment runbook](../runbooks/control-plane-deployment.md). Сон/выключение рабочей станции мешает новым подключениям через портал, задачам отзыва и мониторингу. |

Работающий VPN с ранее выданными действующими конфигурациями должен продолжаться при остановке Control Plane; это проверяется вживую. Просрочка/отзыв на VPS требуют работающего worker и SSH. Не рассчитывайте на мгновенный разрыв уже открытых сессий без проверки клиента.

## Требования и подготовка Windows

Нужны поддерживаемая версия Windows, аппаратная виртуализация, WSL2 и Docker Desktop в режиме **Linux containers**. Текущие требования Docker включают 8 GB памяти рабочей станции и WSL 2.1.5+; сверяйте их с [официальной инструкцией Docker](https://docs.docker.com/desktop/setup/install/windows-install/). Для стека выделите хотя бы 2 GiB, предпочтительно 4 GiB; учтите дополнительную память сборки. Размер диска зависит от образов, данных, метрик и retention: проверяйте свободное место до запуска, named volumes не являются backup.

Проверка из PowerShell:

```powershell
wsl --version
wsl --list --verbose
docker version
docker compose version
```

Если WSL отсутствует, в административном PowerShell выполните установку и перезагрузку согласно [Microsoft](https://learn.microsoft.com/en-us/windows/wsl/install):

```powershell
wsl --install -d Ubuntu
wsl --update
```

Установите и запустите Docker Desktop, включите WSL2 backend и интеграцию нужного дистрибутива. `docker version` должен показывать Server, а не только Client. Для всех описанных overlay нужен Compose **2.24.4+** (`!override`); старый `docker-compose` v1 не подходит. На обычном Linux нужен Docker Engine с Compose plugin; Windows-команды ACL там заменяются POSIX permissions.

Нужны Git/checkout выбранной ревизии, браузер, менеджер паролей и TOTP-приложение. Для SSH со станции — OpenSSH client; в WSL/Linux — `ssh`, `scp`, `ssh-keygen`. Node.js, pnpm, Python/Ansible на Windows не нужны для запуска контейнерного приложения. Linux-путь генерации секретов ниже использует системный `python3`; Ansible находится в worker image.

Для VPS выбирайте Ubuntu **22.04 или 24.04**, x86_64/aarch64, systemd, OpenSSH, рабочие apt repositories и доступ к консоли провайдера. Практический начальный размер из спецификации — 1 vCPU/2 GB RAM; минимумы pre-flight (512 MiB RAM, 1 GiB свободно на `/`) являются проверкой допуска, а не обещанием достаточной ёмкости.

## Checkout и защищённые секреты

Используйте проверенный checkout нужного release. Запишите `git rev-parse HEAD` и `git status --short` в журнал приёмки. Не включайте локальные приватные ключи, приглашения или клиентские конфигурации в архив исходников. Нельзя одновременно запускать один проект из PowerShell и WSL с разными env/путями.

Ниже секреты создаются **за пределами checkout**. Каждая процедура отказывается перезаписывать env. Не включайте transcript, shell tracing или вывод содержимого файла. Сделайте независимую защищённую копию `TTCP_AUTH_ENCRYPTION_KEY`: потеря ключа лишает доступа к TOTP, SSH identities и сохранённым VPN-конфигурациям. Новый случайный ключ старые данные не расшифрует.

### PowerShell: генерация без вывода значений

Откройте PowerShell в корне checkout. Каталог ниже доступен только текущему пользователю по явному ACL; администратор ОС/Docker всё равно входит в границу доверия.

```powershell
$ErrorActionPreference = 'Stop'
$ttcpRepo = (Get-Location).Path
$ttcpSecretDir = Join-Path $env:LOCALAPPDATA 'TTCP-first'
New-Item -ItemType Directory -Force -Path $ttcpSecretDir | Out-Null
$ttcpAcl = Get-Acl -LiteralPath $ttcpSecretDir
$ttcpAcl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($ttcpAcl.Access)) { $ttcpAcl.RemoveAccessRuleSpecific($rule) }
$ttcpIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$ttcpRule = New-Object System.Security.AccessControl.FileSystemAccessRule($ttcpIdentity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$ttcpAcl.SetAccessRule($ttcpRule)
Set-Acl -LiteralPath $ttcpSecretDir -AclObject $ttcpAcl
$ttcpEnvPath = Join-Path $ttcpSecretDir 'runtime.env'
if (Test-Path -LiteralPath $ttcpEnvPath) { throw 'Env уже существует; не перезаписывайте ключи.' }
function New-TtcpRandomBytes {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ,$bytes
}
function New-TtcpPassword {
    return ([BitConverter]::ToString((New-TtcpRandomBytes))).Replace('-', '').ToLowerInvariant()
}
$ttcpMasterKey = [Convert]::ToBase64String((New-TtcpRandomBytes)).Replace('+', '-').Replace('/', '_')
$ttcpLines = @(
    'POSTGRES_DB=ttcp', 'POSTGRES_USER=ttcp',
    ('POSTGRES_PASSWORD=' + (New-TtcpPassword)),
    ('REDIS_PASSWORD=' + (New-TtcpPassword)),
    'GRAFANA_ADMIN_USER=admin',
    ('GRAFANA_ADMIN_PASSWORD=' + (New-TtcpPassword)),
    ('TTCP_AUTH_ENCRYPTION_KEY=' + $ttcpMasterKey),
    'TTCP_AUTH_SESSION_SECONDS=28800', 'HTTP_PORT=8080',
    'GRAFANA_PORT=3000', 'VM_RETENTION=7d', 'TTCP_LOG_LEVEL=INFO'
)
$ttcpStream = [IO.File]::Open($ttcpEnvPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$ttcpWriter = New-Object IO.StreamWriter($ttcpStream, (New-Object Text.UTF8Encoding($false)))
try { foreach ($line in $ttcpLines) { $ttcpWriter.WriteLine($line) } } finally { $ttcpWriter.Dispose() }
Remove-Variable ttcpLines,ttcpMasterKey
```

Проверьте ACL через свойства безопасности файла; не делайте папку общей. Docker получает env, поэтому `docker inspect` также является чувствительным выводом. Не публикуйте его.

### WSL/Linux: генерация без вывода значений

В Bash, из корня checkout (на WSL предпочтителен Linux filesystem). Переменные живут в текущем терминале:

```bash
ttcp_repo="$PWD"
ttcp_secret_dir="$HOME/.config/ttcp-first"
umask 077
mkdir -p "$ttcp_secret_dir"
chmod 700 "$ttcp_secret_dir"
ttcp_env_path="$ttcp_secret_dir/runtime.env"
python3 - "$ttcp_env_path" <<'PY'
import base64
import os
import secrets
import sys
values = {
    "POSTGRES_DB": "ttcp", "POSTGRES_USER": "ttcp",
    "POSTGRES_PASSWORD": secrets.token_hex(32),
    "REDIS_PASSWORD": secrets.token_hex(32),
    "GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": secrets.token_hex(32),
    "TTCP_AUTH_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "TTCP_AUTH_SESSION_SECONDS": "28800", "HTTP_PORT": "8080",
    "GRAFANA_PORT": "3000", "VM_RETENTION": "7d", "TTCP_LOG_LEVEL": "INFO",
}
with open(sys.argv[1], "x", encoding="utf-8") as output:
    output.writelines(f"{key}={value}\n" for key, value in values.items())
os.chmod(sys.argv[1], 0o600)
PY
```

Если файл уже есть, остановитесь и используйте его; не удаляйте его ради повторного выполнения. Остальные параметры перечислены в [.env.example](../../infra/compose/.env.example) и [Compose reference](../../infra/compose/README.md). Для локального теста допустима owner-role БД по умолчанию. Для staging/production создайте отдельные `POSTGRES_APP_USER` и `POSTGRES_APP_PASSWORD` через защищённую DBA-сессию, примените [runtime grants](../../infra/compose/postgres/runtime-grants.sql) как владелец схемы; обе переменные задаются вместе. Миграции и bootstrap используют owner, приложение — runtime role. Не вводите пароль роли в SQL из истории терминала.

Не меняйте имя БД/пользователя/пароль в env с ожиданием переинициализации существующего тома: `POSTGRES_*` и начальный пароль Grafana действуют при первом создании данных. Ротация требует процедуры на существующем хранилище.

## Запуск Compose и миграции

Чтобы не потерять env/имя проекта, определите одну команду-обёртку. Это локальная функция терминала, а не новый CLI продукта. Все дальнейшие `dc` относятся **только к проекту `ttcp-first`**.

PowerShell:

```powershell
$ttcpFiles = @('-f', (Join-Path $ttcpRepo 'infra/compose/compose.yaml'))
function dc {
    & docker compose --project-name ttcp-first --env-file $ttcpEnvPath @ttcpFiles @args
    if ($LASTEXITCODE -ne 0) { throw 'Compose завершился с ошибкой; проверьте предыдущий шаг.' }
}
```

Bash (WSL/Linux):

```bash
ttcp_files=(-f "$ttcp_repo/infra/compose/compose.yaml")
dc() { docker compose --project-name ttcp-first --env-file "$ttcp_env_path" "${ttcp_files[@]}" "$@"; }
```

Общие команды по одной, проверяйте успех каждой:

```text
dc config --quiet
dc pull --ignore-buildable
dc up -d --wait --wait-timeout 180 postgres redis
dc --profile tools run --rm --build migrate
dc --profile tools run --rm migrate python -m alembic current
dc up -d --build --wait --wait-timeout 180
dc ps
```

`migrate` выполняет `alembic upgrade head`; `current` должен совпадать с head выбранного checkout. Не запускайте worker до миграций. Build скачивает зависимости/образы; сеть и доступность pinned packages проверяются на станции. Если startup истёк по таймауту, смотрите `ps`/логи, не удаляйте тома.

Не печатайте полный `docker compose config` с реальным env: он раскрывает секреты. Для проверки используйте `--quiet`. Базовый Compose публикует только NGINX на `127.0.0.1:8080` и Grafana на `127.0.0.1:3000`. SQL, Redis, API, VictoriaMetrics, collector не публикуются. Worker и collector имеют outbound `management_egress` для SSH/DNS.

## Интерактивное создание первого администратора и TOTP

В менеджере паролей/доверенном offline enrollment tool создайте seed из **32 символов uppercase base32**. Настройте его в authenticator: SHA1, 6 цифр, 30 секунд. Пароль администратора — 12–256 символов. Сохраните защищённую recovery-копию seed; не используйте онлайн-сервис QR. Синхронизируйте часы станции/телефона. CLI **не генерирует и не показывает seed/QR**.

Передайте существующий master key из защищённого env в окружение текущего терминала, без вывода. PowerShell:

```powershell
$ttcpKeyLine = [IO.File]::ReadAllLines($ttcpEnvPath) | Where-Object { $_.StartsWith('TTCP_AUTH_ENCRYPTION_KEY=') }
if (@($ttcpKeyLine).Count -ne 1) { throw 'Ожидается ровно один master key.' }
$env:TTCP_AUTH_ENCRYPTION_KEY = $ttcpKeyLine.Substring('TTCP_AUTH_ENCRYPTION_KEY='.Length)
Remove-Variable ttcpKeyLine
try { dc --profile tools run --rm -it -e TTCP_AUTH_ENCRYPTION_KEY migrate python -m apps.api.bootstrap_admin } finally { Remove-Item Env:TTCP_AUTH_ENCRYPTION_KEY }
```

Bash, для env, созданного выше (без кавычек/многострочных значений):

```bash
export TTCP_AUTH_ENCRYPTION_KEY="$(sed -n 's/^TTCP_AUTH_ENCRYPTION_KEY=//p' "$ttcp_env_path")"
dc --profile tools run --rm -it -e TTCP_AUTH_ENCRYPTION_KEY migrate python -m apps.api.bootstrap_admin
unset TTCP_AUTH_ENCRYPTION_KEY
```

Введите username, пароль дважды, seed и текущий OTP в интерактивном TTY; секретные поля скрыты. Дождитесь сообщения `Initial administrator enrolled`, затем **следующего** OTP для веб-входа. `.env` сам по себе не экспортирует переменные shell; поэтому нужен этот шаг. Bootstrap откажет, если admin-role account уже существует; это не reset. Управления admin/viewer аккаунтами и восстановления TOTP в UI нет — [authentication reference](../authentication.md).

## HTTP, локальный HTTPS и первая проверка страниц

Откройте `http://admin.localhost:8080/` и `http://vpn.localhost:8080/` на станции. Если имена не разрешаются, добавьте оба имени к loopback в hosts станции (Windows: `C:\Windows\System32\drivers\etc\hosts`, Linux: `/etc/hosts`) с административными правами. Адрес по одному IP не заменяет эти имена: NGINX выбирает отдельный host. `http://127.0.0.1:8080/healthz` — проверка входной точки; корень IP возвращает 404.

Admin: `Username`, `Password`, `Authenticator code`, `Sign in`; Client без приглашения показывает «Вход по приглашению». Вход должен сохраняться после обновления страницы. Оба интерфейса используют **Secure/HttpOnly** cookies. На HTTP loopback браузеры ведут себя по-разному; успешное открытие HTML не доказывает рабочий вход. Если cookie отвергается/следующий запрос 401, это **DEPLOY-01**, а не повод отключать Secure или передавать браузеру bearer token.

Для полной проверки используйте существующий [HTTPS overlay](../../infra/compose/compose.production.yaml), предварительно обеспечив доверенные сертификаты и ограничение ingress. Для локальной проверки допустимы `admin.localhost` и `vpn.localhost` с сертификатами, доверенными **на станции**. Репозиторий не выпускает локальный CA/сертификаты и не устанавливает trust: до их подготовки процедура HTTPS заблокирована. Структура каталога, вне checkout:

```text
tls/
  admin/fullchain.pem
  admin/privkey.pem
  client/fullchain.pem
  client/privkey.pem
```

В защищённом env задайте `TTCP_ADMIN_HOST=admin.localhost`, `TTCP_CLIENT_HOST=vpn.localhost`, `TTCP_TLS_DIR` — абсолютный путь каталога сертификатов, доступный Docker. В WSL используйте Linux path, в PowerShell — путь Docker Desktop к Windows-каталогу. Не копируйте dangling symlinks Certbot без их target. Перед включением overlay закройте вход с LAN/Internet firewall станции: **overlay публикует 80/443 на всех интерфейсах**, в отличие от базового Compose.

Добавьте overlay к текущему набору файлов. PowerShell:

```powershell
$ttcpFiles += @('-f', (Join-Path $ttcpRepo 'infra/compose/compose.production.yaml'))
```

Bash:

```bash
ttcp_files+=(-f "$ttcp_repo/infra/compose/compose.production.yaml")
```

Общие команды:

```text
dc config --quiet
dc up -d --build --wait --wait-timeout 180 nginx
dc exec nginx nginx -t
```

Откройте `https://admin.localhost/`, `https://vpn.localhost/` без certificate warnings. Порт 8080 с хоста заменён на 80/443; `HTTP_PORT` overlay не управляет. Healthcheck NGINX остаётся на внутреннем 8080. Не используйте `curl -k` как доказательство доверенного TLS.

Для staging/production вместо localhost нужны два разных имени `admin.<домен>` и `vpn.<домен>`. Автоматическая ссылка приглашения заменяет префикс `admin.` на `vpn.`, сохраняя схему/порт; выбирайте согласованную пару. `VITE_CLIENT_PORTAL_URL` — build-time параметр frontend, а не runtime env Compose. Готовой настройки произвольной несогласованной пары через Compose нет.

Телефон трактует localhost как **себя**. Базовый listener на loopback недоступен с телефона или VPS. Для мобильной приёмки нужны достижимое имя станции, доверенный на телефоне TLS, маршрутизация и явный firewall; готового безопасного mobile ingress в репозитории нет (**DEPLOY-02**). QR можно показать на станции и считать приложением телефона после подготовки конфигурации для устройства телефона; этот способ сам по себе не доказывает вход в портал с телефона.

## Management SSH key и доверие к VPS

Используйте отдельный management key, не provider/root key. Bootstrap принимает только Ed25519 public key без комментария. API сейчас принимает **незашифрованный OpenSSH private key** (`password=None`); encrypted private key не поддержан (**DEPLOY-03**). Защитите незашифрованный файл ACL/0600, API сохранит его зашифрованным master key. Никогда не отправляйте private key на VPS.

PowerShell, после подготовки защищённого каталога:

```powershell
$ttcpManagementKey = Join-Path $ttcpSecretDir 'management-key'
if (Test-Path -LiteralPath $ttcpManagementKey) { throw 'Ключ уже существует; не перезаписывайте его.' }
ssh-keygen -t ed25519 -f $ttcpManagementKey -C ttcp-management
if ($LASTEXITCODE -ne 0) { throw 'Ключ не создан.' }
$ttcpPublicFields = ([IO.File]::ReadAllText($ttcpManagementKey + '.pub').Trim() -split '\s+')
[IO.File]::WriteAllText((Join-Path $ttcpSecretDir 'management-bootstrap.pub'), ($ttcpPublicFields[0] + ' ' + $ttcpPublicFields[1] + "`n"), [Text.Encoding]::ASCII)
```

В интерактивном запросе passphrase оставьте пустым только для этого автоматизированного management key. Отдельно защитите public key для передачи: сохраните из `.pub` **только первые два поля** (`ssh-ed25519 BASE64`), без комментария, с LF и без BOM. Bash:

```bash
ssh-keygen -t ed25519 -f "$ttcp_secret_dir/management-key" -C ''
awk '{print $1 " " $2}' "$ttcp_secret_dir/management-key.pub" > "$ttcp_secret_dir/management-bootstrap.pub"
chmod 600 "$ttcp_secret_dir/management-key"
```

В Bash также сначала убедитесь, что private key по этому пути отсутствует; если `ssh-keygen` спрашивает о перезаписи, откажитесь. Не заменяйте рабочую identity случайной повторной генерацией.

Из аутентифицированной консоли провайдера получите public host key и SHA256 fingerprint:

```bash
cat /etc/ssh/ssh_host_ed25519_key.pub
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

Это public материал, но не вставляйте реальные ключи/адреса в документацию. В защищённом локальном `known_hosts` запишите `hostname ssh-ed25519 BASE64` (для порта кроме 22: `[hostname]:port ...`). Адрес должен совпадать с SSH hostname, а ключ — с provider console. `ssh-keyscan` не является источником доверия. При mismatch остановитесь и сверяйте через консоль; не используйте `StrictHostKeyChecking=no`.

Следующие примеры — **Bash в WSL/Linux**, `node.example.invalid`, путь и provider username замените защищёнными локальными значениями. Для нестандартного порта добавьте `-p` к ssh и `-P` к scp. Первичный вход по паролю:

```bash
ssh -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$ttcp_secret_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null -o PubkeyAuthentication=no -o PreferredAuthentications=password root@node.example.invalid
```

Вход по provider key:

```bash
ssh -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$ttcp_secret_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes -i /secure/provider-key root@node.example.invalid
```

Если provider выдаёт sudo-пользователя, входите как он и выполняйте bootstrap в root-сессии через утверждённый provider workflow. Пароль вводится только в SSH prompt, не в argv, inventory или Admin Web. Оставьте консоль/восстановительный вход открытыми до проверки management account.

## Безопасный bootstrap отдельного VPS

Через проверенный SSH/консоль перенесите **только проверенные tracked исходники** в root-controlled `/root/ttcp-bootstrap`. Нельзя копировать весь рабочий каталог с `.env`, `.tools`, private keys, dumps и profiles. Сохраните layout: четыре файла `scripts/bootstrap-node.sh`, `bootstrap_node.py`, `node_credential.py`, `node_lifecycle.py` и `automation/ansible/roles/trusttunnel_endpoint/templates/`. Подробности и допустимое состояние узла: [node bootstrap](../node-bootstrap.md).

Для выбранного **committed** release создайте архив только этих исходников из Bash (WSL/Linux):

```bash
git -C "$ttcp_repo" archive --format=tar --output="$ttcp_secret_dir/bootstrap-source.tar" HEAD scripts/bootstrap-node.sh scripts/bootstrap_node.py scripts/node_credential.py scripts/node_lifecycle.py automation/ansible/roles/trusttunnel_endpoint/templates
scp -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$ttcp_secret_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes -i /secure/provider-key "$ttcp_secret_dir/bootstrap-source.tar" root@node.example.invalid:/root/ttcp-bootstrap-source.tar
```

`git archive HEAD` не включает uncommitted changes; подтверждённая ревизия исходников должна совпадать с release образов Control Plane. Для password transport используйте password options из предыдущего шага. В root-сессии **одноразового VPS**, перед bootstrap:

```bash
install -d -m 700 /root/ttcp-bootstrap
tar -xf /root/ttcp-bootstrap-source.tar -C /root/ttcp-bootstrap
```

Не распаковывайте непроверенный сторонний архив; не перезаписывайте существующий foreign checkout на рабочем узле.

Через то же pinned соединение перенесите management **public** key (Bash, вариант provider key):

```bash
scp -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$ttcp_secret_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes -i /secure/provider-key "$ttcp_secret_dir/management-bootstrap.pub" root@node.example.invalid:/root/ttcp-management.pub
```

При password bootstrap уберите provider `-i` и используйте те же password options, что при SSH. В **root-сессии VPS**:

```bash
sh /root/ttcp-bootstrap/scripts/bootstrap-node.sh < /root/ttcp-management.pub
```

Bootstrap создаёт `ttcp`, ограниченные sudo helpers и loopback node_exporter; VPN ещё не установлен. Не запускайте `automation/ansible/bootstrap.sh`: это другой, controller/CLI workflow. Повтор с тем же ключом поддержан; чужие accounts/paths, symlinks или другой ключ отклоняются. Не удаляйте ownership marker для обхода отказа. При ошибке устраните причину, повторите и проверьте **новое соединение** из WSL/Linux:

```bash
ssh -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$ttcp_secret_dir/known_hosts" -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o BatchMode=yes -i "$ttcp_secret_dir/management-key" ttcp@node.example.invalid 'sudo -n /usr/local/sbin/ttcp-node-status'
```

До deploy допустим `LoadState=not-found`. По аналогичному pinned соединению `sudo -n /bin/true` и status helper с аргументом должны отказать. На консоли VPS проверьте `systemctl is-active prometheus-node-exporter` и `ss -lntp`: 9100 только loopback. Повторите bootstrap с тем же ключом на одноразовом VPS и повторите проверки. Доступ из WSL не доказывает SSH из контейнера: окончательное доказательство — pre-flight worker.

## DNS, firewall и первая установка VPN

| Направление | Требование |
| --- | --- |
| Станция → registry/package repositories | Исходящий HTTPS/DNS для build/pull. |
| Worker/collector → VPS | SSH на зарегистрированном порту; разрешить реальный внешний адрес станции/NAT в provider и host firewall. Dynamic IP может потребовать обновления правила. |
| VPS → repositories/GitHub/ACME | DNS и HTTPS; работающие apt, часы/NTP. |
| VPN client → VPS | TCP 443 и UDP 443 для workload; проверить обе сети/IPv4/IPv6, если используются. |
| ACME → VPS | TCP 80 при HTTP challenge, DNS домена указывает на VPS. После deploy порт 80 нужен также для renewal. |
| Интернет → Control Plane | В базовой локальной среде доступа нет; HTTPS ingress для staging/production готовит оператор. |
| SQL/Redis/metrics/exporter | Не открывать наружу; exporter читается через pinned SSH на loopback VPS. |

В DNS VPN-домена A/AAAA должны соответствовать узлу. Pre-flight требует единственный адрес в семействе зарегистрированного `Public IP`; другое семейство не проверяется против интерфейсов узла. Не оставляйте неработающий AAAA. Проверка портов pre-flight — локальное bind/socket observation на VPS, **не проверка provider firewall/NAT извне**. Сам bootstrap/deploy порты не открывает.

В Admin Web → **Servers → Add managed server** заполните поля по [Admin guide](admin-web-guide.md): management user `ttcp`, verified host public key без комментария, matching private key. Root/provider пароль туда не вводится. Нажмите **Create server**, затем **Run pre-flight**. В **Jobs** откройте задачу: все 11 checks должны быть `pass` либо допустимо `skipped`, результат **Ready**. `succeeded` с **Not ready** не допускает установку. Fresh result действует 15 минут; после изменения target/key повторите проверку.

Для deploy выберите явный upstream release `major.minor.patch`, без `v`, `latest` или prerelease. Руководство не фиксирует непроверенный номер: согласуйте доступную версию по [официальному TrustTunnel](https://github.com/TrustTunnel/TrustTunnel). При HTTP ACME укажите **Certificate contact email**. Нажмите **Deploy**, дождитесь job success, installed version и проверок **Monitoring**. Без HTTP ACME оператор заранее обеспечивает сертификат `/etc/letsencrypt/live/<VPN-domain>/fullchain.pem` и `privkey.pem`; снятие checkbox не создаёт self-signed TLS.

Workload устанавливается в `/opt/trusttunnel`; credentials затем добавляет worker. Уже занятые 443 после deploy ожидаемы: pre-flight предназначен для **первой установки**, не для оценки здоровья установленного VPN. Используйте **Check status** и **Monitoring**. Сертификат/renewal Data Plane реализован в lifecycle helper; Control Plane ACME provision/renewal остаётся операторским этапом (**DEPLOY-04**). Проверяйте timer/hooks вживую, не считайте сертификат бессрочным.

## Сквозная проверка: от пользователя до трафика

1. Создайте тестового VPN-пользователя, например с `device_limit=1`, задайте срок и доступ к установленному серверу. Роль VPN-пользователя отличается от административной.
2. **Invitations**: выберите пользователя, **Create invitation**, скопируйте одноразовую ссылку приватно. Проверьте Client host/scheme; localhost-ссылку открывайте на станции.
3. В отдельном браузерном профиле откройте приглашение, добавьте устройство, выберите сервер, **Настроить подключение**. Дождитесь **Готово к подключению**. Обновите страницу: сессия должна восстановиться без повторного приглашения.
4. **Получить конфигурацию** → deeplink, QR или TOML по [Client guide](client-web-guide.md). Импортируйте в реальное приложение. Не сохраняйте QR/TOML в acceptance evidence.
5. До/после подключения сравните внешний адрес через выбранный оператором доверенный сервис, откройте обычный сайт и проверьте DNS/ожидаемый IPv4/IPv6 policy. Запишите только PASS/FAIL и обезличенное наблюдение. Job success без реального трафика не является приёмкой.
6. Проверьте лимит устройства и, при наличии второго сервера, отдельную конфигурацию того же устройства без расхода ещё одной квоты. Для одного VPS этот пункт помечается не выполненным, а не PASS.
7. Отзовите тестовое устройство; дождитесь revoke job и проверьте отказ **нового** VPN-подключения. Проверьте expired/disabled user. Зафиксируйте задержку применения; logout портала сам по себе не отзывает VPN.
8. На тестовом профиле остановите Control Plane командой `dc stop` и проверьте сохранение уже настроенного действующего VPN. Возобновите `dc start --wait`, проверьте задачи/мониторинг. Отзыв проверяйте отдельным профилем от профиля продолжения трафика.

## Здоровье, логи и восстановление

Общие команды через текущий `dc`:

```text
dc ps
dc exec nginx nginx -t
dc exec api python -m apps.shared.healthcheck api
dc exec worker python -m apps.shared.healthcheck worker
dc exec metrics-collector python -m apps.shared.healthcheck monitoring
dc logs --tail=100 api worker metrics-collector nginx
```

Healthcheck worker проверяет зависимости отдельным процессом, а не прогресс задач. Проверяйте также Jobs, возраст метрик и дату последнего наблюдения. `/api/healthz` и `/api/readyz` доступны внутри API, но **не входят в web edge allowlist**: через Admin/Client NGINX ожидается 404. Проверяйте readiness командой `dc exec api python -m apps.shared.healthcheck api`, а HTTP входную точку dev — PowerShell `Invoke-WebRequest http://127.0.0.1:8080/healthz` или Bash `curl --fail http://127.0.0.1:8080/healthz`. Grafana локально: `http://127.0.0.1:3000/`, login из защищённого env.

Не прикладывайте полные env, raw SQL exceptions, `docker inspect`, cookies, приглашения и generated configuration к обращению в поддержку. Сначала просмотрите ограниченные логи и обезличьте запись. [Incident runbook](../runbooks/incident-recovery.md) описывает восстановление без удаления БД.

| Симптом | Действие оператора |
| --- | --- |
| HTML есть, вход сразу пропадает | Проверить Secure cookie/TLS/точный origin, clock и следующую TOTP; не отключать защиту. |
| `queued` не заканчивается | Проверить worker, миграции, PostgreSQL/Redis; dispatch восстанавливается из БД. Не создавать jobs вручную в Redis. |
| SSH/auth/host pin failure | Console/provider fingerprint, matching key, `ttcp`, порт, firewall, actual NAT IP. Не обходить pin. |
| Pre-flight `Not ready` | Открыть checks: DNS, свободные 80/443, ОС, диск, RAM, NTP; устранить конкретный отказ. |
| ACME/deploy failure | Проверить DNS/public port 80 и egress VPS; читать safe job failure, проверить реальное node state до повтора. |
| `unknown`, `rollback_failed`, потеря worker | Сверить узел через консоль/status/monitoring и [lifecycle](../server-lifecycle.md); timeout не означает rollback. |
| Нет конфигурации | Доступ/срок/квота, active credential, worker, актуальные bootstrap helpers и official generator в `/opt/trusttunnel`. |
| Monitoring `Unavailable` | Проверить collector, pin/exporter/VM и freshness; отсутствие метрик не доказывает остановку VPN. |
| Backup disabled/unknown | Подготовить [backup config](../backups.md); unknown требует CLI verify исходного UUID/namespace. |

## Остановка, обновление и безопасное удаление

`dc stop` / `dc start --wait` останавливают/возобновляют контейнеры; `dc restart api worker metrics-collector` перезапускает процессы, но **не применяет** изменённый env. После env/code changes используйте `dc up -d --build --wait --wait-timeout 180`. Миграционные upgrades делайте по [upgrade runbook](../runbooks/upgrade-migrations.md), с backup и завершением/проверкой jobs.

`dc down` удаляет контейнеры/сети проекта, сохраняя данные. `dc down --volumes` уничтожает PostgreSQL, метрики и Grafana **этого проекта**: только для согласованного одноразового стенда после backup или явного решения отказаться от данных. Перед этим `dc ps` и журнал должны подтверждать `ttcp-first`, а не production. Не используйте глобальный Docker prune для teardown.

Удаление Control Plane не удаляет VPS/VPN. Для безопасного teardown одноразового VPS сначала отзовите credentials и дождитесь jobs, затем **Uninstall TrustTunnel** и проверьте workload removal. Management user/helpers, exporter, packages, firewall и сертификаты остаются; provider delete VPS — отдельное ручное действие после подтверждения правильного ресурса. **Disable server** не останавливает VPN. Архивы/ключи удаляются по отдельной retention policy, а не автоматически.

Для staging/production подключайте [backup overlay](../../infra/compose/compose.backup.yaml) с отдельными reader credentials/S3/keyring вне checkout и выполняйте [backup/restore drill](../runbooks/backup-restore.md). UI не настраивает storage и не восстанавливает БД; automatic schedule/retention service, WAL/PITR отсутствуют. Без проверенного off-host restore и независимого escrow production не подготовлен (**DEPLOY-05**).

## Конкретные DOC/DEPLOY gaps

| ID | Факт текущей реализации | Что нужно до приёмки |
| --- | --- | --- |
| DEPLOY-01 | HTTP Compose + Secure cookies; нет готового local CA/certificate workflow. | Подготовить доверенный TLS через существующий overlay; проверить login/refresh в выбранном браузере. HTTP failure не лечится документацией. |
| DEPLOY-02 | Loopback/localhost изолирован от мобильных устройств; HTTPS overlay публикует все interfaces. | Отдельно подготовить DNS/trust/маршрут/firewall для mobile acceptance; не публиковать станцию случайно. |
| DEPLOY-03 | Management private key должен быть unencrypted; UI не имеет admin/viewer CRUD/MFA reset. | Защищённое хранение и escrow; viewer enrollment/recovery — управляемая DBA-процедура, готового operator UI нет. |
| DEPLOY-04 | Control Plane TLS files монтируются, ACME issuer/renewal automation для них не поставляется. | Operator certificate issue/renewal + reload/recreate NGINX и проверка срока; Data Plane ACME проверяется отдельно. |
| DEPLOY-05 | Backup opt-in, runtime DB role provisioning, расписание/retention/escrow не автоматизированы. | DBA/storage preparation и реальный off-host restore; base dev owner fallback не переносить в production. |
| DEPLOY-06 | Windows Docker build, VPS SSH/systemd/ACME, native import/VPN не доказаны статическим config. | Заполнить human checklist и staging acceptance; точные границы текущей проверки в протоколе. |
| DOC-01 | Baseline предпочитает отдельный CP VPS; Windows CP задан как первый тестовый сценарий. | Это временная staging-топология TTCP-022A, не изменение production-архитектуры. |

Долговременные решения перечислены в [ADR registry](../adr/README.md); многие ADR имеют статус Proposed и требуют принятия владельцем. Это руководство не меняет их статус и не устраняет gaps изменением функциональности.
