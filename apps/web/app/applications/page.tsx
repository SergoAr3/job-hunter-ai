import { Suspense } from "react";
import { ApplicationsList } from "../../components/applications-list";
import { getConfig } from "../../lib/server/config";
import { errorMessage } from "../../lib/errors";

export const dynamic = "force-dynamic";
export default function Page() {
  try {
    getConfig();
  } catch (error) {
    return (
      <div className="notice error" role="alert">
        <h1>Нужна настройка Web</h1>
        <p>{errorMessage(error)}</p>
      </div>
    );
  }
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
