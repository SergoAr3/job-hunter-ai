import { redirect } from "next/navigation";
import { loginUrl } from "../../../../lib/auth";
import { pageAccess } from "../../../../lib/server/page-access";
import { AuthUnavailable } from "../../../../components/auth-unavailable";
import Link from "next/link";
import { ApplicationStatusDetail } from "../../../../components/application-status-detail";
import { getApplication } from "../../../../lib/server/api";
import { errorMessage, WebError } from "../../../../lib/errors";
import { notFound } from "next/navigation";
import { safeApplicationReturn } from "../../../../lib/application-return";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ applicationId: string }>;
  searchParams: Promise<{ from?: string | string[] }>;
}) {
  const { applicationId } = await params;
  const backUrl = safeApplicationReturn((await searchParams).from);
  const detailUrl = `/applications/${encodeURIComponent(applicationId)}?from=${encodeURIComponent(backUrl)}`;
  const identity = await pageAccess(detailUrl);
  if (!identity) return <AuthUnavailable />;
  let detail;
  try {
    detail = await getApplication(applicationId);
  } catch (error) {
    if (error instanceof WebError && error.code === "unauthenticated")
      redirect(loginUrl(detailUrl));
    if (error instanceof WebError && error.code === "APPLICATION_NOT_FOUND")
      notFound();
    return (
      <div className="notice error" role="alert">
        <h1>Не удалось открыть вакансию</h1>
        <p>{errorMessage(error)}</p>
        <Link href={detailUrl}>Повторить</Link>
        <Link className="back-link" href={backUrl}>
          {backUrl.startsWith("/discover?")
            ? "← К результатам поиска"
            : "← К моим вакансиям"}
        </Link>
      </div>
    );
  }
  return (
    <>
      <Link className="back-link" href={backUrl}>
        {backUrl.startsWith("/discover?")
          ? "← К результатам поиска"
          : "← К моим вакансиям"}
      </Link>
      <ApplicationStatusDetail
        key={detail.application.id}
        detail={detail}
        matchApplicationId={detail.application.id}
        telegramLinked={identity.user?.telegram_linked}
      />
    </>
  );
}
