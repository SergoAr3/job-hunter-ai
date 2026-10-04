import { CVImport } from "../../../../components/cv-import";
import { AuthUnavailable } from "../../../../components/auth-unavailable";
import { pageAccess } from "../../../../lib/server/page-access";

export const dynamic = "force-dynamic";
export default async function Page() {
  if (!(await pageAccess("/profile/import"))) return <AuthUnavailable />;
  return <CVImport />;
}
