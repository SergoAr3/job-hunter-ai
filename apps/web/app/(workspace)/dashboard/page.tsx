import Link from "next/link";
import { redirect } from "next/navigation";
import { Dashboard } from "../../../components/dashboard";
import { AuthUnavailable } from "../../../components/auth-unavailable";
import { pageAccess } from "../../../lib/server/page-access";
import {
  getApplicationsSummary,
  listApplications,
} from "../../../lib/server/api";
import { getProfile } from "../../../lib/server/profile";
import { WebError } from "../../../lib/errors";
import { loginUrl } from "../../../lib/auth";

export const dynamic = "force-dynamic";
export default async function Page() {
  const identity = await pageAccess("/dashboard");
  if (!identity) return <AuthUnavailable />;
  let data;
  try {
    const [summary, recent, profile] = await Promise.all([
      getApplicationsSummary(),
      listApplications({ q: "", status: null, sort: "newest", offset: 0 }),
      getProfile().catch((error) => {
        if (error instanceof WebError && error.code === "profile_missing")
          return null;
        throw error;
      }),
    ]);
    data = { summary, recent: recent.items, profile };
  } catch (error) {
    if (error instanceof WebError && error.code === "unauthenticated")
      redirect(loginUrl("/dashboard"));
    if (error instanceof WebError && error.code === "auth_unavailable")
      return <AuthUnavailable />;
    return (
      <div className="notice error" role="alert">
        <h1>Обзор временно недоступен</h1>
        <p>Не удалось загрузить данные. Повторите попытку позже.</p>
        <Link className="button-link" href="/dashboard">
          Повторить
        </Link>
      </div>
    );
  }
  return (
    <Dashboard {...data} telegramLinked={identity.user?.telegram_linked} />
  );
}
