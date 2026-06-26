# TrustTunnel Server Ansible

Ansible-проект для быстрой установки и обслуживания TrustTunnel Endpoint на Linux-сервере.

Проект умеет:

- устанавливать TrustTunnel Endpoint;
- настраивать TLS через Let's Encrypt, self-signed или существующий сертификат;
- создавать `systemd`-сервис;
- генерировать клиентские VPN-конфиги;
- переносить существующих VPN-пользователей через `credentials.toml`;
- добавлять и удалять VPN-клиентов без повторного полного деплоя;
- удалять установленный TrustTunnel с сервера;
- запускаться через helper `ttctl` или старый интерактивный `bootstrap.sh`.

## 0. Установка зависимостей на управляющей машине

Перед `./ttctl init`, `./ttctl deploy`, `./ttctl add-client` и `./ttctl remove-client` нужно подготовить управляющую машину: локальный Linux/WSL, admin VM или CI runner, откуда запускается Ansible.

Минимально нужны:

- `python3`;
- `ansible`;
- `ansible-playbook`;
- Python-модуль `yaml`, то есть `PyYAML` или distro-пакет `python3-yaml`;
- `sshpass`, если подключение к серверу идет по SSH-паролю, а не по SSH-ключу.

Проверить зависимости без установки:

```bash
chmod +x scripts/install_requirements.sh scripts/uninstall_requirements.sh
./scripts/install_requirements.sh --check-only
```

Установить недостающие зависимости интерактивно:

```bash
./scripts/install_requirements.sh
```

Установить без вопросов:

```bash
./scripts/install_requirements.sh --yes
```

Если хочешь поставить Python-зависимости в virtualenv:

```bash
./scripts/install_requirements.sh --venv
source .venv/bin/activate
```

`requirements.txt` содержит только Python-зависимости проекта:

```text
PyYAML>=6.0
```

`python3`, `ansible`, `ansible-playbook` и `sshpass` устанавливаются через пакетный менеджер ОС, а не через `requirements.txt`.

Для безопасного удаления вспомогательных зависимостей есть отдельный скрипт:

```bash
./scripts/uninstall_requirements.sh
```

Он спрашивает отдельно про каждый компонент и не удаляет `python3` автоматически, потому что Python часто нужен системе и другим инструментам. Подробнее: `docs/DEPENDENCIES.md`.

## Самый короткий сценарий

Если SSH уже работает по ключу:

```bash
chmod +x scripts/install_requirements.sh scripts/uninstall_requirements.sh ttctl
./scripts/install_requirements.sh --check-only || ./scripts/install_requirements.sh
cp deploy.example.yml deploy.yml
nano deploy.yml
./ttctl init --config deploy.yml
./ttctl deploy
./ttctl status
```

Если SSH только по паролю, безопасный вариант через Ansible Vault:

```bash
chmod +x scripts/install_requirements.sh scripts/uninstall_requirements.sh ttctl
./scripts/install_requirements.sh --check-only || ./scripts/install_requirements.sh
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
7. Подготовленную управляющую машину с зависимостями из раздела `0. Установка зависимостей на управляющей машине`.

## Команды `ttctl`

```bash
./ttctl init --config deploy.yml
./ttctl init --config deploy.yml --ask-ssh-pass
./ttctl init --config deploy.yml --ask-become-pass
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
./ttctl deploy
./ttctl status
./ttctl add-client
./ttctl add-client --sync-from-server
./ttctl add-client --no-apply
./ttctl remove-client
./ttctl remove-client --sync-from-server
./ttctl remove-client --no-apply
./ttctl import-credentials ./credentials.toml
./ttctl uninstall
```

`ttctl deploy` — это полный install/update через `site.yml`.

`ttctl add-client` и `ttctl remove-client` — легкие post-install операции. Они не запускают полный deploy.

Если в проекте есть `vault.yml`, `ttctl` спросит Vault-пароль один раз за запуск команды и будет использовать временный защищенный `--vault-password-file` для всех внутренних Ansible-вызовов. Временный файл удаляется при завершении команды.

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
- `trusttunnel.clients` — VPN-пользователи для первичной раскатки.

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

Если рядом есть `vault.yml`, `ttctl deploy` автоматически добавит его в запуск и спросит Vault-пароль.

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

## Let's Encrypt сертификаты при uninstall и повторном deploy

Обычный uninstall:

```bash
./ttctl uninstall
```

не удаляет Let's Encrypt сертификат. Он удаляет сервис, unit, install directory и служебные hook/cron-файлы, но сертификат остается в `/etc/letsencrypt/live/<domain>/`.

При следующем deploy с:

```yaml
trusttunnel:
  cert_mode: letsencrypt
