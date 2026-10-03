"""Bounded, process-local fixed-window auth budgets; no forwarded-header trust."""
import hashlib
import math
import time
from threading import Lock
from app.services.auth import AuthError


class Limiter:
    def __init__(self, clock=time.monotonic, capacity=10000):
        self.clock, self.capacity = clock, capacity
        self.buckets = {}
        self.lock = Lock()

    def check(self, budgets):
        with self.lock:
            now = self.clock()
            self.buckets = {key: value for key, value in self.buckets.items() if value[1] > now}
            missing = {key for key, _, _ in budgets if key not in self.buckets}
            retry = 0
            for key, limit, window in budgets:
                count, end = self.buckets.get(key, (0, now + window))
                if count >= limit:
                    retry = max(retry, math.ceil(end - now))
            if len(self.buckets) + len(missing) > self.capacity:
                retry = max(retry, 60)
            if retry:
                error = AuthError("rate_limited", 429)
                error.retry_after = str(max(1, retry))
                raise error
            for key, _, window in budgets:
                count, end = self.buckets.get(key, (0, now + window))
                self.buckets[key] = (count + 1, end)


limiter = Limiter()


def limit_request(request, action, email=None):
    peer = request.client.host if request.client else "unknown"
    # BFF traffic shares its API peer budget. Never trust client-supplied XFF.
    limits = {"login": (60, 900), "mail": (20, 3600), "register": (20, 3600),
              "consume": (60, 900), "telegram-create": (30, 600), "telegram-poll": (600, 600)}
    count, window = limits[action]
    budgets = [(f"{action}:ip:{peer}", count, window)]
    if email is not None:
        digest = hashlib.sha256(email.lower().encode("ascii")).hexdigest()
        budgets.append((f"{'mail' if action in {'mail','register'} else action}:email:{digest}", 5 if action in {"mail","register"} else 10, 3600 if action in {"mail","register"} else 900))
    limiter.check(budgets)


def limit_link(user_id):
    # Password reauthentication is expensive even when many IPs attack one user.
    limiter.check([(f"telegram-link:user:{user_id}", 5, 600)])
