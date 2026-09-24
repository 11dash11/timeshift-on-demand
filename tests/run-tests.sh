#!/bin/bash
#
# Tests for the crash-tolerance logic in
# packaging/helpers/timeshift-on-demand-backup-helper.
#
# Usage:  tests/run-tests.sh      (no root, no real Timeshift needed)
#
# The helper calls Timeshift by absolute path (/usr/bin/timeshift, on
# purpose, because it runs as root via pkexec), so a fake `timeshift`
# earlier in PATH wouldn't be picked up by the real file. The helper
# itself is not changed. Each run instead makes a throwaway copy with
# exactly those absolute paths rewritten to plain `timeshift`, then puts
# tests/fake-timeshift/ first in PATH. The copy must contain exactly
# EXPECTED_TIMESHIFT_CALLS occurrences before rewriting and none after,
# so if the helper changes how it calls Timeshift, this fails loudly
# instead of silently testing something else.
#
# Each case runs with TMPDIR pointed at its own empty directory. The case
# checks that the directory is empty again afterwards (the helper's
# mktemp + `trap ... EXIT` cleanup), and that the fake saw the helper's
# temp file there during --create (proving mktemp really used it).

set -u

TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(dirname "$TESTS_DIR")
HELPER="$REPO_DIR/packaging/helpers/timeshift-on-demand-backup-helper"
FAKE_DIR="$TESTS_DIR/fake-timeshift"
EXPECTED_TIMESHIFT_CALLS=1

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0

# --- build the testable copy ---------------------------------------------

HELPER_COPY="$WORK/backup-helper-under-test"
found=$(grep -o '/usr/bin/timeshift' "$HELPER" | wc -l)
if [[ "$found" -ne "$EXPECTED_TIMESHIFT_CALLS" ]]; then
    echo "SETUP FAIL: expected $EXPECTED_TIMESHIFT_CALLS references to /usr/bin/timeshift in"
    echo "  $HELPER, found $found. Update this harness to match the helper."
    exit 2
fi
sed 's#/usr/bin/timeshift#timeshift#g' "$HELPER" > "$HELPER_COPY"
chmod +x "$HELPER_COPY"
if grep -q '/usr/bin/timeshift' "$HELPER_COPY"; then
    echo "SETUP FAIL: /usr/bin/timeshift still present in the testable copy"
    exit 2
fi

# --- runner --------------------------------------------------------------

# run_case NAME SCENARIO EXPECTED_EXIT EXPECT_LIST_CALLED(yes|no) [EXPECTED_MESSAGE]
run_case() {
    local name=$1 scenario=$2 want_exit=$3 want_list=$4 want_msg=${5:-}
    local case_dir="$WORK/case-$scenario"
    local tmp="$case_dir/tmp" log="$case_dir/calls.log" out="$case_dir/out.txt"
    mkdir -p "$tmp"
    : > "$log"

    local got_exit=0
    env PATH="$FAKE_DIR:$PATH" TMPDIR="$tmp" \
        FAKE_TIMESHIFT_SCENARIO="$scenario" FAKE_TIMESHIFT_LOG="$log" \
        "$HELPER_COPY" > "$out" 2>&1 || got_exit=$?

    local problems=()

    [[ "$got_exit" -eq "$want_exit" ]] \
        || problems+=("exit code: want $want_exit, got $got_exit")

    grep -qx -- '--create --tags D --scripted' "$log" \
        || problems+=("timeshift --create --tags D --scripted was never called")

    if [[ "$want_list" == yes ]]; then
        grep -qx -- '--list' "$log" || problems+=("expected a 'timeshift --list' call, none made")
    else
        grep -qx -- '--list' "$log" && problems+=("unexpected 'timeshift --list' call")
    fi

    if [[ -n "$want_msg" ]]; then
        grep -qF -- "$want_msg" "$out" || problems+=("output missing: $want_msg")
    fi

    local seen_during
    seen_during=$(cat "$log.tmpcount" 2>/dev/null || echo 0)
    [[ "$seen_during" -ge 1 ]] \
        || problems+=("helper's mktemp file not seen in private TMPDIR during run (hard-coded /tmp?)")

    local leftover
    leftover=$(ls -A "$tmp")
    [[ -z "$leftover" ]] \
        || problems+=("temp file(s) leaked into TMPDIR: $leftover")

    if [[ ${#problems[@]} -eq 0 ]]; then
        printf 'PASS  %s\n' "$name"
        pass=$((pass + 1))
    else
        printf 'FAIL  %s\n' "$name"
        printf '        - %s\n' "${problems[@]}"
        echo "        --- helper output ---"
        sed 's/^/        | /' "$out"
        fail=$((fail + 1))
    fi
}

# --- cases ---------------------------------------------------------------

run_case "1  exit 0, real success -> 0" \
    success 0 no

run_case "2  exit 1, generic failure -> passes through as 1" \
    generic-fail 1 no

run_case "3  exit 134 + failure phrase, recent snapshot exists -> stays 134" \
    abort-with-failure-phrase 134 no \
    "Exit 134 but output shows a real failure"

# Exit 134 follows the same rule as 139: success only when this run's
# output says "Snapshot saved successfully" and has no failure phrase.
# `timeshift --list` is never consulted, so snapshot age doesn't matter.

run_case "4  exit 134, 'Snapshot saved successfully', no failure phrase -> tolerated, 0" \
    abort-recent 0 no \
    "Exit 134 (abort) after the snapshot was already saved"

run_case "4b exit 134, no saved line, a previous run's snapshot is 1 min old -> stays 134" \
    abort-recent-not-saved 134 no

run_case "5a exit 134, saved line, long run (snapshot 10 min old) -> tolerated, 0" \
    abort-old 0 no \
    "Exit 134 (abort) after the snapshot was already saved"

run_case "5b exit 134, saved line, --list would show no snapshots -> tolerated, 0 (--list not used)" \
    abort-none 0 no \
    "Exit 134 (abort) after the snapshot was already saved"

run_case "6  exit 139, 'Snapshot saved successfully', no failure phrase -> tolerated, 0" \
    segv-saved 0 no \
    "Exit 139 (segfault) after the snapshot was already saved"

run_case "7  exit 139, snapshot never saved -> stays 139" \
    segv-not-saved 139 no

run_case "8  exit 139, saved successfully AND failure phrase -> stays 139" \
    segv-saved-and-failure-phrase 139 no \
    "Exit 139 but output shows a real failure"

echo
echo "$pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
