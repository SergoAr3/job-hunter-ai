import Link from "next/link";
export default function NotFound() {
  return (
    <div className="state">
      <h1>Вакансия не найдена</h1>
      <p>Эта запись недоступна у настроенного пользователя.</p>
      <Link href="/applications">К моим вакансиям</Link>
    </div>
  );
}
