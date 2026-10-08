"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { FollowUpsPage } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { ReminderSummary } from "./reminder-summary";
import { useBrowserTimezone } from "../lib/browser-timezone";

const buckets = ["overdue", "today", "upcoming"] as const;
const labels = {
  overdue: "Требует внимания",
  today: "Сегодня",
  upcoming: "Далее",
};
export function DashboardFollowUps({
  telegramLinked,
}: {
  telegramLinked?: boolean;
}) {
  const [data, setData] = useState<FollowUpsPage[] | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const timezone = useBrowserTimezone();
  useEffect(() => {
    let active = true;
    if (!timezone) return;
    Promise.all(
      buckets.map((bucket) =>
        webRequest<FollowUpsPage>(
          `/api/applications/follow-ups?${new URLSearchParams({ timezone, bucket })}`,
        ),
      ),
    )
      .then((value) => {
        if (active) setData(value);
      })
      .catch(() => {
        if (active) setError("Не удалось загрузить ближайшие действия.");
      });
    return () => {
      active = false;
    };
  }, [reload, timezone]);
  return (
    <section
      className="dashboard-card dashboard-follow-ups"
      aria-labelledby="follow-ups-heading"
    >
      <h2 id="follow-ups-heading">Ближайшие действия</h2>
      {!timezone ? (
        <p className="muted">Не удалось определить часовой пояс браузера.</p>
      ) : error ? (
        <div role="alert">
          <p>{error}</p>
          <button
            onClick={() => {
              setError("");
              setReload((v) => v + 1);
            }}
          >
            Повторить
          </button>
        </div>
      ) : !data ? (
        <p className="muted" role="status">
          Загружаем действия…
        </p>
      ) : data.every((group) => !group.items.length) ? (
        <p className="muted">
          Напоминаний пока нет. Добавьте следующее действие в карточке вакансии.
        </p>
      ) : (
        <div className="follow-up-groups">
          {data.map((group, index) => (
            <div key={buckets[index]}>
              <h3>{labels[buckets[index]]}</h3>
              {!group.items.length ? (
                <p className="muted">Нет действий</p>
              ) : (
                <ul>
                  {group.items.map((item) => (
                    <li key={item.application_id}>
                      <Link href={`/applications/${item.application_id}`}>
                        <strong>{item.next_action}</strong>
                        <span className="muted">
                          {item.title || "Вакансия без названия"}
                          {item.company ? ` · ${item.company}` : ""}
                        </span>
                      </Link>
                      <ReminderSummary
                        telegramLinked={telegramLinked}
                        value={item}
                        legacy={item.next_action_due_on}
                        temporal={false}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {group.has_next && (
                <Link
                  className="text-link"
                  href="/applications?sort=next_action"
                >
                  Все вакансии →
                </Link>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
