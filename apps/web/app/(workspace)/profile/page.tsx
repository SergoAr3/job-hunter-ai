import { ProfileWorkspace } from "../../../components/profile-workspace";
import { pageAccess, pagePath } from "../../../lib/server/page-access";
import { AuthUnavailable } from "../../../components/auth-unavailable";

export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!(await pageAccess(await pagePath("/profile", searchParams))))
    return <AuthUnavailable />;
  return <ProfileWorkspace />;
}
