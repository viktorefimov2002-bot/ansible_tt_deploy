# TrustTunnel Server Ansible

Ansible-проект для быстрой установки и обслуживания TrustTunnel Endpoint на Linux-сервере.

Проект умеет:

- устанавливать TrustTunnel Endpoint;
- настраивать TLS через Let's Encrypt, self-signed или существующий сертификат;
- создавать `systemd`-сервис;
- генерировать клиентские VPN-конфиги;
- переносить существующих VPN-пользователей через `credentials.toml`;
- добавлять и удалять VPN-клиентов;
- удалять установленный TrustTunnel с сервера;
- запускаться как через новый helper `ttctl`, так и через старый интерактивный `bootstrap.sh`.

## Самый короткий сценарий

```bash
chmod +x ttctl
cp deploy.example.yml deploy.yml
nano deploy.yml
./ttctl init --config deploy.yml
./ttctl deploy
```

После установки проверить сервис:

```bash
./ttctl status
```

Если новый `ttctl`-workflow не подходит или не заводится в твоей среде, можно использовать старый интерактивный сценарий:

```bash
./bootstrap.sh
```

## Что подготовить заранее

Перед раскаткой желательно иметь:

1. Linux-сервер x86_64 или aarch64.
2. SSH-доступ к серверу.
3. Пользователя с root-доступом или sudo-доступом.
4. Домен для VPN, например `vpn.example.com`.
5. DNS A/AAAA-запись домена на IP сервера.
6. Открытые входящие порты на стороне облачного провайдера/security group/NAT:
   - `80/tcp` для Let's Encrypt HTTP challenge;
   - `443/tcp` для TrustTunnel;
   - `443/udp` для TrustTunnel, если используется UDP.
7. Ansible на управляющей машине.
8. Python с YAML-библиотекой для `ttctl init`:

```bash
sudo apt-get install -y python3-yaml
```

или:

```bash
python3 -m pip install PyYAML
```

Если YAML-библиотеки нет, `ttctl` остановится с понятной ошибкой. В таком случае можно либо установить зависимость, либо использовать `./bootstrap.sh`.

## Новый workflow через `ttctl`

`ttctl` — это единая обертка над существующими playbook'ами и helper-скриптами.

Доступные команды:

```bash
./ttctl init --config deploy.yml
./ttctl deploy
./ttctl status
./ttctl add-client
./ttctl remove-client
./ttctl import-credentials ./credentials.toml
./ttctl uninstall
```

Важно: `ttctl` не заменяет Ansible-роль. Он только упрощает подготовку локальных файлов и запуск уже существующих playbook'ов.

## Настройка `deploy.yml`

Скопируй пример:

```bash
cp deploy.example.yml deploy.yml
```

Файл `deploy.yml` добавлен в `.gitignore`, потому что в нем могут быть реальные адреса, пользователи и секреты.

Ниже пример минимального файла для нового сервера:

```yaml
---
server:
  host: 203.0.113.10
  ssh_user: root
  become: false

trusttunnel:
  domain: vpn.example.com
  public_address: vpn.example.com:443
  cert_mode: letsencrypt
  acme_email: admin@example.com

  open_firewall: false
  firewall_backend: auto

  clients:
    - username: user1
      password: change-me
```

### Какие поля обязательно заменить

#### `server.host`

IP или DNS-имя сервера, на который Ansible будет подключаться по SSH.

Пример:

```yaml
server:
  host: 144.31.109.13
```

или:

```yaml
server:
  host: my-vps.example.com
```

#### `server.ssh_user`

SSH-пользователь для подключения.

Если на сервере разрешен root-login:

```yaml
server:
  ssh_user: root
  become: false
```

Если подключение идет под обычным пользователем, например `ubuntu`, но у него есть sudo:

```yaml
server:
  ssh_user: ubuntu
  become: true
```

`become: true` означает, что Ansible будет выполнять задачи через sudo.

#### `trusttunnel.domain`

Домен, на который будет выпущен сертификат и который будет использоваться для TLS/SNI.

