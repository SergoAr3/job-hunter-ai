#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
# shellcheck disable=SC1091
source scripts/dev_processes.sh

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
if ! command -v npm >/dev/null 2>&1; then
    echo "Missing npm. Install Node.js 22.12+ (which includes npm) to run the Web app." >&2
    exit 1
fi
if [[ ! -x apps/web/node_modules/.bin/next ]]; then
    echo "Missing Web dependencies. Run: cd apps/web && npm install" >&2
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

# One server-only credential shared by API and Bot for this dev run.
# It is never exported to the Web process or written to .env.
dev_bot_api_service_token="$(
    set +u
    source .env
    if [[ -n "${BOT_API_SERVICE_TOKEN:-}" ]]; then
        printf '%s' "$BOT_API_SERVICE_TOKEN"
    else
        apps/api/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
    fi
)"

run_api() (
    cd apps/api
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -a
    # shellcheck disable=SC1091
    source ../../.env
    set +a
    export AUTH_MAIL_DELIVERY=capture
    export AUTH_MAIL_SINK_DIR="${AUTH_MAIL_SINK_DIR:-/tmp/job-hunter-auth-mail}"
    export WEB_PUBLIC_ORIGIN=http://127.0.0.1:3100
    APP_ENV=development BOT_API_SERVICE_TOKEN="$dev_bot_api_service_token" exec python -m uvicorn app.main:app --reload --host 127.0.0.1 --no-proxy-headers
)

run_bot() (
    cd apps/bot
    # shellcheck disable=SC1091
    source .venv/bin/activate
    set -a
    # shellcheck disable=SC1091
    source ../../.env
    set +a
    APP_ENV=development API_BASE_URL=http://127.0.0.1:8000 BOT_API_SERVICE_TOKEN="$dev_bot_api_service_token" exec python ../../scripts/bot_dev.py
)

run_web() (
    cd apps/web
    unset BOT_API_SERVICE_TOKEN

    export APP_ENV=development

    export WEB_PUBLIC_ORIGIN=http://127.0.0.1:3100
    export API_BASE_URL=http://127.0.0.1:8000
    exec npm run dev -- -p 3100
)

run_reminder_worker() (
    cd apps/api
    source .venv/bin/activate
    set -a
    source ../../.env
    set +a
    export APP_ENV=development
    export WEB_PUBLIC_ORIGIN=http://127.0.0.1:3100
    exec python -m app.workers.reminders
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

wait_for_web() {
    local attempt
    for attempt in {1..30}; do
        if ! kill -0 "$web_pid" 2>/dev/null; then
            wait "$web_pid" || true
            return 1
        fi
        if curl --silent --fail --max-time 1 http://127.0.0.1:3100/login | grep '<title>Job Hunter AI</title>' >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

cleanup() {
    local exit_status=$?
    trap - EXIT INT TERM

    dev_log "Shutting down..."
    stop_dev_process_groups "$api_pid" "$bot_pid" "$web_pid" "$reminder_pid"

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
web_pid=""
reminder_pid=""
# Give each managed background job its own process group. $! is its PGID;
# killing only the shell wrapper would leave service grandchildren running.
set -m
trap cleanup EXIT INT TERM

dev_log "Starting API..."
begin_dev_service_start
run_api > >(prefix_logs API) 2>&1 &
api_pid=$!
finish_dev_service_start
if ! wait_for_api; then
    dev_error "API exited or did not become ready"
    exit 1
fi

dev_log "Starting BOT..."
begin_dev_service_start
run_bot > >(prefix_logs BOT) 2>&1 &
bot_pid=$!
finish_dev_service_start

sleep 1
if ! kill -0 "$bot_pid" 2>/dev/null; then
    wait "$bot_pid" || true
    dev_error "BOT exited during startup"
    exit 1
fi

dev_log "Starting Web..."
begin_dev_service_start
run_web > >(prefix_logs WEB) 2>&1 &
web_pid=$!
finish_dev_service_start

if ! wait_for_web; then
    dev_error "Web exited or did not become ready (check whether port 3100 is already in use)"
    exit 1
fi

dev_log "Starting reminder worker..."
begin_dev_service_start
run_reminder_worker > >(prefix_logs REMINDERS) 2>&1 &
reminder_pid=$!
finish_dev_service_start
sleep 1
if ! kill -0 "$reminder_pid" 2>/dev/null; then
    wait "$reminder_pid" || true
    dev_error "Reminder worker exited during startup (check for an existing worker)"
    exit 1
fi

dev_log "Application is running"
dev_log "API: http://127.0.0.1:8000"
dev_log "Swagger: http://127.0.0.1:8000/docs"
dev_log "Web: http://127.0.0.1:3100/login"

while true; do
    if ! kill -0 "$reminder_pid" 2>/dev/null; then
        wait "$reminder_pid" || true
        dev_error "Reminder worker exited unexpectedly"
        exit 1
    fi
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
    if ! kill -0 "$web_pid" 2>/dev/null; then
        wait "$web_pid" || true
        dev_error "Web exited unexpectedly"
        exit 1
    fi
    sleep 1
done
