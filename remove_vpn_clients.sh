#!/bin/bash
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FILES_DIR="${SCRIPT_DIR}/roles/trusttunnel_endpoint/files"
INPUT_FILE="${1:-${FILES_DIR}/remove_clients.txt}"
CREDENTIALS_FILE="${2:-${FILES_DIR}/credentials.toml}"
DEPLOY_AFTER_REMOVE="${TRUSTTUNNEL_DEPLOY_AFTER_REMOVE:-ask}"

usage() {
  echo "Usage: $0 [remove_clients.txt] [credentials.toml]" >&2
  echo >&2
  echo "Input format: one VPN username per line. Empty lines and # comments are ignored." >&2
  echo >&2
  echo "Set TRUSTTUNNEL_DEPLOY_AFTER_REMOVE=yes or no to skip the interactive deploy question." >&2
}

cleanup() {
  if [ -n "${TMP_CREDENTIALS:-}" ] && [ -f "$TMP_CREDENTIALS" ]; then
    rm -f "$TMP_CREDENTIALS"
  fi
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

should_deploy_after_remove() {
  case "$DEPLOY_AFTER_REMOVE" in
    yes|true|1) return 0 ;;
    no|false|0) return 1 ;;
    ask)
      if [ -r /dev/tty ]; then
        prompt_yes_no "Run deploy now to sync credentials and refresh client configs? yes/no" "yes"
        return $?
      fi
      return 1
      ;;
    *)
      echo "Invalid TRUSTTUNNEL_DEPLOY_AFTER_REMOVE value: $DEPLOY_AFTER_REMOVE. Use yes, no or ask." >&2
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

if [ ! -f "$CREDENTIALS_FILE" ]; then
  echo "Credentials file not found: $CREDENTIALS_FILE" >&2
  echo "Copy the current server file first, for example:" >&2
  echo "  scp root@<server>:/opt/trusttunnel/credentials.toml $CREDENTIALS_FILE" >&2
  exit 1
fi

TMP_CREDENTIALS=$(mktemp /tmp/trusttunnel-credentials.XXXXXX)
trap cleanup EXIT

awk '
  FNR == NR {
    line = $0
    sub(/[ \t]*#.*/, "", line)
    sub(/^[ \t\r\n]+/, "", line)
    sub(/[ \t\r\n]+$/, "", line)
    if (line != "") {
      remove_user[line] = 1
      requested++
    }
    next
  }

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

  function flush_block() {
    if (block == "") {
      return
    }
    if (block_username != "" && (block_username in remove_user)) {
      removed[block_username] = 1
    } else {
      printf "%s", block
    }
    block = ""
    block_username = ""
  }

  BEGIN {
    in_client = 0
  }

  /^[ \t]*\[\[client\]\][ \t]*$/ {
    flush_block()
    in_client = 1
    block = $0 "\n"
    next
  }

  in_client {
    block = block $0 "\n"
    if ($0 ~ /^[ \t]*username[ \t]*=/) {
      value = $0
      sub(/^[^=]*=[ \t]*/, "", value)
      block_username = toml_value(value)
    }
    next
  }

  {
    printf "%s\n", $0
  }

  END {
    flush_block()
    if (requested == 0) {
      print "No usernames found in remove list." > "/dev/stderr"
      exit 2
    }
    for (username in remove_user) {
      if (removed[username]) {
        printf "Removed VPN client: %s\n", username > "/dev/stderr"
      } else {
        printf "VPN client was not present: %s\n", username > "/dev/stderr"
      }
    }
  }
' "$INPUT_FILE" "$CREDENTIALS_FILE" > "$TMP_CREDENTIALS"

cat "$TMP_CREDENTIALS" > "$CREDENTIALS_FILE"
chmod 600 "$CREDENTIALS_FILE"

echo "Credentials file updated: $CREDENTIALS_FILE"

if should_deploy_after_remove; then
  if [ ! -x "${SCRIPT_DIR}/deploy.sh" ]; then
    echo "deploy.sh was not found or is not executable. Run ./bootstrap.sh first or run ansible-playbook manually." >&2
    exit 1
  fi

  credentials_file_name=$(credentials_file_name_for_role)
  echo "Running deploy to sync credentials and refresh client configs..." >&2
  "${SCRIPT_DIR}/deploy.sh" \
    -e "trusttunnel_existing_credentials_file=${credentials_file_name}" \
    -e trusttunnel_generate_client_config=true \
    -e trusttunnel_fetch_client_configs=true \
    -e trusttunnel_prune_client_configs=true
else
  credentials_dir=$(CDPATH= cd -- "$(dirname -- "$CREDENTIALS_FILE")" && pwd)
  if [ "$credentials_dir" = "$FILES_DIR" ]; then
    echo "Run ./deploy.sh -e trusttunnel_existing_credentials_file=$(basename -- "$CREDENTIALS_FILE") to sync credentials on the server."
  else
    echo "Place the credentials file under $FILES_DIR before running deploy with trusttunnel_existing_credentials_file."
  fi
fi