```yaml
trusttunnel:
  domain: vpn.example.com
```

Для режима `letsencrypt` этот домен должен указывать на IP сервера.

#### `trusttunnel.public_address`

Адрес, который попадет в клиентские VPN-конфиги.

Обычно он совпадает с доменом и портом:

```yaml
trusttunnel:
  public_address: vpn.example.com:443
```

Если используется внешний load balancer, NAT или нестандартный внешний порт, здесь нужно указать именно тот адрес, к которому будут подключаться клиенты.

#### `trusttunnel.cert_mode`

Режим сертификата.

```yaml
trusttunnel:
  cert_mode: letsencrypt
```

Доступные варианты:

- `letsencrypt` — выпустить настоящий сертификат через Certbot;
- `selfsigned` — создать самоподписанный сертификат;
- `existing` — использовать уже существующие сертификат и ключ на сервере.

Для обычной установки на публичный сервер чаще всего нужен `letsencrypt`.

#### `trusttunnel.acme_email`

Email для Let's Encrypt:

```yaml
trusttunnel:
  acme_email: admin@example.com
```

Нужен только при:

```yaml
cert_mode: letsencrypt
```

#### `trusttunnel.clients`

Список VPN-клиентов, которых нужно создать.

Один пользователь:

```yaml
trusttunnel:
  clients:
    - username: user1
      password: strong-password-here
```

Несколько пользователей:

```yaml
trusttunnel:
  clients:
    - username: alice
      password: alice-password
    - username: bob
      password: bob-password
```

Имена пользователей могут содержать латинские буквы, цифры, `_`, `.`, `@` и `-`.

### Firewall-поля

#### `trusttunnel.open_firewall`

```yaml
trusttunnel:
  open_firewall: false
```

Если `false`, Ansible не будет менять локальный firewall на сервере.

Это нормально, если:

- firewall выключен;
- правила уже открыты;
- firewall управляется у облачного провайдера;
- порты открываются через security group/NAT.

Если хочешь, чтобы Ansible попробовал открыть локальные правила через `ufw` или `firewalld`:

```yaml
trusttunnel:
  open_firewall: true
  firewall_backend: auto
```

Открываются:

- `80/tcp`;
- `443/tcp`;
- `443/udp`.

#### `trusttunnel.firewall_backend`

```yaml
trusttunnel:
  firewall_backend: auto
```

Варианты:

- `auto` — сначала искать `ufw`, потом `firewalld`;
- `ufw`;
- `firewalld`;
- `none`.

## Первый деплой нового сервера

1. Переключись на ветку с P1-изменениями:

```bash
git fetch origin
git checkout p1-usability-safe-cli
```

2. Сделай helper исполняемым:

```bash
chmod +x ttctl
```

Если executable-bit не сохранился, можно запускать так:

```bash
bash ./ttctl --help
```

3. Создай локальный конфиг:

```bash
cp deploy.example.yml deploy.yml
```

4. Отредактируй `deploy.yml`:

```bash
nano deploy.yml
```

Минимально проверь поля:

- `server.host`;
- `server.ssh_user`;
- `server.become`;
- `trusttunnel.domain`;
- `trusttunnel.public_address`;
- `trusttunnel.cert_mode`;
- `trusttunnel.acme_email`;
- `trusttunnel.clients`.

5. Сгенерируй Ansible runtime-файлы:

```bash
./ttctl init --config deploy.yml
```

Команда создаст:

- `inventory.ini`;
- `vars.yml`;
- при необходимости скопирует `credentials.toml` в `roles/trusttunnel_endpoint/files/`.

6. Запусти деплой:

```bash
./ttctl deploy
```

7. Проверь сервис:

```bash
./ttctl status
```

8. Клиентские конфиги будут скачаны локально в директорию:

```text
client_configs/
```

## Подключение не под root, а под sudo-пользователем

Например, сервер доступен как `ubuntu@203.0.113.10`:

