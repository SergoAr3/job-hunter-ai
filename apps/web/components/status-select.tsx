"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  applicationStatuses,
  type ApplicationStatus,
} from "../lib/applications";
import { statusLabels } from "./vacancy";

export function StatusSelect({
  value,
  disabled,
  onChange,
  labelId,
}: {
  value: ApplicationStatus;
  disabled: boolean;
  onChange: (status: ApplicationStatus) => void;
  labelId: string;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [above, setAbove] = useState(false);
  const [maxHeight, setMaxHeight] = useState(320);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listboxId = useId();
  const selectedIndex = applicationStatuses.indexOf(value);

  useEffect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  useEffect(() => {
    if (open)
      optionRefs.current[activeIndex]?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, open]);

  function openAt(index: number) {
    if (disabled) return;
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      const below = window.innerHeight - rect.bottom - 8;
      const aboveSpace = rect.top - 8;
      const opensAbove = below < 290 && aboveSpace > below;
      setAbove(opensAbove);
      setMaxHeight(
        Math.max(80, Math.min(320, (opensAbove ? aboveSpace : below) - 8)),
      );
    }
    setActiveIndex(index);
    setOpen(true);
  }

  function choose(index: number) {
    onChange(applicationStatuses[index]);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    switch (event.key) {
      case "Enter":
      case " ":
        event.preventDefault();
        if (open) choose(activeIndex);
        else openAt(selectedIndex);
        break;
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        if (open)
          setActiveIndex(
            (current) =>
              (current + direction + applicationStatuses.length) %
              applicationStatuses.length,
          );
        else
          openAt(
            (selectedIndex + direction + applicationStatuses.length) %
              applicationStatuses.length,
          );
        break;
      }
      case "Home":
      case "End":
        event.preventDefault();
        if (!open)
          openAt(event.key === "Home" ? 0 : applicationStatuses.length - 1);
        else
          setActiveIndex(
            event.key === "Home" ? 0 : applicationStatuses.length - 1,
          );
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  return (
    <div className="status-select" ref={rootRef}>
      <button
        id="application-status"
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-labelledby={`${labelId} ${listboxId}-value`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={
          open ? `${listboxId}-option-${activeIndex}` : undefined
        }
        className="status-select-trigger"
        disabled={disabled}
        onKeyDown={handleKeyDown}
        onClick={(event) => {
          if (!open) openAt(selectedIndex);
          else if (event.detail > 0) setOpen(false);
          else choose(activeIndex);
        }}
      >
        <span id={`${listboxId}-value`}>{statusLabels[value]}</span>
        <span className="status-select-chevron" aria-hidden="true" />
      </button>
      <div
        id={listboxId}
        role="listbox"
        aria-labelledby={labelId}
        className={`status-select-popup${above ? " is-above" : ""}`}
        style={{ maxHeight }}
        hidden={!open}
      >
        {applicationStatuses.map((status, index) => (
          <button
            key={status}
            id={`${listboxId}-option-${index}`}
            ref={(element) => {
              optionRefs.current[index] = element;
            }}
            type="button"
            role="option"
            aria-selected={status === value}
            tabIndex={-1}
            className={`status-select-option${index === activeIndex ? " is-active" : ""}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => choose(index)}
          >
            <span>{statusLabels[status]}</span>
            {status === value && <span aria-hidden="true">✓</span>}
          </button>
        ))}
      </div>
    </div>
  );
}
