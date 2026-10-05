"use client";

import { useEffect, useId, useRef, useState } from "react";

export function ListboxSelect<T extends string>({
  id,
  labelId,
  value,
  options,
  labels,
  disabled,
  invalid,
  describedBy,
  onChange,
  className = "",
  editable = false,
  maxLength,
}: {
  id: string;
  labelId: string;
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  disabled: boolean;
  invalid?: boolean;
  describedBy?: string;
  onChange: (value: T) => void;
  className?: string;
  /** Free-form suggestions, used by the CV language-level editor. */
  editable?: boolean;
  maxLength?: number;
}) {
  const [isOpen, setOpen] = useState(false);
  const open = isOpen && !disabled;
  const [activeIndex, setActiveIndex] = useState(0);
  const [above, setAbove] = useState(false);
  const [maxHeight, setMaxHeight] = useState(320);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement | HTMLInputElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listboxId = useId();
  const selectedIndex = Math.max(0, options.indexOf(value));

  useEffect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", closeOutside);

    return () => {
      document.removeEventListener("pointerdown", closeOutside);
    };
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
      const needed = Math.min(320, options.length * 44 + 12);
      const opensAbove = below < needed && aboveSpace > below;
      setAbove(opensAbove);
      setMaxHeight(
        Math.max(80, Math.min(320, (opensAbove ? aboveSpace : below) - 8)),
      );
    }
    setActiveIndex(index);
    setOpen(true);
  }

  function choose(index: number) {
    if (disabled || options[index] === undefined) return;
    onChange(options[index]);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function handleKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement | HTMLInputElement>,
  ) {
    if (disabled) return;
    switch (event.key) {
      case "Enter":
      case " ":
        if (editable && event.key === " ") return;
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
              (current + direction + options.length) % options.length,
          );
        else
          openAt((selectedIndex + direction + options.length) % options.length);
        break;
      }
      case "Home":
      case "End":
        if (editable && !open) return;
        event.preventDefault();
        if (!open) openAt(event.key === "Home" ? 0 : options.length - 1);
        else setActiveIndex(event.key === "Home" ? 0 : options.length - 1);
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
          triggerRef.current?.focus();
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  return (
    <div
      className={`status-select${className ? ` ${className}` : ""}`}
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      {editable ? (
        <input
          id={id}
          ref={(node) => {
            triggerRef.current = node;
          }}
          role="combobox"
          aria-labelledby={labelId}
          aria-autocomplete="list"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={
            open ? `${listboxId}-option-${activeIndex}` : undefined
          }
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className="status-select-trigger"
          value={value}
          maxLength={maxLength}
          disabled={disabled}
          onChange={(event) => {
            onChange(event.target.value as T);
            setOpen(false);
          }}
          onClick={() => openAt(selectedIndex)}
          onKeyDown={handleKeyDown}
        />
      ) : (
        <button
          id={id}
          ref={(node) => {
            triggerRef.current = node;
          }}
          type="button"
          role="combobox"
          aria-labelledby={`${labelId} ${listboxId}-value`}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={
            open ? `${listboxId}-option-${activeIndex}` : undefined
          }
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className="status-select-trigger"
          disabled={disabled}
          onKeyDown={handleKeyDown}
          onClick={(event) => {
            if (!open) openAt(selectedIndex);
            else if (event.detail > 0) setOpen(false);
            else choose(activeIndex);
          }}
        >
          <span id={`${listboxId}-value`}>{labels[value]}</span>
          <span className="status-select-chevron" aria-hidden="true" />
        </button>
      )}
      <div
        id={listboxId}
        role="listbox"
        aria-labelledby={labelId}
        className={`status-select-popup${above ? " is-above" : ""}`}
        style={{ maxHeight }}
        hidden={!open}
      >
        {options.map((option, index) => (
          <button
            key={option}
            id={`${listboxId}-option-${index}`}
            ref={(element) => {
              optionRefs.current[index] = element;
            }}
            type="button"
            role="option"
            aria-selected={option === value}
            tabIndex={-1}
            className={`status-select-option${index === activeIndex ? " is-active" : ""}`}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => choose(index)}
          >
            <span>{labels[option]}</span>
            {option === value && <span aria-hidden="true">✓</span>}
          </button>
        ))}
      </div>
    </div>
  );
}
