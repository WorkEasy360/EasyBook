"use client";

import * as React from "react";
import { cn } from "@/lib/cn";
import { Icons } from "./icons";
import { Spinner } from "./spinner";
import { useFieldContext } from "./field";

/**
 * Async single-select with typeahead.
 *
 * Needed because the lists behind these pickers are unbounded — an
 * organization can have 50,000 customers or items, so a <select> full of
 * options is not an option (spec §78). Search happens on the server.
 *
 * Implements the ARIA combobox pattern: the input keeps focus and owns
 * aria-activedescendant, so a screen reader announces the highlighted option
 * without the focus ring leaving the text field.
 */

export interface ComboboxOption {
  value: string;
  label: string;
  /** Second line — SKU, email, account code. */
  hint?: string;
  disabled?: boolean;
}

export interface ComboboxProps {
  value: string | null;
  onChange: (value: string | null, option: ComboboxOption | null) => void;
  options: ComboboxOption[];
  /** Search term, controlled so the caller can issue the query. */
  search: string;
  onSearchChange: (search: string) => void;
  isLoading?: boolean;
  placeholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  /** Shown when `value` is set but the option is not in the current page. */
  selectedLabel?: string | null;
  className?: string;
}

export function Combobox({
  value,
  onChange,
  options,
  search,
  onSearchChange,
  isLoading = false,
  placeholder = "Search…",
  emptyMessage = "No matches",
  disabled = false,
  selectedLabel,
  className,
}: ComboboxProps) {
  const field = useFieldContext();
  const [open, setOpen] = React.useState(false);
  const [highlighted, setHighlighted] = React.useState(0);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const listId = React.useId();

  const selected = options.find((option) => option.value === value) ?? null;
  const displayLabel = selected?.label ?? selectedLabel ?? "";

  // Reset the highlight whenever the result set changes, so Enter never picks
  // a row the user cannot see.
  const [lastSignature, setLastSignature] = React.useState("");
  const signature = options.map((option) => option.value).join("|");
  if (signature !== lastSignature) {
    setLastSignature(signature);
    setHighlighted(0);
  }

  React.useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function commit(option: ComboboxOption) {
    if (option.disabled) return;
    onChange(option.value, option);
    onSearchChange("");
    setOpen(false);
    inputRef.current?.blur();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setHighlighted((current) => {
        const next = current + delta;
        if (next < 0) return Math.max(0, options.length - 1);
        if (next >= options.length) return 0;
        return next;
      });
      return;
    }

    if (event.key === "Enter") {
      const option = options[highlighted];
      if (open && option) {
        // Only swallow Enter when it is actually picking something — otherwise
        // it must still submit the surrounding form.
        event.preventDefault();
        commit(option);
      }
      return;
    }

    if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        setOpen(false);
      }
      return;
    }

    if (event.key === "Backspace" && search === "" && value) {
      onChange(null, null);
    }
  }

  const activeId = open && options[highlighted] ? `${listId}-${options[highlighted].value}` : undefined;

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <div
        className={cn(
          "flex h-9 items-center gap-1 rounded-md border bg-white px-2.5",
          field?.invalid ? "border-danger-500" : "border-ink-300 hover:border-ink-400",
          disabled && "cursor-not-allowed bg-ink-50",
        )}
      >
        <input
          ref={inputRef}
          id={field?.id}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          aria-describedby={field?.describedBy}
          aria-invalid={field?.invalid || undefined}
          aria-required={field?.required || undefined}
          disabled={disabled || field?.disabled}
          value={open ? search : displayLabel}
          placeholder={displayLabel || placeholder}
          onChange={(event) => {
            onSearchChange(event.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="min-w-0 flex-1 bg-transparent text-sm text-ink-900 placeholder:text-ink-400 focus:outline-none disabled:cursor-not-allowed"
        />

        {isLoading ? <Spinner className="size-3.5 shrink-0 text-ink-400" /> : null}

        {value && !disabled ? (
          <button
            type="button"
            aria-label="Clear selection"
            onClick={() => {
              onChange(null, null);
              onSearchChange("");
              inputRef.current?.focus();
            }}
            className="shrink-0 rounded p-0.5 text-ink-400 hover:text-ink-700"
          >
            <Icons.close className="size-3.5" />
          </button>
        ) : null}

        <Icons.chevronDown className="size-3.5 shrink-0 text-ink-400" />
      </div>

      {open ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-ink-200 bg-white py-1 shadow-overlay"
        >
          {isLoading && options.length === 0 ? (
            <li className="px-3 py-2 text-sm text-ink-500">Searching…</li>
          ) : options.length === 0 ? (
            <li className="px-3 py-2 text-sm text-ink-500">{emptyMessage}</li>
          ) : (
            options.map((option, index) => (
              <li
                key={option.value}
                id={`${listId}-${option.value}`}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled || undefined}
                // pointerdown, not click: click fires after the input's blur,
                // by which time the list has already closed.
                onPointerDown={(event) => {
                  event.preventDefault();
                  commit(option);
                }}
                onPointerEnter={() => setHighlighted(index)}
                className={cn(
                  "cursor-pointer px-3 py-1.5 text-sm",
                  index === highlighted ? "bg-brand-50 text-brand-900" : "text-ink-800",
                  option.disabled && "cursor-not-allowed opacity-50",
                )}
              >
                <span className="block truncate">{option.label}</span>
                {option.hint ? (
                  <span className="block truncate text-xs text-ink-500">{option.hint}</span>
                ) : null}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
