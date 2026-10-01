import type { ReactNode } from "react";
import type { ApplicationDetail } from "../lib/contracts";
import { JobMetadata, JobText, SourceLink, statusLabels } from "./vacancy";
export function ApplicationView({
  detail,
  statusControl,
  historySection,
  statusUnconfirmed = false,
}: {
  detail: ApplicationDetail;
  statusControl?: ReactNode;
  historySection?: ReactNode;
  statusUnconfirmed?: boolean;
}) {
  const { job, application } = detail;
  return (
    <article className="application-card">
      <header className="application-detail-header">
        <div className="application-detail-heading">
          <span className="eyebrow">СОХРАНЁННАЯ ВАКАНСИЯ</span>
          <h1>{job.title ?? "Вакансия без названия"}</h1>
          {job.company && <p className="company">{job.company}</p>}
        </div>
        <span className="badge status-badge">
          {statusUnconfirmed
            ? "Статус не подтверждён"
            : (statusLabels[application.status] ?? application.status)}
        </span>
      </header>
      {statusControl}
      <JobMetadata job={job} />
      <div className="save-area">
        <SourceLink url={job.source_url} />
      </div>
      {(application.note || application.next_action) && (
        <section className="crm-notes">
          <h2>Ваши записи</h2>
          {application.note && (
            <>
              <h3>Заметка</h3>
              <p className="source-text">{application.note}</p>
            </>
          )}
          {application.next_action && (
            <>
              <h3>Следующее действие</h3>
              <p className="source-text">{application.next_action}</p>
              {application.next_action_due_on && (
                <p>
                  Дата:{" "}
                  <time dateTime={application.next_action_due_on}>
                    {application.next_action_due_on}
                  </time>
                </p>
              )}
            </>
          )}
        </section>
      )}
      <JobText job={job} />
      {historySection}
      <p className="muted read-only-note">
        Заметку и следующее действие можно изменить в Telegram.
      </p>
    </article>
  );
}
