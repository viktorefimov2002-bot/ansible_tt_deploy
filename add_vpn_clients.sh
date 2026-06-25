#!/bin/bash
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="${SCRIPT_DIR}/roles/trusttunnel_endpoint/files"
INPUT_FILE="${1:-${FILES_DIR}/new_clients.toml}"
CREDENTIALS_FILE="${2:-${FILES_DIR}/credentials.toml}"
DEPLOY_AFTER_ADD="${TRUSTTUNNEL_DEPLOY_AFTER_ADD:-ask}"
INIT_CREDENTIALS="${TRUSTTUNNEL_INIT_CREDENTIALS:-ask}"

usage() {
  echo "Usage: $0 [new_clients.toml] [credentials.toml]" >&2
  echo >&2
  echo "Input format:" >&2
  echo '  [[client]]' >&2
  echo '  username = "user1"' >&2
  echo '  password = "change-me"' >&2
  echo >&2
  echo "Set TRUSTTUNNEL_DEPLOY_AFTER_ADD=yes or no to skip the interactive deploy question." >&2
  echo "Set TRUSTTUNNEL_INIT_CREDENTIALS=yes only when creating a brand-new credentials file is intended." >&2
}

cleanup() {
  if [ -n "${PARSED_FILE:-}" ] && [ -f "$PARSED_FILE" ]; then
    rm -f "$PARSED_FILE"
  fi
}

parse_clients() {
  awk '
    function trim(value) {
      sub(/^[ \t\r\n]+/, "", value)
      sub(/[ \t\r\n]+$/, "", value)
      return value
    }

    function toml_value(value) {
      value = trim(value)
      if (substr(value, 1, 1) == "\"" && substr(value, length(value), 1) == "\"") {
        value = substr(value, 2, length(value) - 2)
      } else if (substr(value, 1, 1) == "'"'"'" && substr(value, length(value), 1) == "'"'"'") {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }

    function flush_client() {
      if (username != "" || password != "") {
        if (username == "" || password == "") {
          printf "Invalid client block: both username and password are required.\n" > "/dev/stderr"
          exit 1
        }
        if (username !~ /^[A-Za-z0-9_.@-]+$/) {
          printf "Invalid username '%s'. Allowed: letters, digits, underscore, dot, at-sign and hyphen.\n", username > "/dev/stderr"
          exit 1
        }
        printf "%s\t%s\n", username, password
      }
      username = ""
      password = ""
    }

    /^[ \t]*\[\[client\]\][ \t]*$/ {
      flush_client()
      in_client = 1
      next
    }

    in_client && /^[ \t]*username[ \t]*=/ {
      value = $0
      sub(/^[^=]*=[ \t]*/, "", value)
      username = toml_value(value)
      next
    }

    in_client && /^[ \t]*password[ \t]*=/ {
      value = $0
      sub(/^[^=]*=[ \t]*/, "", value)
      password = toml_value(value)
      next
    }

    END {
      flush_client()
    }
  ' "$INPUT_FILE"
}

username_exists() {
  username="$1"
  awk -v wanted="$username" '
    function trim(value) {
      sub(/^[ \t\r\n]+/, "", value)
      sub(/[ \t\r\n]+$/, "", value)
      return value
    }

    function toml_value(value) {
      value = trim(value)
      if (substr(value, 1, 1) == "\"" && substr(value, length(value), 1) == "\"") {
        value = substr(value, 2, length(value) - 2)
      } else if (substr(value, 1, 1) == "'"'"'" && substr(value, length(value), 1) == "'"'"'") {
        value = substr(value, 2, length(value) - 2)
      }
      return value
    }

    /^[ \t]*username[ \t]*=/ {
      value = $0
      sub(/^[^=]*=[ \t]*/, "", value)
      if (toml_value(value) == wanted) {
        found = 1
        exit
      }
    }

    END {
      exit found ? 0 : 1
    }
  ' "$CREDENTIALS_FILE"
}

toml_escape() {
  printf "%s" "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

prompt_yes_no() {
  label="$1"
  default="$2"

  while :; do
    printf "%s [%s]: " "$label" "$default" >&2
    IFS= read -r value </dev/tty
    if [ -z "$value" ]; then
      value="$default"
    fi
    case "$value" in
      yes|y|Y|да|д|Д) return 0 ;;
      no|n|N|нет|н|Н) return 1 ;;
      *) printf "Invalid value '%s'. Allowed values: yes/no or да/нет\n" "$value" >&2 ;;
    esac
  done
}

