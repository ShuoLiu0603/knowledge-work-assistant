#!/bin/sh
set -eu

require_positive_number() {
    name="$1"
    value="$2"
    if ! awk -v value="$value" 'BEGIN { exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value > 0) }'; then
        echo "$name must be a positive number" >&2
        exit 1
    fi
}

require_positive_integer() {
    name="$1"
    value="$2"
    case "$value" in
        ''|*[!0-9]*)
            echo "$name must be a positive integer" >&2
            exit 1
            ;;
    esac
    if [ "$value" -le 0 ]; then
        echo "$name must be a positive integer" >&2
        exit 1
    fi
}

require_positive_integer "MAX_UPLOAD_SIZE_MB" "${MAX_UPLOAD_SIZE_MB}"
require_positive_integer "NGINX_MAX_BODY_SIZE_MB" "${NGINX_MAX_BODY_SIZE_MB}"
require_positive_number "AGENT_STREAM_MIN_TIMEOUT_SECONDS" "${AGENT_STREAM_MIN_TIMEOUT_SECONDS}"
require_positive_integer "LLM_TIMEOUT_SECONDS" "${LLM_TIMEOUT_SECONDS}"
require_positive_integer "AGENT_STREAM_TIMEOUT_LLM_CALLS" "${AGENT_STREAM_TIMEOUT_LLM_CALLS}"
require_positive_integer "NGINX_PROXY_TIMEOUT_SECONDS" "${NGINX_PROXY_TIMEOUT_SECONDS}"

if ! printf '%s\n' "${API_PREFIX}" | grep -Eq '^/[A-Za-z0-9._~/-]+$' \
    || printf '%s\n' "${API_PREFIX}" | grep -Eq '(^|/)\.{1,2}(/|$)' \
    || [ "${API_PREFIX}" = "/" ] \
    || [ "${API_PREFIX%/}" != "${API_PREFIX}" ] \
    || printf '%s\n' "${API_PREFIX}" | grep -Fq '//'; then
    echo "API_PREFIX must be a safe non-root path without dot segments or a trailing slash" >&2
    exit 1
fi

if ! awk -v proxy="${NGINX_MAX_BODY_SIZE_MB}" -v file="${MAX_UPLOAD_SIZE_MB}" \
    'BEGIN { exit !(proxy > file) }'; then
    echo "NGINX_MAX_BODY_SIZE_MB must exceed MAX_UPLOAD_SIZE_MB for multipart overhead" >&2
    exit 1
fi

agent_deadline="$(
    awk \
        -v minimum="${AGENT_STREAM_MIN_TIMEOUT_SECONDS}" \
        -v timeout="${LLM_TIMEOUT_SECONDS}" \
        -v calls="${AGENT_STREAM_TIMEOUT_LLM_CALLS}" \
        'BEGIN { computed = timeout * calls; print (minimum > computed ? minimum : computed) }'
)"

if ! awk -v proxy="${NGINX_PROXY_TIMEOUT_SECONDS}" -v deadline="${agent_deadline}" \
    'BEGIN { exit !(proxy >= deadline) }'; then
    echo "NGINX_PROXY_TIMEOUT_SECONDS must cover the computed Agent stream deadline" >&2
    exit 1
fi
