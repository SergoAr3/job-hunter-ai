import { ProfileWorkspace } from "../../components/profile-workspace";
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
  return <ProfileWorkspace />;
}
