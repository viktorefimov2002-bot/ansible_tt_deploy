# TrustTunnel Server Ansible

Ansible-проект для установки TrustTunnel Endpoint на Linux-сервер. Плейбук устанавливает бинарный релиз, создает конфиги, настраивает TLS, systemd-сервис и генерирует клиентские конфиги.

## Что потребуется

- Ansible на управляющей машине.
- SSH-доступ к серверу с sudo.
- Поддерживаемая ОС сервера: Linux x86_64 или aarch64.
- Для Let's Encrypt в standalone-режиме: домен должен указывать на IP сервера, входящий порт 80 должен быть доступен извне, а на самом сервере порт 80 не должен быть занят другим процессом. Certbot сам поднимает временный HTTP-сервер для проверки, но firewall и внешний security group/NAT должны пропускать трафик на 80/tcp.

## Быстрый старт

Скопируйте пример inventory:

```bash
cp inventory.example.ini inventory.ini
```

Укажите сервер в `inventory.ini`, затем запустите:

```bash
ansible-playbook -i inventory.ini site.yml
```

Плейбук спросит домен, публичный адрес, одного VPN-пользователя, режим сертификата и email для Let's Encrypt.

`trusttunnel_domain` - это домен TrustTunnel endpoint для TLS/SNI и выпуска сертификата, например `vpn.example.com`. В режиме `letsencrypt` именно на него выпускается сертификат, поэтому DNS A/AAAA-запись должна указывать на сервер.

`trusttunnel_public_address` - это адрес, который попадет в клиентские конфиги и к которому будут подключаться VPN-клиенты. TrustTunnel принимает `ip`, `ip:port`, `domain` или `domain:port`, например `vpn.example.com` или `vpn.example.com:443`. Если порт не указан, TrustTunnel берёт порт из `trusttunnel_listen_address`, по умолчанию это `443`. Явный `:443` не обязателен, но делает клиентский конфиг однозначным; если используется внешний NAT/LB или нестандартный порт, укажите именно внешний адрес для клиентов.

## Запуск через vars-файл

Для повторяемой раскатки лучше использовать vars-файл:

```bash
cp vars.example.yml vars.yml
ansible-playbook -i inventory.ini site.yml -e @vars.yml
```

В `vars.yml` можно задать несколько пользователей:

```yaml
trusttunnel_clients:
  - username: user1
    password: change-me
  - username: user2
    password: change-me-too
```

Переменные `trusttunnel_client_username` и `trusttunnel_client_password` оставлены для совместимости с интерактивным режимом и используются, когда `trusttunnel_clients` пустой.

### Миграция существующих VPN-клиентов

Если нужно перенести клиентов со старого TrustTunnel-сервера, можно использовать готовый `credentials.toml` вместо создания нового пользователя.

Положите файл, например, сюда:

```bash
cp credentials.toml roles/trusttunnel_endpoint/files/credentials.toml
```

И задайте:

```yaml
trusttunnel_existing_credentials_file: credentials.toml
trusttunnel_clients: []
```

Роль скопирует этот файл на новый сервер как `/opt/trusttunnel/credentials.toml`, извлечет из него `username`-значения и сгенерирует клиентские конфиги для найденных пользователей. Пароли клиентов при этом сохраняются из исходного `credentials.toml`.

## Bootstrap

Есть тонкий helper-скрипт:

```bash
./bootstrap.sh
```

Он спрашивает базовые параметры, создает `inventory.ini` и `vars.yml`, выставляет `600` на оба файла, затем запускает `ansible-playbook`. Основная логика установки остается в Ansible-роли.

### Что подготовить перед запуском bootstrap

Перед запуском желательно заранее понимать или подготовить:

- IP или DNS-имя удаленного сервера, на который будет установлен TrustTunnel Endpoint.
- Root SSH-доступ к удаленному серверу. Bootstrap всегда настраивает Ansible-подключение как `root`.
- Способ SSH-доступа: пароль или ключ. При доступе по паролю на управляющей машине нужен `sshpass`; bootstrap может временно установить его через `apt-get`.
- Домен для TrustTunnel, например `vpn.example.com`. В режиме Let's Encrypt этот домен должен указывать на сервер, потому что на него выпускается TLS-сертификат.
- Публичный адрес для клиентов будет выбран автоматически как `<домен>:443`, например `vpn.example.com:443`. Именно это значение попадет в клиентские конфиги.
- Режим сертификата: `letsencrypt`, `selfsigned` или `existing`.
- Если используется Let's Encrypt: email для уведомлений Certbot.
- Если переносите клиентов со старого сервера: файл `credentials.toml`, заранее положенный в `roles/trusttunnel_endpoint/files/`.
- Имя и пароль VPN-клиента, если не используете готовый `credentials.toml`.
- Решение, нужно ли открывать локальный firewall средствами Ansible. По умолчанию `false`; это нормально, если firewall управляется у облачного провайдера или порты уже открыты.
- Пароль Ansible Vault, если хотите хранить секреты в зашифрованном `vault.yml`.

