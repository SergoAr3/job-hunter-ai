import Link from "next/link";
import { applicationStatuses } from "../lib/applications";
import { statusLabels } from "../lib/application-presentation";
import type {
  ApplicationListItem,
  ApplicationsSummary,
} from "../lib/contracts";
import { workplaceLabels, type Profile } from "../lib/profile";
import { compactLocation } from "../lib/presentation";

function SummaryIcon({
  kind,
}: {
  kind: "total" | "saved" | "applied" | "interview";
}) {
  return (
    <span
      className={`dashboard-kpi-icon dashboard-kpi-icon-${kind}`}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {kind === "total" && (
          <>
            <rect x="3" y="6" width="18" height="15" rx="2" />
            <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" />
          </>
        )}
        {kind === "saved" && (
          <path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1Z" />
        )}
        {kind === "applied" && (
          <>
            <path d="m21 3-7 18-4-7-7-4 18-7ZM10 14l11-11" />
          </>
        )}
        {kind === "interview" && (
          <>
            <path d="M21 11a8 8 0 0 1-8 8H9l-6 3 2-6a8 8 0 0 1-2-5 8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z" />
            <path d="M8 9h8M8 13h5" />
          </>
        )}
      </svg>
    </span>
  );
}

function ProfileSummary({ profile }: { profile: Profile | null }) {
  const incomplete = !profile?.target_roles.length || !profile.skills.length;
  return (
    <section
      className="dashboard-card dashboard-profile"
      aria-labelledby="dashboard-profile-heading"
    >
      <h2 id="dashboard-profile-heading">Профиль</h2>
      {profile?.target_roles.length ? (
        <p className="dashboard-role">{profile.target_roles.join(" · ")}</p>
      ) : null}
      {profile && (
        <>
          {profile.location.length > 0 && (
            <p className="dashboard-muted">{profile.location.join(" · ")}</p>
          )}
          <p className="dashboard-muted">
            Формат работы: {workplaceLabels[profile.workplace_preference]}
          </p>
          {profile.skills.length > 0 && (
            <ul className="dashboard-tags" aria-label="Навыки">
              {profile.skills.slice(0, 6).map((skill, index) => (
                <li key={index}>{skill}</li>
              ))}
            </ul>
          )}
          {profile.skills.length > 6 && (
            <p className="dashboard-muted">
              Ещё {profile.skills.length - 6} в профиле
            </p>
          )}
        </>
      )}
      {incomplete && (
        <p className="dashboard-muted">
          Добавьте информацию в профиль, чтобы он был полезнее при работе с
          вакансиями.
        </p>
      )}
      <Link
        className={`text-link${incomplete ? "" : " dashboard-profile-edit"}`}
        href="/profile"
      >
        {incomplete ? "Заполнить профиль" : "Редактировать профиль →"}
      </Link>
    </section>
  );
}

export function Dashboard({
  summary,
  recent,
  profile,
}: {
  summary: ApplicationsSummary;
  recent: ApplicationListItem[];
  profile: Profile | null;
}) {
  const maximum = Math.max(
    1,
    ...applicationStatuses.map((status) => summary.status_counts[status]),
  );
  return (
    <div className="dashboard-page">
      <header className="page-heading dashboard-heading">
        <div>
          <h1>Обзор поиска работы</h1>
          <p>Следите за вакансиями и откликами в одном месте.</p>
        </div>
        <div className="dashboard-actions">
          <Link className="button-link primary" href="/discover">
            Найти вакансии
          </Link>
          <Link className="button-link" href="/applications">
            Мои вакансии
          </Link>
        </div>
      </header>
      {summary.total === 0 ? (
        <div className="dashboard-columns">
          <section className="dashboard-card dashboard-empty">
            <span className="dashboard-empty-icon" aria-hidden="true">
              ↗
            </span>
            <h2>Начните поиск вакансий</h2>
            <p>Здесь появится обзор сохранённых вакансий и вашего прогресса.</p>
            <Link className="button-link primary" href="/discover">
              Найти вакансии
            </Link>
          </section>
          <ProfileSummary profile={profile} />
        </div>
      ) : (
        <>
          <section className="dashboard-summary" aria-label="Сводка вакансий">
            <article
              className="dashboard-card dashboard-kpi dashboard-kpi-total"
              aria-label="Всего вакансий"
            >
              <div className="dashboard-kpi-heading">
                <h2>Всего вакансий</h2>
                <SummaryIcon kind="total" />
              </div>
              <strong className="dashboard-kpi-number">{summary.total}</strong>
              <p className="dashboard-kpi-caption">в вашем списке</p>
            </article>
            {(["saved", "applied", "interview"] as const).map((status) => (
              <article
                className="dashboard-card dashboard-kpi"
                key={status}
                aria-label={
                  status === "saved" ? "Сохранено" : statusLabels[status]
                }
              >
                <div className="dashboard-kpi-heading">
                  <h2>
                    {status === "saved" ? "Сохранено" : statusLabels[status]}
                  </h2>
                  <SummaryIcon kind={status} />
                </div>
                <strong className="dashboard-kpi-number">
                  {summary.status_counts[status]}
                </strong>
                <p className="dashboard-kpi-caption">в этом статусе</p>
              </article>
            ))}
          </section>
          <div className="dashboard-columns">
            <section
              className="dashboard-card dashboard-pipeline-card"
              aria-labelledby="pipeline-heading"
            >
              <h2 id="pipeline-heading">Воронка поиска</h2>
              <p className="dashboard-muted">Вакансии по текущему статусу</p>
              <ul className="dashboard-pipeline">
                {applicationStatuses.map((status) => (
                  <li key={status}>
                    <div className="dashboard-pipeline-label">
                      <span>{statusLabels[status]}</span>
                      <strong>{summary.status_counts[status]}</strong>
                    </div>
                    <div className="dashboard-bar" aria-hidden="true">
                      <span
                        style={{
                          width: `${(summary.status_counts[status] / maximum) * 100}%`,
                        }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <ProfileSummary profile={profile} />
          </div>
          <section
            className="dashboard-card dashboard-recent"
            aria-labelledby="recent-heading"
          >
            <div className="section-heading">
              <h2 id="recent-heading">Недавние вакансии</h2>
            </div>
            <div className="applications-rows">
              {recent.slice(0, 5).map((item) => {
                const date = new Date(item.created_at);
                return (
                  <Link
                    className="application-row dashboard-recent-row"
                    href={`/applications/${item.app_id}`}
                    key={item.app_id}
                  >
                    <span className="application-row-main">
                      <strong>
                        {item.title?.trim() || "Вакансия без названия"}
                      </strong>
                      {(item.company || item.location) && (
                        <span className="dashboard-recent-company">
                          {[item.company, compactLocation(item.location)]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      )}
                    </span>
                    <span className="application-row-meta">
                      {!Number.isNaN(date.getTime()) && (
                        <span>
                          Сохранена{" "}
                          <time dateTime={item.created_at}>
                            {new Intl.DateTimeFormat("ru-RU", {
                              timeZone: "Asia/Yerevan",
                            }).format(date)}
                          </time>
                        </span>
                      )}
                    </span>
                    <span className="badge status-badge">
                      {statusLabels[item.status] ?? item.status}
                    </span>
                  </Link>
                );
              })}
            </div>
            <Link className="text-link dashboard-all" href="/applications">
              Все вакансии →
            </Link>
          </section>
        </>
      )}
    </div>
  );
}
