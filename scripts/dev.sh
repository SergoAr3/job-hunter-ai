#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and configure it first." >&2
    exit 1
fi

if [[ ! -f apps/api/.venv/bin/activate || ! -f apps/bot/.venv/bin/activate ]]; then
    echo "Missing API or BOT virtual environment. Create both project .venv directories first." >&2
    exit 1
fi
if ! apps/bot/.venv/bin/python -c 'import watchfiles' >/dev/null 2>&1; then
    echo "Missing BOT dev dependency. Run: cd apps/bot && .venv/bin/python -m pip install -r requirements-dev.txt" >&2
    exit 1
fi

dev_log() {
    printf '[DEV] %s\n' "$*"
}

dev_error() {
    printf '[DEV] ERROR: %s\n' "$*" >&2
}

prefix_logs() {
    local service_name=$1
    sed -u "s/^/[$service_name] /"
}

wait_for_postgres() {
    local attempt
    for attempt in {1..30}; do
        if docker compose exec -T db pg_isready -U job_hunter -d job_hunter >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

run_migrations() (
    cd apps/api
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -a
    # shellcheck disable=SC1091
    source ../../.env
    set +a
    exec python -m alembic upgrade head
)

run_api() (
    cd apps/api
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -a
    # shellcheck disable=SC1091
    source ../../.env
    set +a
    exec python -m uvicorn app.main:app --reload
)

run_bot() (
    cd apps/bot
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -a
    # shellcheck disable=SC1091
    source ../../.env
    set +a
    API_BASE_URL=http://127.0.0.1:8000 exec python ../../scripts/bot_dev.py
)

wait_for_api() {
    local attempt
    for attempt in {1..30}; do
        if ! kill -0 "$api_pid" 2>/dev/null; then
            wait "$api_pid" || true
            return 1
        fi
        if curl --silent --fail --max-time 1 http://127.0.0.1:8000/health >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

cleanup() {
    local exit_status=$?
    local service_pid
    trap - EXIT INT TERM

    dev_log "Shutting down..."
    for service_pid in "$api_pid" "$bot_pid"; do
        [[ -n "$service_pid" ]] || continue
        kill -TERM "$service_pid" 2>/dev/null || true
    done
    for service_pid in "$api_pid" "$bot_pid"; do
        [[ -n "$service_pid" ]] || continue
        wait "$service_pid" 2>/dev/null || true
    done

    exit "$exit_status"
}

dev_log "Starting PostgreSQL..."
if ! docker compose up -d db; then
    dev_error "failed to start PostgreSQL"
    exit 1
fi
if ! wait_for_postgres; then
    dev_error "PostgreSQL did not become ready"
    exit 1
fi
dev_log "PostgreSQL ready"

dev_log "Running migrations..."
if ! run_migrations 2>&1 | prefix_logs API; then
    dev_error "migrations failed"
    exit 1
fi
dev_log "Migrations complete"

api_pid=""
bot_pid=""
trap cleanup EXIT INT TERM

dev_log "Starting API..."
run_api > >(prefix_logs API) 2>&1 &
api_pid=$!
if ! wait_for_api; then
    dev_error "API exited or did not become ready"
    exit 1
fi

dev_log "Starting BOT..."
run_bot > >(prefix_logs BOT) 2>&1 &
bot_pid=$!

sleep 1
if ! kill -0 "$bot_pid" 2>/dev/null; then
    wait "$bot_pid" || true
    dev_error "BOT exited during startup"
    exit 1
fi

dev_log "Application is running"
dev_log "API: http://127.0.0.1:8000"
dev_log "Swagger: http://127.0.0.1:8000/docs"

while true; do
    if ! kill -0 "$api_pid" 2>/dev/null; then
        wait "$api_pid" || true
        dev_error "API exited unexpectedly"
        exit 1
    fi
    if ! kill -0 "$bot_pid" 2>/dev/null; then
        wait "$bot_pid" || true
        dev_error "BOT exited unexpectedly"
        exit 1
    fi
    sleep 1
done