### Пример интерактивного запуска bootstrap

Ниже пример первичной установки на Ubuntu/Debian control-machine, где Ansible уже установлен, SSH-доступ к серверу идет по паролю, сертификат выпускается через Let's Encrypt, а создается один новый VPN-клиент:

```text
$ ./bootstrap.sh
Is ansible/ansible-playbook already installed on this machine? yes/no [yes]: yes
Remote server IP or DNS name for SSH/Ansible: 144.31.109.13
Adding 144.31.109.13 to known_hosts...
SSH authentication method for Ansible: password, key [password]: password
Remote root SSH password for Ansible:
Testing SSH password for root@144.31.109.13...
SSH password accepted.
TrustTunnel domain for TLS certificate/SNI, for example vpn.example.com: vpn.example.com
Public address for client configs will be: vpn.example.com:443
Certificate mode: letsencrypt, selfsigned, existing [letsencrypt]: letsencrypt
Let's Encrypt email: admin@example.com
Use an existing TrustTunnel credentials.toml to preserve VPN clients? yes/no [no]: no
VPN client username to create in credentials.toml [user1]: user1
VPN client password to create in credentials.toml:
Open local firewall ports with Ansible? true/false [false]: false
Encrypt generated secrets with Ansible Vault? yes/no [yes]: yes
Ansible Vault password for vault.yml:
```

После этого bootstrap:

- добавит сервер в `~/.ssh/known_hosts`, если его там еще нет;
- проверит root SSH-доступ до запуска playbook;
- создаст `inventory.ini` с адресом сервера и `ansible_user=root`;
- создаст `vars.yml` с несекретными параметрами TrustTunnel;
- при выборе Vault создаст зашифрованный `vault.yml` с SSH/VPN/sudo-паролями;
- запустит `ansible-playbook -i inventory.ini site.yml ...`;
- создаст `deploy.sh` и `uninstall.sh` для повторного запуска;
- после успешной раскатки создаст `deployment_info_<host>.md` с памяткой по клиентским конфигам и удалению.

Если Ansible не установлен, ответьте `no` на первый вопрос. Bootstrap спросит, можно ли временно установить Ansible для раскатки и удалить после завершения:

```text
Is ansible/ansible-playbook already installed on this machine? yes/no [yes]: no
Can bootstrap install Ansible temporarily for this deployment and remove it afterwards? yes/no [yes]: yes
```

Если Ansible уже был установлен до запуска bootstrap, скрипт использует его и не удаляет после завершения. Автоматическое удаление выполняется только для Ansible, который установил сам bootstrap в текущем запуске.

Если нужен перенос существующих VPN-клиентов, сначала положите файл:

```bash
cp credentials.toml roles/trusttunnel_endpoint/files/credentials.toml
```

А в bootstrap ответьте:

```text
Use an existing TrustTunnel credentials.toml to preserve VPN clients? yes/no [no]: yes
Credentials file name inside role files directory [credentials.toml]: credentials.toml
```

В этом режиме bootstrap не спрашивает имя и пароль нового VPN-клиента: клиенты берутся из существующего `credentials.toml`.

### Добавление новых VPN-клиентов

Для добавления пользователей после первичной установки используйте локальный helper `add_vpn_clients.sh`. Он читает файл `roles/trusttunnel_endpoint/files/new_clients.toml`, добавляет отсутствующих пользователей в `roles/trusttunnel_endpoint/files/credentials.toml`, а затем нужно запустить обычный deploy.

Формат `new_clients.toml` - обычный TOML с повторяющимися блоками `[[client]]`. В каждом блоке обязательны два поля:

- `username` - имя VPN-клиента. Допустимы латинские буквы, цифры, `_`, `.`, `@` и `-`.
- `password` - пароль этого VPN-клиента.

Пример файла `roles/trusttunnel_endpoint/files/new_clients.toml`:

