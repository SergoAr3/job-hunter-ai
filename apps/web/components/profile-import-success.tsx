"use client";

import { useEffect, useState } from "react";

export function ProfileImportSuccess() {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (
      url.pathname === "/profile" &&
      url.searchParams.get("imported") === "1"
    ) {
      url.searchParams.delete("imported");
      window.history.replaceState(
        window.history.state,
        "",
        `${url.pathname}${url.search}${url.hash}`,
      );
    }
    const timer = window.setTimeout(() => setVisible(false), 4500);
    return () => window.clearTimeout(timer);
  }, []);

  if (!visible) return null;
  return (
    <p className="profile-import-toast" role="status" aria-live="polite">
      Данные резюме применены к профилю.
    </p>
  );
}
