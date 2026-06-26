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
- запускаться через новый helper `ttctl` или старый интерактивный `bootstrap.sh`.

## Самый короткий сценарий

Если SSH уже работает по ключу:

```bash
chmod +x ttctl
cp deploy.example.yml deploy.yml
nano deploy.yml
./ttctl init --config deploy.yml
./ttctl deploy
./ttctl status
```

Если SSH только по паролю, безопасный вариант через Ansible Vault:

```bash
chmod +x ttctl
cp deploy.example.yml deploy.yml
nano deploy.yml
./ttctl init --config deploy.yml --ask-ssh-pass
./ttctl deploy
./ttctl status
```

`--ask-ssh-pass` спросит SSH-пароль скрытым вводом, создаст `vault.yml` и сразу зашифрует его через Ansible Vault. Пароль не нужно писать в `deploy.yml`.

Если sudo тоже требует пароль:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

Если новый `ttctl`-workflow не подходит, можно использовать старый интерактивный сценарий:

```bash
./bootstrap.sh
```

## Что подготовить заранее

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

Для SSH по паролю обычно также нужен `sshpass`:

```bash
sudo apt-get install -y sshpass
```

## Новый workflow через `ttctl`

`ttctl` — единая обертка над существующими playbook'ами и helper-скриптами.

Команды:

```bash
./ttctl init --config deploy.yml
./ttctl init --config deploy.yml --ask-ssh-pass
./ttctl init --config deploy.yml --ask-become-pass
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
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

`deploy.yml` добавлен в `.gitignore`, потому что в нем могут быть реальные адреса и параметры сервера.

Минимальный пример для сервера, куда ты подключаешься по SSH-ключу под `root`:

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

Минимально замени:

- `server.host` — IP или DNS-имя сервера;
- `server.ssh_user` — SSH-пользователь;
- `server.become` — `true`, если нужен sudo;
- `trusttunnel.domain` — домен VPN;
- `trusttunnel.public_address` — адрес для клиентских конфигов, обычно `domain:443`;
- `trusttunnel.cert_mode` — обычно `letsencrypt`;
- `trusttunnel.acme_email` — email для Let's Encrypt;
- `trusttunnel.clients` — VPN-пользователи.

## Откуда Ansible берет SSH-пароль

Ansible не узнает пароль от сервера автоматически. Есть три сценария.

### 1. SSH-ключ

Если работает:

```bash
ssh root@203.0.113.10
```

и пароль не спрашивается, то в `deploy.yml` пароль не нужен:

```yaml
server:
  host: 203.0.113.10
  ssh_user: root
  become: false
```

После `./ttctl init --config deploy.yml` Ansible будет использовать обычный SSH-клиент, ключи из `~/.ssh`, `ssh-agent` и SSH config.

### 2. SSH-пароль через Vault — рекомендуемый парольный способ

Если сервер пускает только по паролю, не пиши пароль в `deploy.yml`. Запусти:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

`ttctl`:

1. спросит SSH-пароль скрытым вводом;
2. спросит пароль для Ansible Vault;
3. создаст обычный `vars.yml` без SSH-пароля;
4. создаст `vault.yml` с `ansible_password`;
5. сразу зашифрует `vault.yml` через `ansible-vault encrypt`.

Дальше:

```bash
./ttctl deploy
```

Если рядом есть `vault.yml`, `ttctl deploy` автоматически добавит его в запуск и спросит Vault-пароль через `--ask-vault-pass`.

### 3. SSH-пароль plaintext — только для временного локального теста

Такой вариант остается, но не рекомендуется:

```yaml
ansible:
  password: remote-ssh-password
```

Тогда `ttctl init` запишет в `vars.yml`:

```yaml
ansible_password: remote-ssh-password
```

`vars.yml` имеет права `600`, но это не шифрование.

## Что такое `become_password`

`become_password` — это пароль для sudo, а не SSH-пароль.

Если подключаешься не под root, например:

```yaml
server:
  host: 203.0.113.10
  ssh_user: ubuntu
  become: true
```

и sudo требует пароль, используй Vault-backed prompt:

```bash
./ttctl init --config deploy.yml --ask-become-pass
```

Если и SSH, и sudo требуют пароль:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

В этом случае в зашифрованный `vault.yml` попадут:

```yaml
ansible_password: <ssh password>
ansible_become_password: <sudo password>
```

## Firewall-поля

```yaml
trusttunnel:
  open_firewall: false
  firewall_backend: auto
```

Если `open_firewall: false`, Ansible не меняет локальный firewall. Это нормально, если firewall выключен, правила уже открыты или порты управляются у облачного провайдера.

Если нужно открыть локальные правила через `ufw` или `firewalld`:

```yaml
trusttunnel:
  open_firewall: true
  firewall_backend: auto
```

Открываются:

- `80/tcp`;
- `443/tcp`;
- `443/udp`.

## Первый деплой нового сервера

```bash
git fetch origin
git checkout p1-usability-safe-cli
chmod +x ttctl
cp deploy.example.yml deploy.yml
nano deploy.yml
```

Если SSH по ключу:

```bash
./ttctl init --config deploy.yml
```

Если SSH по паролю:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

Если обычный пользователь + sudo с паролем:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

Затем:

```bash
./ttctl deploy
./ttctl status
```

Клиентские конфиги будут скачаны локально в:

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

Если SSH по ключу и sudo без пароля:

```bash
./ttctl init --config deploy.yml
```

Если SSH по паролю, но sudo без пароля:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

Если SSH по паролю и sudo тоже требует пароль:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

## Миграция существующих VPN-клиентов

Если нужно сохранить пользователей со старого сервера, забери `credentials.toml`:

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
```

Запусти:

```bash
./ttctl add-client
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

## Проверка установленного сервиса

```bash
./ttctl status
```

Или вручную на сервере:

```bash
sudo systemctl status trusttunnel
sudo journalctl -u trusttunnel -f
```

## Повторный деплой

После изменения `deploy.yml`:

```bash
./ttctl init --config deploy.yml
./ttctl deploy
```

Если используешь парольный доступ, повтори нужный Vault-флаг:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

Если меняешь только Ansible-переменные в `vars.yml`, можно сразу:

```bash
./ttctl deploy
```

## Удаление TrustTunnel с сервера

```bash
./ttctl uninstall
```

Для полного тестового удаления:

```bash
./ttctl uninstall \
  -e trusttunnel_remove_letsencrypt_cert=true \
  -e trusttunnel_close_firewall=true
```

## Откат изменений этой ветки

Если PR еще не влит:

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

Старый способ сохранен:

```bash
./bootstrap.sh
```

Он полезен, если:

- хочется полностью интерактивный сценарий;
- не хочется руками заполнять `deploy.yml`;
- новый `ttctl` workflow пока не подходит.

## Локальные файлы, которые не должны попадать в git

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