```toml
[[client]]
username = "alice"
password = "alice-password"

[[client]]
username = "bob"
password = "bob-password"
```

Запуск:

```bash
./add_vpn_clients.sh
```

После добавления пользователей helper спросит, запускать ли `deploy.sh` сразу. Если ответить `yes`, он запустит deploy с `trusttunnel_existing_credentials_file=credentials.toml`, синхронизирует `/opt/trusttunnel/credentials.toml` на сервере, заново сгенерирует клиентские конфиги и заберет их локально в `client_configs`.

Если `roles/trusttunnel_endpoint/files/credentials.toml` еще не существует, helper остановится и предупредит об этом. Это защита от случайной потери старых пользователей: перед добавлением клиентов скопируйте текущий серверный файл:

```bash
scp root@<server>:/opt/trusttunnel/credentials.toml roles/trusttunnel_endpoint/files/credentials.toml
```

Если вы намеренно создаете новый `credentials.toml` с нуля, это можно подтвердить интерактивно или задать переменную окружения:

```bash
TRUSTTUNNEL_INIT_CREDENTIALS=yes ./add_vpn_clients.sh
```

Если исходный `credentials.toml` называется иначе или лежит в другом месте, можно явно передать оба пути:

```bash
./add_vpn_clients.sh roles/trusttunnel_endpoint/files/new_clients.toml roles/trusttunnel_endpoint/files/credentials.toml
```

Автоматический deploy поддерживается для `credentials.toml`, лежащего в `roles/trusttunnel_endpoint/files/`, потому что Ansible role берет existing credentials только из этой директории. Повторный запуск безопасен: пользователи с уже существующим `username` будут пропущены. Файлы `credentials.toml` и `new_clients.toml` добавлены в `.gitignore`, потому что содержат пароли клиентов.

### Удаление VPN-клиентов

Для удаления пользователей из `credentials.toml` используйте `remove_vpn_clients.sh`. Подготовьте файл `roles/trusttunnel_endpoint/files/remove_clients.txt`:

```text
alice
bob
```

Пустые строки и комментарии через `#` игнорируются. Запуск:

```bash
./remove_vpn_clients.sh
```

После изменения файла helper спросит, запускать ли `deploy.sh` сразу. Если ответить `yes`, он синхронизирует обновленный `credentials.toml` на сервер, заново сгенерирует конфиги оставшихся клиентов и включит `trusttunnel_prune_client_configs=true`, чтобы удалить stale `client_<username>.*` для исключенных пользователей на сервере и в локальном `client_configs`.

Bootstrap поддерживает SSH по паролю и по ключу. Для SSH по паролю Ansible использует `sshpass`; если его нет, helper предложит временно установить `sshpass` через `apt-get` и удалить после завершения. Вывод `apt-get` для `sshpass` пишется в `/tmp/trusttunnel-sshpass-install.log` или `/tmp/trusttunnel-sshpass-remove.log`, а в терминале показывается только краткий статус или ошибка. Аналогично helper может временно установить Ansible, если его нет на управляющей машине.

Если в ответах есть секреты, например SSH-пароль, sudo-пароль или пароль нового VPN-клиента, bootstrap предложит зашифровать их через Ansible Vault. При согласии:

- обычные параметры сохраняются в `vars.yml`;
- секреты сохраняются в зашифрованный `vault.yml`;
- временный файл с паролем Vault создается в `/tmp`, используется только для текущего запуска и удаляется при выходе.

Пароль Vault нужно сохранить у себя: без него нельзя будет повторно использовать или расшифровать `vault.yml`. Если отказаться от Vault, секреты будут записаны в `vars.yml` в открытом виде, но с правами `600`.

После успешной раскатки helper создает:

- `deploy.sh` - повторный запуск установки/обновления с автоматическим подключением `vault.yml`, если он есть;
- `uninstall.sh` - удаление с автоматическим подключением `vault.yml`, если он есть;
- `deployment_info_<host>.md` - локальная памятка с путями клиентских конфигов, проверкой сервиса, firewall-пояснением и командами удаления.

`deploy.sh` и `uninstall.sh` также проверяют, нужен ли `sshpass` для SSH по паролю. Если `sshpass` отсутствует, helper временно установит его через `apt-get`, запустит playbook и удалит обратно.

## Удаление и повторное тестирование

Для быстрого демонтажа есть отдельный playbook:

```bash
./uninstall.sh
```

