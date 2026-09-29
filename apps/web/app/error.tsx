"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <div className="notice error" role="alert">
      <h1>Не удалось загрузить страницу</h1>
      <p>Повторите попытку.</p>
      <button onClick={reset}>Повторить</button>
    </div>
  );
}
