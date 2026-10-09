#!/bin/bash
# Fixed labels only: callers must never supply commands, URLs or secret values.
stage=bootstrap
checkpoint() {
    stage=$1
    printf 'CHECK: %s\n' "$stage"
}
fail() {
    printf 'FAIL: stage=%s assertion=%s\n' "$stage" "$1" >&2
    exit 1
}
# Never print BASH_COMMAND, arguments, environment, response bodies or service logs.
trap 'code=$?; printf "FAIL: stage=%s line=%s exit=%s\n" "$stage" "$LINENO" "$code" >&2; exit "$code"' ERR

expect_exit() {
    local label=$1 expected=$2 actual=0
    shift 2
    checkpoint "$label"
    # Finish reading the producer before matching. grep -q on a live Compose pipe
    # can close stdout early and make exec exit 255 even when the match succeeded.
    # The captured output is private; it is never included in a diagnostic.
    output=$("$@" 2>&1) || actual=$?
    if [ "$actual" != "$expected" ]; then
        printf 'FAIL: stage=%s expected_exit=%s actual_exit=%s\n' "$stage" "$expected" "$actual" >&2
        [ "$actual" != 0 ] || actual=1
        exit "$actual"
    fi
}
_assert_output() {
    local flags=$1 label=$2 pattern=$3 expected=$4
    shift 4
    expect_exit "$label" "$expected" "$@"
    grep "$flags" -- "$pattern" <<< "$output" >/dev/null || fail response-mismatch
    unset output
}
assert_output() { _assert_output -E "$@"; }
assert_output_ci() { _assert_output -Ei "$@"; }
# Only wget's request failure (1) is retryable here. A Compose/exec failure such
# as 255 must fail immediately, rather than masquerading as eventual readiness.
probe_output() {
    local pattern=$1 actual=0
    shift
    output=$("$@" 2>&1) || actual=$?
    if [ "$actual" != 0 ] && [ "$actual" != 1 ]; then
        printf 'FAIL: stage=%s probe_exit=%s\n' "$stage" "$actual" >&2
        exit "$actual"
    fi
    if [ "$actual" = 0 ] && grep -E -- "$pattern" <<< "$output" >/dev/null; then
        unset output
        return 0
    fi
    unset output
    return 1
}
