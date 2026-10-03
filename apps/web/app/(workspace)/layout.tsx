import Link from "next/link";
import { Navigation } from "../../components/navigation";
import { AccountMenu } from "../../components/account-menu";
import { getCurrentUser } from "../../lib/server/auth";
export default async function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const state = await getCurrentUser();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/dashboard">
          <span className="brand-mark" aria-hidden="true">
            JH
          </span>
          <span>
            Job Hunter AI
            <span className="brand-subtitle">Поиск работы, по порядку</span>
          </span>
        </Link>
        <Navigation />
        <AccountMenu
          telegramLinked={
            state.kind === "authenticated"
              ? state.user.telegram_linked
              : undefined
          }
          label={
            state.kind === "authenticated"
              ? state.user.display_name || state.user.email || "Аккаунт"
              : state.kind === "unavailable"
                ? "Аккаунт"
                : null
          }
        />
      </aside>
      <main id="main">{children}</main>
    </div>
  );
}
