import type { ApplicationDetail } from "../lib/contracts";
import { JobMetadata, JobText, SourceLink, statusLabels } from "./vacancy";
export function ApplicationView({ detail }: { detail: ApplicationDetail }) {
  const { job, application } = detail;
  return (
    <article className="application-card">
      <div className="section-heading">
        <span className="eyebrow">СОХРАНЁННАЯ ВАКАНСИЯ</span>
        <span className="badge status-badge">
          {statusLabels[application.status] ?? application.status}
        </span>
      </div>
      <h1>{job.title ?? "Вакансия без названия"}</h1>
      {job.company && <p className="company">{job.company}</p>}
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
      <p className="muted read-only-note">
        Просмотр без редактирования. Статусы и записи можно изменить в Telegram.
      </p>
    </article>
  );
}
