"use client";
import { useEffect, useRef, useState } from "react";
import { webRequest } from "../lib/client";
import { safeNext } from "../lib/auth";
import { errorMessage } from "../lib/errors";

type Challenge = {
  token: string;
  deep_link: string;
  code: string;
  expires_at: string;
  status: string;
};
const terminal: Record<string, string> = {
  expired: "Время подтверждения истекло. Начните заново.",
  cancelled: "Подтверждение отменено в Telegram.",
  conflict: "Этот Telegram уже связан с другим аккаунтом.",
  consumed: "Этот запрос уже завершён. Начните заново, если вход не состоялся.",
};
export function TelegramAuth({
  purpose,
  next = "/profile",
}: {
  purpose: "login" | "link";
  next?: string;
}) {
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const busy = useRef(false);
  const password = useRef<HTMLInputElement>(null);
  const loginButton = useRef<HTMLButtonElement>(null);
  const stopPolling = useRef<(() => void) | null>(null);
  const cancellation = useRef<Promise<void> | null>(null);
  const restoreFocus = useRef(false);
  const completing = useRef(false);
  const [finishing, setFinishing] = useState(false);
  const linking = purpose === "link";
  async function start(event?: React.FormEvent) {
    event?.preventDefault();
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError("");
    setMessage("");
    completing.current = false;
    setFinishing(false);
    // Reserve a tab synchronously while the click still has user activation.
    // Sever its opener before navigating to the backend-provided deep link.
    let telegramWindow: Window | null = null;
    if (!linking) {
      try {
        telegramWindow = window.open("about:blank", "_blank");
        if (telegramWindow) telegramWindow.opener = null;
      } catch {
        telegramWindow?.close();
        telegramWindow = null;
      }
    }
    try {
      // Let the cancel response clear the old HttpOnly cookie before creating a
      // replacement, so a late Set-Cookie cannot erase the fresh binding.
      if (cancellation.current) await cancellation.current;
      const result = await webRequest<Challenge>(
        "/api/auth/telegram/challenges",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            purpose,
            ...(linking ? { password: password.current?.value ?? "" } : {}),
          }),
        },
      );
      setChallenge(result);
      if (telegramWindow && !telegramWindow.closed) {
        try {
          telegramWindow.location.replace(result.deep_link);
        } catch {
          telegramWindow.close(); // Keep the valid challenge and fallback link.
        }
      }
    } catch (e) {
      telegramWindow?.close();
      setError(errorMessage(e));
    } finally {
      if (password.current) password.current.value = "";
      busy.current = false;
      setPending(false);
    }
  }
  function cancelLogin() {
    if (!challenge || linking || completing.current || cancellation.current)
      return;
    stopPolling.current?.(); // Synchronous: a late status cannot start completion.
    restoreFocus.current = true;
    setChallenge(null);
    setMessage("Вход отменён.");
    setError("");
    const job = webRequest("/api/auth/telegram/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose, token: challenge.token }),
      signal: AbortSignal.timeout(3000),
    })
      .then(
        () => undefined,
        () => undefined,
      )
      .finally(() => {
        if (cancellation.current === job) cancellation.current = null;
      });
    cancellation.current = job;
  }
  useEffect(() => {
    if (!challenge && restoreFocus.current) {
      restoreFocus.current = false;
      loginButton.current?.focus();
    }
  }, [challenge]);
  useEffect(() => {
    if (!challenge) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const stop = () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
    stopPolling.current = stop;
    async function poll() {
      if (!active) return;
      if (Date.parse(challenge!.expires_at) <= Date.now()) {
        setMessage(linking ? terminal.expired : "Срок подтверждения истёк.");
        setChallenge(null);
        return;
      }
      try {
        const init = {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ purpose, token: challenge!.token }),
          signal: controller.signal,
        };
        const result = await webRequest<{ status: string }>(
          "/api/auth/telegram/status",
          init,
        );
        if (!active) return;
        if (result.status === "approved") {
          completing.current = true;
          setFinishing(true);
          setMessage("Telegram подтвердил запрос. Завершаем…");
          await webRequest("/api/auth/telegram/complete", init);
          if (!active) return;
          stop();
          window.location.assign(
            safeNext(
              linking
                ? window.location.pathname + window.location.search
                : next,
            ),
          );
          return;
        }
        if (terminal[result.status]) {
          setMessage(
            !linking && result.status === "expired"
              ? "Срок подтверждения истёк."
              : !linking && result.status === "cancelled"
                ? "Вход отменён."
                : terminal[result.status],
          );
          setChallenge(null);
          return;
        }
        timer = setTimeout(poll, 2000);
      } catch (e) {
        if (active) {
          completing.current = false;
          setFinishing(false);
          setError(errorMessage(e));
          setChallenge(null);
        }
      }
    }
    // StrictMode's discarded effect clears its timer before polling begins.
    timer = setTimeout(poll, 0);
    return () => {
      stop();
      if (stopPolling.current === stop) stopPolling.current = null;
    };
  }, [challenge, purpose, next, linking]);
  return (
    <section
      className={`telegram-auth${linking ? "" : " telegram-login"}`}
      aria-busy={pending || finishing}
      aria-label={linking ? "Подключение Telegram" : "Вход через Telegram"}
    >
      {challenge ? (
        <div className={linking ? undefined : "telegram-waiting"}>
          {!linking && (
            <p className="telegram-waiting-title">
              Подтвердите вход в Telegram
            </p>
          )}
          <p role="status">{message || "Ждём подтверждения в Telegram…"}</p>
          <p className={linking ? undefined : "telegram-verification"}>
            Код сверки: <strong>{challenge.code}</strong>
          </p>
          {linking ? (
            <p>
              Подтверждайте только запрос с этим кодом, который вы начали сами.
            </p>
          ) : (
            <p className="telegram-verification">
              Подтверждайте только свой запрос с тем же кодом.
            </p>
          )}
          {!linking && (
            <p className="telegram-fallback-hint">Не открылся Telegram?</p>
          )}
          <a
            className={linking ? "back-link" : "telegram-fallback"}
            href={challenge.deep_link}
            target="_blank"
            rel="noreferrer noopener"
          >
            Открыть Telegram
          </a>
          {!linking && (
            <button
              className="telegram-cancel"
              type="button"
              onClick={cancelLogin}
              disabled={finishing}
            >
              Отменить вход
            </button>
          )}
        </div>
      ) : linking ? (
        <form onSubmit={start}>
          <label>
            Подтвердите пароль аккаунта
            <input
              ref={password}
              name="link_password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <button type="submit" disabled={pending}>
            {pending ? "Подождите…" : "Подключить Telegram"}
          </button>
        </form>
      ) : (
        <button
          ref={loginButton}
          className="telegram-login-button"
          type="button"
          onClick={() => start()}
          disabled={pending}
          aria-busy={pending}
        >
          <svg
            className="telegram-icon"
            aria-hidden="true"
            focusable="false"
            viewBox="0 0 24 24"
            fill="currentColor"
          >
            <path d="M21.5 3.7 18.3 20c-.24 1.15-.88 1.43-1.8.89l-4.88-3.6-2.35 2.27c-.26.26-.48.48-.98.48l.35-4.97L17.7 6.62c.4-.35-.09-.55-.62-.2L5.87 13.48 1.03 11.96c-1.05-.33-1.07-1.05.22-1.55L20.18 3.1c.87-.32 1.63.2 1.32.6Z" />
          </svg>
          <span>
            {pending
              ? "Подождите…"
              : message || error
                ? "Попробовать снова"
                : "Войти через Telegram"}
          </span>
        </button>
      )}
      {!challenge && message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
