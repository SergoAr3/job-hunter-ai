import { redirect } from "next/navigation";
import { getCurrentUser } from "../lib/server/auth";
import { AuthUnavailable } from "../components/auth-unavailable";
export const dynamic = "force-dynamic";
export default async function Home() {
  const state = await getCurrentUser();
  if (state.kind === "unavailable") return <AuthUnavailable />;
  redirect(state.kind === "authenticated" ? "/dashboard" : "/login");
}
