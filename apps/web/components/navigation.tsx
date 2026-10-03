"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export function Navigation() {
  const pathname = usePathname();
  return (
    <nav aria-label="Главная навигация">
      <span className="nav-caption">Рабочее пространство</span>
      <Link
        href="/profile"
        aria-current={pathname === "/profile" ? "page" : undefined}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="8" r="3.5" />
          <path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2" />
        </svg>
        Профиль
      </Link>
      <Link
        href="/discover"
        aria-current={pathname === "/discover" ? "page" : undefined}
      >
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
      <Link
        href="/applications"
        aria-current={
          pathname === "/applications" || pathname.startsWith("/applications/")
            ? "page"
            : undefined
        }
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="6" width="18" height="15" rx="2" />
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" />
        </svg>
        Мои вакансии
      </Link>
    </nav>
  );
}
