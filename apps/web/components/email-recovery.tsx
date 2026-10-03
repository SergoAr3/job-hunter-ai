"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";

export function EmailRequestForm({
  purpose,
  email = "",
}: {
  purpose: "verify" | "reset";
  email?: string;
}) {
  const [pending, setPending] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const busy = useRef(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const data = new FormData(event.currentTarget);
    busy.current = true;
    setPending(true);
    setError("");
    setMessage("");
    try {
      await webRequest(
        `/api/auth/${purpose === "verify" ? "email/resend" : "password/forgot"}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: data.get("email") }),
        },
      );
      setMessage(
        purpose === "verify"
          ? "Если аккаунт существует и требует подтверждения, письмо отправлено."
          : "Если аккаунт подходит для восстановления, письмо отправлено.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <form onSubmit={submit} aria-busy={pending}>
      <label>
        Email
        <input
          name="email"
          type="email"
          autoComplete="email"
          required
          defaultValue={email}
        />
      </label>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={pending}>
        {pending
          ? "Подождите…"
          : purpose === "verify"
            ? "Отправить письмо повторно"
            : "Отправить ссылку"}
      </button>
    </form>
  );
}

export function EmailRecovery({
  mode,
}: {
  mode: "verify" | "forgot" | "reset";
}) {
  const proof = useRef<string | null | undefined>(undefined);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const recoveryForm = useRef<HTMLFormElement | null>(null);
  const [token, setToken] = useState<string | null>(null),
    [ready, setReady] = useState(false),
    [pending, setPending] = useState(false),
    [success, setSuccess] = useState(false),
    [error, setError] = useState("");
  const busy = useRef(false);
  useEffect(() => {
    if (mode === "forgot") return;
    let active = true;
    function invalidateAttempt() {
      ++generation.current;
      request.current?.abort();
      request.current = null;
      busy.current = false;
    }
    function beginAttempt(readFragment: boolean) {
      if (readFragment) {
        const value = new URLSearchParams(window.location.hash.slice(1)).getAll(
          "token",
        );
        proof.current =
          value.length === 1 && /^[A-Za-z0-9_-]{43}$/.test(value[0])
            ? value[0]
            : null;
        // replaceState does not emit hashchange. Preserve the route/query;
        // StrictMode replay retains the captured proof only in memory.
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      }
      invalidateAttempt();
      const attempt = generation.current;
      const incoming = proof.current ?? null;
      recoveryForm.current?.reset();
      queueMicrotask(() => {
        if (!active || generation.current !== attempt) return;
        setToken(incoming);
        setReady(true);
        setPending(false);
        setSuccess(false);
        setError("");
      });
    }
    beginAttempt(proof.current === undefined);
    const hashchange = () => beginAttempt(true);
    window.addEventListener("hashchange", hashchange);
    return () => {
      active = false;
      window.removeEventListener("hashchange", hashchange);
      invalidateAttempt();
    };
  }, [mode]);
  async function consume(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current || !token || proof.current !== token) return;
    const form = event.currentTarget,
      data = new FormData(form);
    const password = String(data.get("password") ?? "");
    if (
      mode === "reset" &&
      (password !== data.get("confirmation") ||
        [...password].length < 15 ||
        [...password].length > 128)
    ) {
      setError(
        password !== data.get("confirmation")
          ? "Пароли не совпадают."
          : "Пароль должен содержать от 15 до 128 символов.",
      );
      return;
    }
    busy.current = true;
    const attempt = generation.current;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError("");
    try {
      await webRequest(
        `/api/auth/${mode === "verify" ? "email/verify" : "password/reset"}`,
        {
          method: "POST",
          signal: controller.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token,
            ...(mode === "reset" ? { password } : {}),
          }),
        },
      );
      if (generation.current !== attempt) return;
      proof.current = null;
      setToken(null);
      setSuccess(true);
    } catch (e) {
      if (generation.current === attempt) setError(errorMessage(e));
    } finally {
      if (generation.current === attempt) {
        if (mode === "reset") form.reset();
        request.current = null;
        busy.current = false;
        setPending(false);
      }
    }
  }
  return (
    <div className="auth-card">
      <Link className="auth-brand" href="/">
        Job Hunter AI
      </Link>
      <h1>
        {mode === "verify"
          ? "Подтвердить email"
          : mode === "forgot"
            ? "Забыли пароль?"
            : "Новый пароль"}
      </h1>
      {mode === "forgot" ? (
        <>
          <p>Отправим ссылку для восстановления, если аккаунт подходит.</p>
          <EmailRequestForm purpose="reset" />
        </>
      ) : success ? (
        <p role="status">
          {mode === "verify"
            ? "Email подтверждён. Теперь можно войти."
            : "Пароль изменён. Все прежние сессии завершены. Войдите снова."}
        </p>
      ) : (
        <>
          {ready && !token && (
            <p role="alert">Ссылка недействительна. Запросите новое письмо.</p>
          )}
          {token && (
            <form ref={recoveryForm} onSubmit={consume} aria-busy={pending}>
              {mode === "reset" ? (
                <>
                  <label>
                    Новый пароль
                    <input
                      name="password"
                      type="password"
                      autoComplete="new-password"
                      required
                      aria-describedby="reset-password-help"
                    />
                  </label>
                  <label>
                    Повторите пароль
                    <input
                      name="confirmation"
                      type="password"
                      autoComplete="new-password"
                      required
                    />
                  </label>
                  <span id="reset-password-help">
                    От 15 до 128 символов. Пробелы сохраняются.
                  </span>
                </>
              ) : (
                <p>Подтвердите email по ссылке из вашего письма.</p>
              )}
              {error && <p role="alert">{error}</p>}
              <button className="primary" type="submit" disabled={pending}>
                {pending
                  ? "Подождите…"
                  : mode === "verify"
                    ? "Подтвердить email"
                    : "Сохранить пароль"}
              </button>
            </form>
          )}
          {mode === "verify" && <EmailRequestForm purpose="verify" />}
          {mode === "reset" && (
            <p>
              <Link href="/forgot-password">Запросить новую ссылку</Link>
            </p>
          )}
        </>
      )}
      <p>
        <Link href="/login">Вернуться ко входу</Link>
      </p>
    </div>
  );
}
