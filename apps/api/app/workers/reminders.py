"""python -m app.workers.reminders [--diagnostics]."""
import argparse
import asyncio
import json
import logging
import os
import signal
from time import monotonic

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal, engine
from app.services import reminders
from app.services.telegram_notifications import ConfigurationFailure, TelegramSender

logger = logging.getLogger("reminders")
POLL_SECONDS = 15
BATCH_SIZE = 5


def log_event(event, context=None, level=logging.INFO):
    logger.log(level, json.dumps({"event": event, **(context or {})}))


async def process_one(sessions, sender, clock=reminders.utc_now):
    with sessions() as session:
        claim = reminders.claim_due(session, clock())
    if claim is None:
        return False
    context = {"application_id": claim.application_id, "generation": str(claim.generation)}
    log_event("reminder_claim", context)
    with sessions() as session:
        destination = reminders.preflight(session, claim, clock())
    if destination is None:
        log_event("reminder_preflight_cancelled", context)
        return True
    started = monotonic()
    outcome = await sender.send(destination, claim.application_id)
    # A known result may be persisted again after DB failure; never send again here.
    for attempt in range(3):
        try:
            with sessions() as session:
                completion = reminders.complete(session, claim, outcome, clock())
            break
        except SQLAlchemyError:
            log_event("reminder_completion_db_unavailable", context, logging.WARNING)
            if attempt == 2:
                raise
            await asyncio.sleep(1)
    event = "stale_completion"
    if completion is not None:
        event = {"sent": "sent", "pending": "retry", "failed": "failed"}[completion.delivery_state]
        if completion.error_code == "DELIVERY_UNCERTAIN":
            event = "uncertain"
    log_event("reminder_" + event,
              {**context, "error_code": completion.error_code if completion else None,
               "latency_ms": int((monotonic() - started) * 1000)})
    if outcome.kind == "configuration":
        raise ConfigurationFailure("Reminder sender credentials unavailable")
    return True


async def run():
    sender = TelegramSender(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("WEB_PUBLIC_ORIGIN", ""),
                            os.getenv("APP_ENV", "production"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    # Prevent an accidental second live worker in local/production startup.
    # Claim concurrency is independently supported and tested in domain services.
    guard = engine.connect()
    try:
        if engine.dialect.name != "postgresql":
            raise ConfigurationFailure("Live reminder worker requires PostgreSQL")
        if not guard.scalar(text("SELECT pg_try_advisory_lock(746391820)")):
            raise ConfigurationFailure("Reminder worker is already running")
        guard.commit()
        while not stop.is_set():
            guard.scalar(text("SELECT 1"))
            guard.commit()
            with SessionLocal() as session:
                recovered = reminders.recover_expired(session)
                counts = reminders.diagnostics(session)
            if recovered:
                log_event("reminder_lease_recovery", {"count": recovered, "error_code": "DELIVERY_UNCERTAIN"})
            log_event("reminder_heartbeat", counts)
            for _ in range(BATCH_SIZE):
                if stop.is_set() or not await process_one(SessionLocal, sender):
                    break
                # Conservative pacing, including alongside normal Bot replies.
                if not stop.is_set():
                    try:
                        await asyncio.wait_for(stop.wait(), 1)
                    except TimeoutError:
                        pass
            try:
                await asyncio.wait_for(stop.wait(), POLL_SECONDS)
            except TimeoutError:
                pass
    finally:
        try:
            if not guard.invalidated and engine.dialect.name == "postgresql":
                guard.execute(text("SELECT pg_advisory_unlock(746391820)"))
                guard.commit()
        finally:
            guard.close()
            await sender.close()
            engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # HTTP client logging can include token-bearing URLs.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    if args.diagnostics:
        with SessionLocal() as session:
            print(json.dumps(reminders.diagnostics(session)))
        return
    try:
        asyncio.run(run())
    except (ConfigurationFailure, SQLAlchemyError):
        logger.error("reminder_worker_stopped_configuration_or_database_unavailable")
        raise SystemExit(78) from None


if __name__ == "__main__":
    main()
