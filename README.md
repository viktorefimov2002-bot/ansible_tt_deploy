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

## Bootstrap

Есть тонкий helper-скрипт:

```bash
./bootstrap.sh
```

Он спрашивает базовые параметры, создает `inventory.ini` и `vars.yml`, выставляет `600` на `vars.yml`, затем запускает `ansible-playbook`. Основная логика установки остается в Ansible-роли.

## Удаление и повторное тестирование

Для быстрого демонтажа есть отдельный playbook:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e @vars.yml
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
ansible-playbook -i inventory.ini uninstall.yml -e @vars.yml \
  -e trusttunnel_remove_letsencrypt_cert=true \
  -e trusttunnel_close_firewall=true
```

Можно запустить удаление и через основной playbook:

```bash
ansible-playbook -i inventory.ini site.yml -e @vars.yml -e trusttunnel_state=absent
```

## Важные переменные

- `trusttunnel_install_version` - версия endpoint-релиза. По умолчанию `auto`: используется версия, встроенная в скачанный с ветки `master` `install.sh`. Для закрепления конкретного релиза укажите, например, `1.0.33`.
- `trusttunnel_install_dir` - директория установки, по умолчанию `/opt/trusttunnel`.
- `trusttunnel_listen_address` - адрес прослушивания, по умолчанию `0.0.0.0:443`.
- `trusttunnel_cert_mode` - `letsencrypt`, `selfsigned` или `existing`.
- `trusttunnel_existing_cert_chain_path` и `trusttunnel_existing_private_key_path` - пути к сертификату и ключу при `existing`.
- `trusttunnel_open_firewall` - открыть 80/tcp, 443/tcp и 443/udp до выпуска сертификата, по умолчанию выключено.
- `trusttunnel_firewall_backend` - `firewalld`, `ufw` или `none`.
- `trusttunnel_fetch_client_configs` - забрать клиентские конфиги на управляющую машину, по умолчанию включено.
- `trusttunnel_client_config_local_dir` - локальная директория для скачанных клиентских конфигов, по умолчанию `client_configs` рядом с playbook.
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
