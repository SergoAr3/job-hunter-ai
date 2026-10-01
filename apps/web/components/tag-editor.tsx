"use client";

import { useRef } from "react";

export function appendTag(values: string[], input: string, maxItems: number) {
  const value = input.trim();
  if (!value || values.length >= maxItems || values.includes(value))
    return values;
  return [...values, value];
}

export function TagEditor({
  id,
  label,
  itemName,
  values,
  inputValue,
  onInputChange,
  onChange,
  maxItems,
  maxLength,
  placeholder,
  error,
  disabled,
}: {
  id: string;
  label: string;
  itemName: string;
  values: string[];
  inputValue: string;
  onInputChange: (value: string) => void;
  onChange: (values: string[]) => void;
  maxItems: number;
  maxLength: number;
  placeholder: string;
  error?: string;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const full = values.length >= maxItems;
  function addCurrent() {
    const next = appendTag(values, inputValue, maxItems);
    if (next !== values) onChange(next);
    onInputChange("");
  }

  return (
    <div className="tag-editor-field">
      <label htmlFor={id}>{label}</label>
      <div className="tag-editor" data-invalid={error ? true : undefined}>
        {values.map((value, index) => (
          <span className="tag-editor-chip" key={`${value}-${index}`}>
            <span className="tag-editor-text">{value}</span>
            <button
              type="button"
              className="tag-editor-remove"
              disabled={disabled}
              aria-label={`Удалить ${itemName} ${value}${values.indexOf(value) === index ? "" : `, запись ${index + 1}`}`}
              onClick={() => {
                onChange(values.filter((_, itemIndex) => itemIndex !== index));
                queueMicrotask(() => inputRef.current?.focus());
              }}
            >
              <span aria-hidden="true">×</span>
            </button>
          </span>
        ))}
        <input
          id={id}
          ref={inputRef}
          className="tag-editor-input"
          value={inputValue}
          maxLength={maxLength}
          disabled={disabled || full}
          placeholder={full ? `Лимит: ${maxItems}` : placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addCurrent();
            }
          }}
        />
      </div>
      <div className="tag-editor-meta">
        {error ? (
          <p className="profile-field-error" id={`${id}-error`}>
            {error}
          </p>
        ) : (
          <span />
        )}
        <span className="profile-count">
          {values.length} / {maxItems}
        </span>
      </div>
    </div>
  );
}
