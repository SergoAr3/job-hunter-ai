import Link from "next/link";
import type { Metadata } from "next";
import "./globals.css";
import { Navigation } from "../components/navigation";
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
            <Navigation />
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