should_deploy_after_add() {
  case "$DEPLOY_AFTER_ADD" in
    yes|true|1) return 0 ;;
    no|false|0) return 1 ;;
    ask)
      if [ -r /dev/tty ]; then
        prompt_yes_no "Run deploy now to sync credentials and fetch client configs? yes/no" "yes"
        return $?
      fi
      return 1
      ;;
    *)
      echo "Invalid TRUSTTUNNEL_DEPLOY_AFTER_ADD value: $DEPLOY_AFTER_ADD. Use yes, no or ask." >&2
      exit 1
      ;;
  esac
}

confirm_credentials_initialization() {
  case "$INIT_CREDENTIALS" in
    yes|true|1) return 0 ;;
    no|false|0) return 1 ;;
    ask)
      if [ -r /dev/tty ]; then
        echo "Credentials file does not exist yet: $CREDENTIALS_FILE" >&2
        echo "Creating it will make this file the source of truth for the next deploy." >&2
        echo "If the server already has VPN users, first copy /opt/trusttunnel/credentials.toml from the server to preserve them." >&2
        prompt_yes_no "Create a new credentials.toml containing only clients from the input file? yes/no" "no"
        return $?
      fi
      return 1
      ;;
    *)
      echo "Invalid TRUSTTUNNEL_INIT_CREDENTIALS value: $INIT_CREDENTIALS. Use yes, no or ask." >&2
      exit 1
      ;;
  esac
}

credentials_file_name_for_role() {
  credentials_dir=$(CDPATH= cd -- "$(dirname -- "$CREDENTIALS_FILE")" && pwd)
  if [ "$credentials_dir" != "$FILES_DIR" ]; then
    echo "Automatic deploy is supported only when credentials.toml is under $FILES_DIR." >&2
    echo "Run deploy manually with a credentials file placed under the role files directory." >&2
    return 1
  fi
  basename -- "$CREDENTIALS_FILE"
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ ! -f "$INPUT_FILE" ]; then
  echo "Input file not found: $INPUT_FILE" >&2
  usage
  exit 1
fi

mkdir -p "$FILES_DIR"
mkdir -p "$(dirname -- "$CREDENTIALS_FILE")"
if [ ! -f "$CREDENTIALS_FILE" ]; then
  if ! confirm_credentials_initialization; then
    echo "Cannot continue without an existing credentials file." >&2
    echo "Copy the current server file first, for example:" >&2
    echo "  scp root@<server>:/opt/trusttunnel/credentials.toml $CREDENTIALS_FILE" >&2
    exit 1
  fi
  touch "$CREDENTIALS_FILE"
fi
chmod 600 "$CREDENTIALS_FILE"

PARSED_FILE=$(mktemp /tmp/trusttunnel-new-clients.XXXXXX)
trap cleanup EXIT
parse_clients > "$PARSED_FILE"

if [ ! -s "$PARSED_FILE" ]; then
  echo "No clients found in $INPUT_FILE." >&2
  exit 1
fi

added_count=0
skipped_count=0

while IFS="$(printf '\t')" read -r username password; do
  if username_exists "$username"; then
    echo "Skipping existing VPN client: $username"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  {
    printf '\n[[client]]\n'
    printf 'username = "%s"\n' "$(toml_escape "$username")"
    printf 'password = "%s"\n' "$(toml_escape "$password")"
  } >> "$CREDENTIALS_FILE"
  echo "Added VPN client: $username"
  added_count=$((added_count + 1))
done < "$PARSED_FILE"

echo
echo "Done. Added: $added_count, skipped existing: $skipped_count"
echo "Credentials file: $CREDENTIALS_FILE"

if should_deploy_after_add; then
  if [ ! -x "${SCRIPT_DIR}/deploy.sh" ]; then
    echo "deploy.sh was not found or is not executable. Run ./bootstrap.sh first or run ansible-playbook manually." >&2
    exit 1
  fi

  credentials_file_name=$(credentials_file_name_for_role)
  echo "Running deploy to sync credentials and fetch client configs..." >&2
  "${SCRIPT_DIR}/deploy.sh" \
    -e "trusttunnel_existing_credentials_file=${credentials_file_name}" \
    -e trusttunnel_generate_client_config=true \
    -e trusttunnel_fetch_client_configs=true
else
  credentials_dir=$(CDPATH= cd -- "$(dirname -- "$CREDENTIALS_FILE")" && pwd)
  if [ "$credentials_dir" = "$FILES_DIR" ]; then
    echo "Run ./deploy.sh -e trusttunnel_existing_credentials_file=$(basename -- "$CREDENTIALS_FILE") to sync credentials and regenerate client configs on the server."
  else
    echo "Place the credentials file under $FILES_DIR before running deploy with trusttunnel_existing_credentials_file."
  fi
fi
