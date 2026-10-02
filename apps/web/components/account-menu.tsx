"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { webRequest } from "../lib/client";
import { errorMessage } from "../lib/errors";
export function AccountMenu({ label }: { label: string | null }) {
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
          <button onClick={logout} disabled={pending}>
            {pending ? "Выходим…" : "Выйти"}
          </button>
        </>
      ) : (
        <>
          <span>Локальный dev-аккаунт</span>
          <Link href="/login">Войти по email</Link>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