Если запускать вручную и у вас есть `vault.yml`, добавьте vault-файл и ввод пароля Vault:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e @vars.yml -e @vault.yml --ask-vault-pass
```

По умолчанию он:

- останавливает и отключает `trusttunnel.service`;
- удаляет `/etc/systemd/system/trusttunnel.service`;
- удаляет `/opt/trusttunnel`;
- удаляет deploy hook Certbot для перезагрузки TrustTunnel;
- удаляет cron fallback, если он создавался;
- удаляет локально скачанные клиентские конфиги из `client_configs`.

Let's Encrypt-сертификат и firewall-правила по умолчанию не удаляются, потому что они могут использоваться другими сервисами или быть управляемыми у провайдера. Для полного тестового сноса можно явно включить:

```bash
./uninstall.sh \
  -e trusttunnel_remove_letsencrypt_cert=true \
  -e trusttunnel_close_firewall=true
```

Можно запустить удаление и через основной playbook:

```bash
./deploy.sh -e trusttunnel_state=absent
```

## Важные переменные

- `trusttunnel_install_version` - версия endpoint-релиза. По умолчанию `auto`: используется версия, встроенная в скачанный с ветки `master` `install.sh`. Для закрепления конкретного релиза укажите, например, `1.0.33`.
- `trusttunnel_install_dir` - директория установки, по умолчанию `/opt/trusttunnel`.
- `trusttunnel_listen_address` - адрес прослушивания, по умолчанию `0.0.0.0:443`.
- `trusttunnel_cert_mode` - `letsencrypt`, `selfsigned` или `existing`.
- `trusttunnel_existing_cert_chain_path` и `trusttunnel_existing_private_key_path` - пути к сертификату и ключу при `existing`.
  Если при удалении не удалять Let's Encrypt-сертификат, то повторная установка с тем же `trusttunnel_domain` и `trusttunnel_cert_mode: letsencrypt` переиспользует сертификат из `/etc/letsencrypt/live/<domain>/`.
  Режим `existing` нужен, когда вы хотите явно указать уже лежащие на удалённой машине `fullchain.pem` и `privkey.pem`, например `/etc/letsencrypt/live/vpn.example.com/fullchain.pem`.
- `trusttunnel_existing_credentials_file` - имя файла из `roles/trusttunnel_endpoint/files/`, который нужно использовать как готовый `credentials.toml` для миграции клиентов.
- `trusttunnel_open_firewall` - открыть 80/tcp, 443/tcp и 443/udp до выпуска сертификата, по умолчанию выключено.
- `trusttunnel_firewall_backend` - `auto`, `ufw`, `firewalld` или `none`. При `auto` роль сначала ищет `ufw` как типичный backend Ubuntu, потом `firewalld`, иначе использует `none`.
- `trusttunnel_fetch_client_configs` - забрать клиентские конфиги на управляющую машину, по умолчанию включено.
- `trusttunnel_client_config_local_dir` - локальная директория для скачанных клиентских конфигов, по умолчанию `client_configs` рядом с playbook.
- `trusttunnel_prune_client_configs` - удалить stale клиентские конфиги, которых больше нет в текущем `credentials.toml` или `trusttunnel_clients`, по умолчанию выключено.
- `trusttunnel_generate_client_random_prefix` - сгенерировать TLS random prefix и добавить allow-rule в `rules.toml`.
- `trusttunnel_client_config_format` - `toml` или `deeplink`.
- `trusttunnel_remove_install_dir` - удалить директорию установки при uninstall, по умолчанию включено.
- `trusttunnel_remove_letsencrypt_cert` - удалить сертификат Certbot для домена при uninstall, по умолчанию выключено.
- `trusttunnel_close_firewall` - убрать локальные firewall-правила при uninstall, по умолчанию выключено.

## Результат на сервере

- Endpoint: `/opt/trusttunnel/trusttunnel_endpoint`
- Основной конфиг: `/opt/trusttunnel/vpn.toml`
- TLS hosts: `/opt/trusttunnel/hosts.toml`
- Учетные данные: `/opt/trusttunnel/credentials.toml`
- Правила: `/opt/trusttunnel/rules.toml`
- Клиентские конфиги: `/opt/trusttunnel/client_<username>.<format>`
- Сервис: `trusttunnel.service`

Если включен `trusttunnel_fetch_client_configs`, клиентские конфиги также появятся локально в `client_configs`.

Проверка:

```bash
sudo systemctl status trusttunnel
sudo journalctl -u trusttunnel -f
```