```yaml
server:
  host: 203.0.113.10
  ssh_user: ubuntu
  become: true
```

После `ttctl init` в `inventory.ini` будет сгенерировано подключение с:

```text
ansible_user=ubuntu ansible_become=true
```

Если sudo требует пароль, пока лучше использовать старый `bootstrap.sh`, потому что он уже умеет работать с Ansible Vault для секретов. В `ttctl` прямой Vault-flow пока не реализован.

## Миграция существующих VPN-клиентов

Если у тебя уже есть старый сервер TrustTunnel и нужно сохранить пользователей, возьми с него файл:

```bash
scp root@OLD_SERVER:/opt/trusttunnel/credentials.toml ./credentials.toml
```

В `deploy.yml` укажи:

```yaml
trusttunnel:
  domain: vpn.example.com
  public_address: vpn.example.com:443
  cert_mode: letsencrypt
  acme_email: admin@example.com

  existing_credentials_source: ./credentials.toml
  existing_credentials_file: credentials.toml
  clients: []
```

Затем:

```bash
./ttctl init --config deploy.yml
./ttctl deploy
```

`ttctl init` скопирует файл в:

```text
roles/trusttunnel_endpoint/files/credentials.toml
```

А Ansible-роль положит его на сервер как:

```text
/opt/trusttunnel/credentials.toml
```

Пароли пользователей сохранятся из исходного файла.

Также можно импортировать credentials отдельно:

```bash
./ttctl import-credentials ./credentials.toml
```

После этого в `vars.yml` или `deploy.yml` нужно использовать:

```yaml
trusttunnel_existing_credentials_file: credentials.toml
```

или в `deploy.yml`:

```yaml
trusttunnel:
  existing_credentials_file: credentials.toml
  clients: []
```

## Добавление VPN-клиентов после установки

Подготовь файл:

```text
roles/trusttunnel_endpoint/files/new_clients.toml
```

Пример:

```toml
[[client]]
username = "alice"
password = "alice-password"

[[client]]
username = "bob"
password = "bob-password"
```

Запусти:

```bash
./ttctl add-client
```

Эта команда вызывает существующий helper `add_vpn_clients.sh`.

Если текущего локального `credentials.toml` еще нет, сначала забери его с сервера:

```bash
scp root@SERVER:/opt/trusttunnel/credentials.toml roles/trusttunnel_endpoint/files/credentials.toml
```

Или, если подключение не под root:

```bash
scp ubuntu@SERVER:/tmp/credentials.toml roles/trusttunnel_endpoint/files/credentials.toml
```

## Удаление VPN-клиентов

Подготовь файл:

```text
roles/trusttunnel_endpoint/files/remove_clients.txt
```

Пример:

```text
alice
bob
```

Запусти:

```bash
./ttctl remove-client
```

Эта команда вызывает существующий helper `remove_vpn_clients.sh`.

## Проверка установленного сервиса

Через helper:

```bash
./ttctl status
```

Или вручную на сервере:

```bash
sudo systemctl status trusttunnel
sudo journalctl -u trusttunnel -f
```

Файлы на сервере:

```text
/opt/trusttunnel/trusttunnel_endpoint
/opt/trusttunnel/vpn.toml
/opt/trusttunnel/hosts.toml
/opt/trusttunnel/credentials.toml
/opt/trusttunnel/rules.toml
/etc/systemd/system/trusttunnel.service
```

## Повторный деплой

После изменения `deploy.yml`:

```bash
./ttctl init --config deploy.yml
./ttctl deploy
```

Если меняешь только Ansible-переменные в `vars.yml`, можно сразу:

```bash
./ttctl deploy
```

## Удаление TrustTunnel с сервера

Через helper:

```bash
./ttctl uninstall
```

По умолчанию uninstall:

- останавливает и отключает `trusttunnel.service`;
- удаляет systemd unit;
- удаляет `/opt/trusttunnel`;
- удаляет deploy hook Certbot;
- удаляет cron fallback для Certbot, если он создавался;
- удаляет локально скачанные клиентские конфиги из `client_configs`.

