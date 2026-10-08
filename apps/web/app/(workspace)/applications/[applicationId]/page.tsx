import { redirect } from "next/navigation";
import { loginUrl } from "../../../../lib/auth";
import { pageAccess } from "../../../../lib/server/page-access";
import { AuthUnavailable } from "../../../../components/auth-unavailable";
import Link from "next/link";
import { ApplicationStatusDetail } from "../../../../components/application-status-detail";
import { getApplication } from "../../../../lib/server/api";
import { errorMessage, WebError } from "../../../../lib/errors";
import { notFound } from "next/navigation";
import { safeApplicationsReturn } from "../../../../lib/applications";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ applicationId: string }>;
  searchParams: Promise<{ from?: string | string[] }>;
}) {
  const { applicationId } = await params;
  const identity = await pageAccess(
    `/applications/${encodeURIComponent(applicationId)}`,
  );
  if (!identity) return <AuthUnavailable />;
  const backUrl = safeApplicationsReturn((await searchParams).from);
  let detail;
  try {
    detail = await getApplication(applicationId);
  } catch (error) {
    if (error instanceof WebError && error.code === "unauthenticated")
      redirect(loginUrl(`/applications/${applicationId}`));
    if (error instanceof WebError && error.code === "APPLICATION_NOT_FOUND")
      notFound();
    return (
      <div className="notice error" role="alert">
        <h1>Не удалось открыть вакансию</h1>
        <p>{errorMessage(error)}</p>
        <Link href={`/applications/${encodeURIComponent(applicationId)}`}>
          Повторить
        </Link>
      </div>
    );
  }
  return (
    <>
      <Link className="back-link" href={backUrl}>
        ← К моим вакансиям
      </Link>
      <ApplicationStatusDetail
        detail={detail}
        telegramLinked={identity.user?.telegram_linked}
      />
    </>
  );
}
