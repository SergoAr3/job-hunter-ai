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
        {children}
      </body>
    </html>
  );
}
