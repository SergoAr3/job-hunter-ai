"use client";
import { useId, useState } from "react";
import { MatchDisclosure } from "./match-disclosure";
import type {
  MatchConfidence,
  MatchVerdict,
  MatchResult,
} from "../lib/match-contract";
import {
  getMatchPreviewRows,
  matchVerdictLabels,
} from "../lib/match-presentation";
export function CompactMatchSummary({ result }: { result: MatchResult }) {
  const { score, verdict, coverage, confidence } = result;
  const [expanded, setExpanded] = useState(false);
  const previewId = useId();
  const rows = getMatchPreviewRows(result);
  return (
    <>
      <div className="match-compact-facts">
        {score !== null && verdict !== "insufficient_data" && (
          <strong
            className="match-compact-score"
            aria-label={`Оценка ${score} из 100`}
          >
            {score} <small aria-hidden="true">/ 100</small>
          </strong>
        )}
        <div className="match-compact-copy">
          <p className="match-verdict">{matchVerdictLabels[verdict]}</p>
          <p className="match-helper">
            Оценка основана только на сравнимых критериях.
          </p>
          <p className="match-helper">Сравнимый вес критериев: {coverage}%</p>
          {confidence === "low" && (
            <p className="match-compact-warning">
              Данных для общей оценки мало.
            </p>
          )}
        </div>
        <button
          type="button"
          className="text-action match-preview-toggle"
          aria-expanded={expanded}
          aria-controls={previewId}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "Скрыть подробности" : "Подробнее о соответствии"}
          <span aria-hidden="true">{expanded ? "▴" : "▾"}</span>
        </button>
      </div>
      <div
        id={previewId}
        className="match-preview"
        role="region"
        aria-label="Краткий разбор соответствия"
        hidden={!expanded}
      >
        {!!rows.length && (
          <ul role="list">
            {rows.map((row, index) => (
              <li key={index}>
                <span aria-hidden="true">{row.icon}</span>
                <span>{row.text}</span>
              </li>
            ))}
          </ul>
        )}
        <a
          className="text-link match-details-link"
          href="#match-details"
          onClick={() =>
            document
              .getElementById("match-details")
              ?.focus({ preventScroll: true })
          }
        >
          Перейти к полному разбору <span aria-hidden="true">↓</span>
        </a>
      </div>
    </>
  );
}
export function MatchSummary({
  score,
  verdict,
  coverage,
  confidence,
}: {
  score: number | null;
  verdict: MatchVerdict | null;
  coverage: number | null;
  confidence: MatchConfidence | null;
}) {
  return (
    <div
      className={`match-summary${confidence === "low" || verdict === "insufficient_data" ? " match-summary-muted" : ""}`}
    >
      {score !== null && verdict !== "insufficient_data" && (
        <div className="match-score-value">
          <strong aria-label={`Оценка ${score} из 100`}>
            {score} <small aria-hidden="true">/ 100</small>
          </strong>
          <span>Оценка по сравнимым критериям</span>
        </div>
      )}
      {verdict && (
        <p className="match-verdict">{matchVerdictLabels[verdict]}</p>
      )}
      <p className="match-helper match-summary-scope">
        Оценка основана только на сравнимых критериях.
      </p>
      {coverage !== null && (
        <p className="match-coverage">
          Для оценки удалось сравнить критерии с суммарным весом {coverage}%.
        </p>
      )}
      <MatchDisclosure label="Как считается оценка">
        <p className="match-helper">
          Критерии имеют разный вес. Неизвестные данные исключаются из оценки.
        </p>
      </MatchDisclosure>
      {confidence === "low" && (
        <p className="match-caveat">
          Данных для общей оценки мало. Балл относится только к сравнимым
          критериям.
        </p>
      )}
      {verdict === "insufficient_data" && (
        <p className="match-caveat">
          Не хватает сравнимых критериев или данных о роли и обязательных
          навыках. Ниже показано, что удалось сравнить и какие данные можно
          уточнить.
        </p>
      )}
    </div>
  );
}
