"use client";
import { useId, useState, type ReactNode } from "react";

export function MatchDisclosure({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="match-disclosure">
      <button
        type="button"
        className="text-action"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded(!expanded)}
      >
        <span aria-hidden="true">{expanded ? "−" : "+"}</span> {label}
      </button>
      <div id={id} hidden={!expanded}>
        {children}
      </div>
    </div>
  );
}
