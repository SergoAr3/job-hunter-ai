import Link from "next/link";
import { ApplicationView } from "../../../components/application-detail";
import { getApplication } from "../../../lib/server/api";
import { errorMessage, WebError } from "../../../lib/errors";
import { notFound } from "next/navigation";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
}: {
  params: Promise<{ applicationId: string }>;
}) {
  const { applicationId } = await params;
  let detail;
  try {
    detail = await getApplication(applicationId);
  } catch (error) {
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
      <Link className="back-link" href="/discover">
        ← К поиску вакансий
      </Link>
      <ApplicationView detail={detail} />
    </>
  );
}