```

роль снова запускает Certbot, но с флагом `--keep-until-expiring`. Это значит: если сертификат уже есть и еще не близок к истечению, Certbot переиспользует существующий сертификат, а не выпускает новый каждый раз.

Если нужен полный тестовый снос вместе с сертификатом:

```bash
./ttctl uninstall -e trusttunnel_remove_letsencrypt_cert=true
```

## Первый деплой нового сервера

```bash
git fetch origin
git checkout p1-usability-safe-cli
chmod +x scripts/install_requirements.sh scripts/uninstall_requirements.sh ttctl
./scripts/install_requirements.sh --check-only || ./scripts/install_requirements.sh
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

Затем запусти:

```bash
./ttctl add-client
```

`ttctl add-client` не запускает полный `site.yml`. Это post-install операция:

1. если локального `roles/trusttunnel_endpoint/files/credentials.toml` нет, он сам забирает текущий `/opt/trusttunnel/credentials.toml` с сервера через Ansible;
2. добавляет новых клиентов из `new_clients.toml` в локальный `credentials.toml`;
3. пропускает клиентов с уже существующим `username`;
4. проверяет, что `trusttunnel.service` активен на сервере;
5. копирует обновленный `credentials.toml` обратно на сервер в `/opt/trusttunnel/credentials.toml`;
6. выполняет `systemctl restart trusttunnel`, потому что TrustTunnel перечитывает `credentials.toml` только после рестарта процесса;
7. проверяет, что сервис остался активен;
8. генерирует клиентские конфиги для пользователей из `new_clients.toml`;
9. скачивает клиентские конфиги локально в `client_configs/`.

Если хочешь принудительно обновить локальный `credentials.toml` с сервера перед добавлением:

```bash
./ttctl add-client --sync-from-server
```

Если хочешь только изменить локальный `credentials.toml`, но не применять на сервере сразу:

```bash
./ttctl add-client --no-apply
```

Старый флаг `--no-deploy` тоже принимается как alias для `--no-apply`.

Если файл с новыми клиентами лежит в другом месте:

```bash
./ttctl add-client ./my_new_clients.toml
```

## Удаление VPN-клиентов после установки

Подготовь файл:

```text
roles/trusttunnel_endpoint/files/remove_clients.txt
```

Пример:

```text
alice
bob
```

Затем запусти:

```bash
./ttctl remove-client
```

`ttctl remove-client` тоже не запускает полный `site.yml`. Это post-install операция:

1. если локального `credentials.toml` нет, он забирает текущий `/opt/trusttunnel/credentials.toml` с сервера;
2. удаляет указанных пользователей из локального `credentials.toml`;
3. проверяет, что `trusttunnel.service` активен;
4. копирует обновленный `credentials.toml` обратно на сервер;
5. выполняет `systemctl restart trusttunnel`, потому что TrustTunnel перечитывает `credentials.toml` только после рестарта процесса;
6. проверяет, что сервис остался активен;
7. удаляет сгенерированные client config-файлы для этих пользователей на сервере и локально.

Если хочешь принудительно обновить локальный `credentials.toml` с сервера перед удалением:

```bash
./ttctl remove-client --sync-from-server
```

Если хочешь только изменить локальный `credentials.toml`, но не применять на сервере сразу:

```bash
./ttctl remove-client --no-apply
```

## Почему нет отдельного режима полного обновления клиентов

Для текущего проекта безопаснее оставить операции явными:

- `add-client` — только добавляет новых, дубли пропускает;
- `remove-client` — явно удаляет указанных;
- полную замену клиентов не делать отдельной быстрой командой, чтобы не потерять доступы случайно.

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

Для добавления или удаления клиентов после установки полный deploy не нужен:

```bash
./ttctl add-client
./ttctl remove-client
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

## Удаление зависимостей с управляющей машины

Если нужно убрать вспомогательные пакеты с управляющей машины, используй безопасный helper:

```bash
./scripts/uninstall_requirements.sh
```

Он спрашивает отдельно про каждый компонент. Для предварительного просмотра:

```bash
./scripts/uninstall_requirements.sh --dry-run
```

Не удаляй `ansible`, `sshpass`, `python3-pip` или `PyYAML`, если они нужны другим проектам. `python3` скрипт не удаляет автоматически.

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
