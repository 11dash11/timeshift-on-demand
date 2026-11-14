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
# The helper's command line must stay exactly this. --tags D makes Timeshift
# tag snapshots "daily" (pruned by count_daily); without it they are tagged
# "ondemand" and never pruned, and any comment flag also exempts a snapshot.
if [[ "$(grep -c -- '/usr/bin/timeshift --create --tags D --scripted 2>&1' "$HELPER")" -ne 1 ]] \
   || grep -qE -- '(^|[[:space:]])--?comments?([[:space:]=]|$)' "$HELPER"; then
    echo "SETUP FAIL: the helper's timeshift command changed. It must be exactly"
    echo "  /usr/bin/timeshift --create --tags D --scripted   (no comments flag): see tests/run-tests.sh."
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
#   [EXPECTED_REASON_SUBSTRING] [UNWANTED_REASON_SUBSTRING]
# Every nonzero exit must end with a "BACKUP-FAILED: <reason>" line, and
# a zero exit must not print one.
run_case() {
    local name=$1 scenario=$2 want_exit=$3 want_list=$4 want_msg=${5:-} want_reason=${6:-}
    local unwanted_reason=${7:-}
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

    # The exact command matters beyond the exit-code logic: --tags D makes
    # Timeshift tag the snapshot "daily", which its count-based pruning
    # handles; without it a snapshot is tagged "ondemand" and is never
    # pruned, and any --comments would also exempt it from pruning.
    grep -qx -- '--create --tags D --scripted' "$log" \
        || problems+=("timeshift --create --tags D --scripted was never called")
    [[ "$(grep -c -- '--create' "$log")" -eq 1 ]] \
        || problems+=("expected exactly one --create call, with exactly those arguments")

    if [[ "$want_list" == yes ]]; then
        grep -qx -- '--list' "$log" || problems+=("expected a 'timeshift --list' call, none made")
    else
        grep -qx -- '--list' "$log" && problems+=("unexpected 'timeshift --list' call")
    fi

    if [[ -n "$want_msg" ]]; then
        grep -qF -- "$want_msg" "$out" || problems+=("output missing: $want_msg")
    fi

    if [[ "$want_exit" -ne 0 ]]; then
        grep -q '^BACKUP-FAILED: .\+' "$out" \
            || problems+=("failure printed no BACKUP-FAILED: reason line")
        [[ "$(tail -n 1 "$out")" == BACKUP-FAILED:* ]] \
            || problems+=("BACKUP-FAILED: line is not the last line of output")
    else
        grep -q '^BACKUP-FAILED:' "$out" \
            && problems+=("success printed a BACKUP-FAILED: line")
    fi
    if [[ -n "$want_reason" ]]; then
        grep -F -- 'BACKUP-FAILED: ' "$out" | grep -qF -- "$want_reason" \
            || problems+=("reason missing: $want_reason")
    fi
    if [[ -n "$unwanted_reason" ]]; then
        grep -F -- 'BACKUP-FAILED: ' "$out" | grep -qF -- "$unwanted_reason" \
            && problems+=("reason should not contain: $unwanted_reason")
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
    generic-fail 1 no "" "Timeshift failed (exit code 1)"

run_case "2b exit 1, another Timeshift instance running -> 1, plain reason" \
    already-running 1 no "" "Timeshift is already running, so no backup was made. If the Timeshift window is open, close it." "exit code"

run_case "3  exit 134 + failure phrase, recent snapshot exists -> stays 134" \
    abort-with-failure-phrase 134 no \
    "Exit 134 but output shows a real failure" "write errors"

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
    segv-not-saved 139 no "" "crashed before saving the snapshot (exit code 139)"

run_case "8  exit 139, saved successfully AND failure phrase -> stays 139" \
    segv-saved-and-failure-phrase 139 no \
    "Exit 139 but output shows a real failure"

# Exit 0 isn't proof either: real runs have exited 0 and printed the saved
# line while writes to a failing drive were erroring.

run_case "9  exit 0, saved line, benign long_parse/remove-directory noise -> stays 0" \
    success-benign-noise 0 no

run_case "9b exit 0, saved line, rsync-log-changes 'File exists' -> stays 0" \
    success-log-file-exists 0 no

run_case "9c exit 0, saved line, No space left on device -> 1" \
    success-no-space 1 no \
    "treating as failure"

run_case "10 exit 0, saved line, write error beside \\r progress -> 1" \
    success-write-errors 1 no \
    "treating as failure" "Timeshift reported success, but its output shows write errors"

run_case "11 exit 0, saved line, Input/output error -> 1" \
    success-io-error-only 1 no \
    "treating as failure"

run_case "12 exit 0, saved line, Error creating directory -> 1" \
    success-mkdir-error 1 no \
    "treating as failure"

run_case "13 exit 134, saved line, write error -> stays 134" \
    abort-saved-write-error 134 no \
    "Exit 134 but output shows a real failure"

# Exit codes 126/127 from Timeshift (really the shell: missing or
# non-executable binary) must not reach the callers as-is: they read those
# as pkexec's "dialog dismissed" / "not authorized".
run_case "14 exit 127 (timeshift can't be started) -> 1, never 127" \
    exit-127 1 no "" "Timeshift could not be started (is it installed?). Exit code 127."

run_case "15 exit 126 (timeshift can't be executed) -> 1, never 126" \
    exit-126 1 no "" "Timeshift could not be run (exit code 126)"

# The realistic trigger: no timeshift binary at all (stdbuf exits 127).
missing_helper="$WORK/helper-missing-timeshift"
sed 's#/usr/bin/timeshift#/nonexistent/timeshift#g' "$HELPER" > "$missing_helper"
chmod +x "$missing_helper"
missing_tmp="$WORK/missing-tmp"; mkdir -p "$missing_tmp"
missing_exit=0
TMPDIR="$missing_tmp" "$missing_helper" > "$WORK/missing.out" 2>&1 || missing_exit=$?
if [[ "$missing_exit" -eq 1 ]] \
   && [[ "$(tail -n 1 "$WORK/missing.out")" == "BACKUP-FAILED: Timeshift could not be started"* ]]; then
    printf 'PASS  %s\n' "16 timeshift binary missing -> 1 with a clear reason"
    pass=$((pass + 1))
else
    printf 'FAIL  %s\n' "16 timeshift binary missing -> 1 with a clear reason"
    printf '        - exit %s, output:\n' "$missing_exit"; sed 's/^/        | /' "$WORK/missing.out"
    fail=$((fail + 1))
fi

# --- progress filter / log display (python unittest; Gtk cases skip
# themselves when PyGObject is unavailable) -------------------------------

for t in test_progress test_backup_runner test_status test_last_run test_tray_state test_app_tray test_retention test_config test_prompt_settings; do
    if python3 "$TESTS_DIR/$t.py" >"$WORK/$t.out" 2>&1; then
        printf 'PASS  unit tests: %s\n' "$t"
        pass=$((pass + 1))
    else
        printf 'FAIL  unit tests: %s\n' "$t"
        sed 's/^/        | /' "$WORK/$t.out"
        fail=$((fail + 1))
    fi
done

echo
echo "$pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
