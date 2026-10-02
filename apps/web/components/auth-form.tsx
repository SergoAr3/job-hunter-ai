"use client";
import Link from "next/link";
import { TelegramAuth } from "./telegram-auth";
import { useRef, useState } from "react";
import { safeNext } from "../lib/auth";
import { webRequest } from "../lib/client";
import { errorMessage, WebError } from "../lib/errors";
export function AuthForm({
  mode,
  next,
  registered = false,
  localLogout = false,
}: {
  mode: "login" | "register";
  next: string;
  registered?: boolean;
  localLogout?: boolean;
}) {
  const registering = mode === "register";
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const busy = useRef(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const password = String(data.get("password") ?? "");
    const length = [...password].length;
    if (length < 15 || length > 128) {
      setFields({ password: "Пароль должен содержать от 15 до 128 символов." });
      return;
    }
    busy.current = true;
    setPending(true);
    setError("");
    setFields({});
    try {
      await webRequest(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: data.get("email"),
          password,
          ...(registering
            ? { display_name: data.get("display_name") || null }
            : {}),
        }),
      });
      (form.elements.namedItem("password") as HTMLInputElement).value = "";
      window.location.assign(
        registering
          ? `/login?registered=1&next=${encodeURIComponent(safeNext(next))}`
          : safeNext(next),
      );
    } catch (e) {
      (form.elements.namedItem("password") as HTMLInputElement).value = "";
      setError(errorMessage(e));
      if (e instanceof WebError) setFields(e.fieldErrors ?? {});
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <div className="auth-card">
      <Link className="auth-brand" href="/">
        Job Hunter AI
      </Link>
      <h1>{registering ? "Создать аккаунт" : "Войти"}</h1>
      <p>Ваш профиль и вакансии — в одном месте.</p>
      {registered && (
        <p className="notice" role="status">
          Запрос на регистрацию обработан. Теперь можно попробовать войти.
        </p>
      )}
      {localLogout && (
        <p className="notice" role="status">
          Вы вышли на этом устройстве. Сервис не подтвердил завершение сессии;
          она может оставаться активной до истечения срока.
        </p>
      )}
      <form onSubmit={submit} aria-busy={pending}>
        {registering && (
          <label>
            Имя <span>(необязательно)</span>
            <input
              name="display_name"
              autoComplete="name"
              maxLength={255}
              aria-describedby={
                fields.display_name ? "display-name-error" : undefined
              }
            />
            {fields.display_name && (
              <span id="display-name-error">{fields.display_name}</span>
            )}
          </label>
        )}
        <label>
          Email
          <input
            name="email"
            type="email"
            autoComplete="email"
            required
            aria-invalid={!!fields.email}
            aria-describedby={fields.email ? "email-error" : undefined}
          />
          {fields.email && <span id="email-error">{fields.email}</span>}
        </label>
        <label>
          Пароль
          <input
            name="password"
            type="password"
            autoComplete={registering ? "new-password" : "current-password"}
            required
            aria-invalid={!!fields.password}
            aria-describedby="password-help password-error"
          />
        </label>
        <span id="password-help">
          От 15 до 128 символов. Пробелы сохраняются.
        </span>
        <span id="password-error">{fields.password}</span>
        {error && <p role="alert">{error}</p>}
        <button className="primary" type="submit" disabled={pending}>
          {pending ? "Подождите…" : registering ? "Создать аккаунт" : "Войти"}
        </button>
      </form>
      {!registering && <TelegramAuth purpose="login" next={next} />}
      <p>
        {registering ? "Уже есть аккаунт? " : "Ещё нет аккаунта? "}
        <Link
          href={`/${registering ? "login" : "register"}?next=${encodeURIComponent(safeNext(next))}`}
        >
          {registering ? "Войти" : "Зарегистрироваться"}
        </Link>
      </p>
    </div>
  );
}
