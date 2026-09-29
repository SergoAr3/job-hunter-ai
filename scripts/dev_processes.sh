#!/usr/bin/env bash

# Defer shutdown only while a background job is being started and $! is saved.
# The caller provides cleanup() and keeps its EXIT trap installed throughout.
begin_dev_service_start() {
    shutdown_signal=0
    trap 'shutdown_signal=130' INT
    trap 'shutdown_signal=143' TERM
}

finish_dev_service_start() {
    trap cleanup INT TERM
    if (( shutdown_signal != 0 )); then
        exit "$shutdown_signal"
    fi
}

# Requires Bash job control (set -m): each PID passed here is also the PGID of
# one background service. Its subprocesses inherit that group.
stop_dev_process_groups() {
    local service_pid
    for service_pid in "$@"; do
        [[ -n "$service_pid" ]] || continue
        kill -TERM -- "-$service_pid" 2>/dev/null || true
    done
    for service_pid in "$@"; do
        [[ -n "$service_pid" ]] || continue
        wait "$service_pid" 2>/dev/null || true
    done
}
