import Link from "next/link";
import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Job Hunter AI",
  description: "Персональный поиск работы",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <a className="skip-link" href="#main">
          К содержимому
        </a>
        <div className="app-shell">
          <aside className="sidebar">
            <Link className="brand" href="/discover">
              <span className="brand-mark" aria-hidden="true">
                JH
              </span>
              <span>
                Job Hunter AI
                <span className="brand-subtitle">Поиск работы, по порядку</span>
              </span>
            </Link>
            <nav aria-label="Главная навигация">
              <span className="nav-caption">Рабочее пространство</span>
              <Link href="/discover">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="10.8" cy="10.8" r="6.3" />
                  <path d="m15.5 15.5 4.2 4.2" />
                </svg>
                Поиск вакансий
              </Link>
            </nav>
            <div className="local-label">
              <span className="local-dot" /> Локальный режим
              <p>Ваши вакансии — в одном месте.</p>
            </div>
          </aside>
          <main id="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
