import { redirect } from "next/navigation";
import { AuthForm } from "../../components/auth-form";
import { AuthUnavailable } from "../../components/auth-unavailable";
import { getCurrentUser } from "../../lib/server/auth";
import { safeNext } from "../../lib/auth";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const next = safeNext(params.next);
  const state = await getCurrentUser();
  if (state.kind === "authenticated") redirect(next);
  if (state.kind === "unavailable") return <AuthUnavailable />;
  return (
    <main id="main" className="auth-shell">
      <AuthForm
        mode="register"
        next={next}
        registered={params.registered === "1"}
        localLogout={params.logout === "local"}
      />
    </main>
  );
}
