import type { Metadata } from "next";
import { EmailRecovery } from "../../components/email-recovery";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};
export default function Page() {
  return (
    <main id="main" className="auth-shell">
      <EmailRecovery mode="verify" />
    </main>
  );
}
