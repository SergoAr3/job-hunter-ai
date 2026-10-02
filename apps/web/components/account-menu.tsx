"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { TelegramAuth } from "./telegram-auth";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";
export function AccountMenu({
  label,
  telegramLinked,
}: {
  label: string | null;
  telegramLinked?: boolean;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const busy = useRef(false);
  async function logout() {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError("");
    try {
      const result = await webRequest<{ revocation_confirmed: boolean }>(
        "/api/auth/logout",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      window.location.assign(
        result.revocation_confirmed ? "/login" : "/login?logout=local",
      );
    } catch (e) {
      setError(errorMessage(e));
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <div className="account-menu">
      {label ? (
        <>
          <span>{label}</span>
          {telegramLinked === true ? (
            <span>Telegram подключён</span>
          ) : telegramLinked === false ? (
            <TelegramAuth purpose="link" />
          ) : null}
          <button onClick={logout} disabled={pending}>
            {pending ? "Выходим…" : "Выйти"}
          </button>
        </>
      ) : (
        <>
          <Link href="/login">Войти</Link>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
