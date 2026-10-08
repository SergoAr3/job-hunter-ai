#!/usr/bin/env bash

set -euo pipefail

script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
project_root="$(cd "$(dirname "$script_path")/../.." && pwd)"

if [[ ${1:-} == child ]]; then
    sleep 30 &
    child_pid=$!
    printf '%s\n' "$child_pid" > "$2.grandchild"
    wait "$child_pid"
    exit
fi

if [[ ${1:-} == runner ]]; then
    # Use the same job-control launch pattern and cleanup function as dev.sh.
    # shellcheck disable=SC1091
    source "$project_root/scripts/dev_processes.sh"
    set -m
    api_pid=""
    bot_pid=""
    web_pid=""
    reminder_pid=""
    cleanup() {
        local exit_status=$?
        trap - EXIT INT TERM
        stop_dev_process_groups "$api_pid" "$bot_pid" "$web_pid" "$reminder_pid"
        exit "$exit_status"
    }
    trap cleanup EXIT INT TERM
    for service in api bot web reminder; do
        begin_dev_service_start
        (
            bash "$script_path" child "$2/$service" &
            printf '%s\n' "$!" > "$2/$service.child"
            wait "$!"
        ) > >(sed -u "s/^/[$service] /") 2>&1 &
        if [[ ${3:-} == startup_race && $service == api ]]; then
            # Test-only hook: wait for the nested process, then signal this
            # runner before it has copied $! to the managed PGID variable.
            for attempt in {1..100}; do
                [[ -s "$2/api.grandchild" ]] && break
                sleep 0.01
            done
            [[ -s "$2/api.grandchild" ]] || exit 1
            kill -TERM "$$"
        fi
        service_pid=$!
        printf '%s\n' "$service_pid" > "$2/$service.group"
        case "$service" in
            api) api_pid=$service_pid ;;
            bot) bot_pid=$service_pid ;;
            web) web_pid=$service_pid ;;
            reminder) reminder_pid=$service_pid ;;
        esac
        finish_dev_service_start
    done
    while true; do
        for service_pid in "$api_pid" "$bot_pid" "$web_pid" "$reminder_pid"; do
            kill -0 "$service_pid" 2>/dev/null || exit 1
        done
        sleep 0.1
    done
fi

test_dir="$(mktemp -d)"
runner_pid=""
finish() {
    if [[ -n "$runner_pid" ]]; then
        kill -TERM "$runner_pid" 2>/dev/null || true
        wait "$runner_pid" 2>/dev/null || true
    fi
    rm -rf "$test_dir"
}
trap finish EXIT

wait_for_children() {
    local attempt service ready
    for attempt in {1..100}; do
        ready=true
        for service in api bot web reminder; do
            if [[ ! -s "$1/$service.group" || ! -s "$1/$service.child" || ! -s "$1/$service.grandchild" ]]; then
                ready=false
                break
            fi
        done
        if [[ $ready == true ]]; then
            for service in api bot web reminder; do
                kill -0 -- "-$(cat "$1/$service.group")" 2>/dev/null || {
                    echo "Dummy service PID is not its process group ID" >&2
                    return 1
                }
            done
            return 0
        fi
        sleep 0.1
    done
    echo "Dummy services did not start" >&2
    return 1
}

assert_stopped() {
    local scenario_dir=$1
    shift
    local attempt service pid group all_stopped
    for attempt in {1..100}; do
        all_stopped=true
        for service in "$@"; do
            for suffix in group child grandchild; do
                pid="$(cat "$scenario_dir/$service.$suffix")"
                if kill -0 "$pid" 2>/dev/null; then
                    all_stopped=false
                fi
            done
            group="$(cat "$scenario_dir/$service.group")"
            if kill -0 -- "-$group" 2>/dev/null; then
                all_stopped=false
            fi
        done
        [[ $all_stopped == true ]] && return 0
        sleep 0.1
    done
    echo "A dummy service process survived cleanup" >&2
    return 1
}

set -m
for scenario in runner_sigterm runner_sigint child_failure startup_race; do
    scenario_dir="$test_dir/$scenario"
    mkdir -p "$scenario_dir"
    bash "$script_path" runner "$scenario_dir" "$scenario" &
    runner_pid=$!
    if [[ $scenario == startup_race ]]; then
        wait "$runner_pid" 2>/dev/null || true
        runner_pid=""
        [[ -s "$scenario_dir/api.group" && ! -e "$scenario_dir/bot.group" && ! -e "$scenario_dir/web.group" ]]
        assert_stopped "$scenario_dir" api
        printf '%s: pending signal stopped the nested dummy group before BOT startup\n' "$scenario"
        continue
    fi
    wait_for_children "$scenario_dir"
    if [[ $scenario == runner_sigterm ]]; then
        kill -TERM "$runner_pid"
    elif [[ $scenario == runner_sigint ]]; then
        kill -INT "$runner_pid"
    else
        kill -TERM "$(cat "$scenario_dir/api.grandchild")"
    fi
    wait "$runner_pid" 2>/dev/null || true
    runner_pid=""
    assert_stopped "$scenario_dir" api bot web reminder
    printf '%s: all dummy service processes stopped\n' "$scenario"
done
