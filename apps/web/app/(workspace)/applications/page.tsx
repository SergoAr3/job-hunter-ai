import { Suspense } from "react";
import { ApplicationsList } from "../../../components/applications-list";
import { pageAccess, pagePath } from "../../../lib/server/page-access";
import { AuthUnavailable } from "../../../components/auth-unavailable";

export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!(await pageAccess(await pagePath("/applications", searchParams))))
    return <AuthUnavailable />;
  return (
    <Suspense
      fallback={
        <div className="state" role="status">
          Загружаем вакансии…
        </div>
      }
    >
      <ApplicationsList />
    </Suspense>
  );
}