Let's Encrypt-сертификат и firewall-правила по умолчанию не удаляются.

Для полного тестового удаления:

```bash
./ttctl uninstall \
  -e trusttunnel_remove_letsencrypt_cert=true \
  -e trusttunnel_close_firewall=true
```

## Откат изменений этой ветки

Эти P1-изменения сделаны безопасно и добавлены отдельной веткой.

Если PR еще не влит:

- просто не мержить PR;
- перейти обратно на `main`:

```bash
git checkout main
```

Если нужно удалить локальные runtime-файлы:

```bash
rm -f inventory.ini vars.yml vault.yml deploy.sh uninstall.sh deploy.yml
rm -rf client_configs
rm -f roles/trusttunnel_endpoint/files/credentials.toml
```

Если PR уже был влит и нужно откатить git-изменения, смотри:

```text
docs/ROLLBACK.md
```

## Старый интерактивный способ через `bootstrap.sh`

Старый способ сохранен.

```bash
./bootstrap.sh
```

Он интерактивно спросит:

- установлен ли Ansible;
- адрес сервера;
- SSH-метод: пароль или ключ;
- домен;
- режим сертификата;
- email Let's Encrypt;
- использовать ли существующий `credentials.toml`;
- пользователя и пароль VPN-клиента;
- открывать ли firewall;
- шифровать ли секреты через Ansible Vault.

Этот способ полезен, если:

- нужен Ansible Vault для секретов;
- нужен SSH по паролю;
- не хочется руками заполнять `deploy.yml`;
- новый `ttctl` workflow пока не подходит.

## Важные Ansible-переменные

Ниже основные переменные, которые можно задавать через `vars.yml` или генерировать через `deploy.yml`.

- `trusttunnel_domain` — домен TrustTunnel endpoint.
- `trusttunnel_public_address` — адрес для клиентских конфигов.
- `trusttunnel_cert_mode` — `letsencrypt`, `selfsigned` или `existing`.
- `trusttunnel_acme_email` — email для Let's Encrypt.
- `trusttunnel_existing_cert_chain_path` — путь к существующему fullchain при `cert_mode: existing`.
- `trusttunnel_existing_private_key_path` — путь к существующему private key при `cert_mode: existing`.
- `trusttunnel_open_firewall` — открывать локальные firewall-порты средствами Ansible.
- `trusttunnel_firewall_backend` — `auto`, `ufw`, `firewalld` или `none`.
- `trusttunnel_clients` — список VPN-клиентов.
- `trusttunnel_existing_credentials_file` — имя готового credentials-файла из `roles/trusttunnel_endpoint/files/`.
- `trusttunnel_install_version` — версия TrustTunnel release, например `1.0.33`; по умолчанию `auto`.
- `trusttunnel_client_config_format` — `toml` или `deeplink`.
- `trusttunnel_fetch_client_configs` — скачивать клиентские конфиги локально.
- `trusttunnel_client_config_local_dir` — куда сохранять клиентские конфиги локально.
- `trusttunnel_prune_client_configs` — удалять stale клиентские конфиги.
- `trusttunnel_generate_client_random_prefix` — генерировать TLS random prefix.
- `trusttunnel_remove_install_dir` — удалять `/opt/trusttunnel` при uninstall.
- `trusttunnel_remove_letsencrypt_cert` — удалять Let's Encrypt сертификат при uninstall.
- `trusttunnel_close_firewall` — убирать firewall-правила при uninstall.

## Что появится локально после запуска

Файлы, которые создаются локально и не должны попадать в git:

```text
inventory.ini
vars.yml
vault.yml
deploy.yml
deploy.sh
uninstall.sh
deployment_info_<host>.md
client_configs/
roles/trusttunnel_endpoint/files/credentials.toml
roles/trusttunnel_endpoint/files/new_clients.toml
roles/trusttunnel_endpoint/files/remove_clients.txt
```

Они добавлены в `.gitignore`.
