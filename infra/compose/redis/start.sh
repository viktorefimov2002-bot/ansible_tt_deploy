#!/bin/sh
set -eu
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
# Hash through stdin: no plaintext password in Redis config or process arguments.
password_hash=$(printf '%s' "$REDIS_PASSWORD" | sha256sum)
password_hash=${password_hash%% *}
umask 077
printf 'user default on #%s ~* &* +@all\n' "$password_hash" > /run/redis/users.acl
chown redis:redis /run/redis/users.acl
unset password_hash
exec /usr/local/bin/docker-entrypoint.sh redis-server /usr/local/etc/redis/redis.conf
