"use client";

import { DATABASES, type DatabaseId } from "@/lib/databases";

export function DatabasePicker({
  value,
  onChange,
  disabled,
  compact = false,
}: {
  value: DatabaseId[];
  onChange: (next: DatabaseId[]) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const selected = new Set(value);
  const toggle = (id: DatabaseId) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(Array.from(next));
  };
  const allOn = value.length === DATABASES.length;
  const toggleAll = () =>
    onChange(allOn ? [] : DATABASES.map((d) => d.id));

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          Search databases · {value.length}/{DATABASES.length}
        </p>
        <button
          type="button"
          onClick={toggleAll}
          className="text-[11px] font-medium text-[var(--cnrs-blue)] underline-offset-2 hover:underline"
          disabled={disabled}
        >
          {allOn ? "Deselect all" : "Select all"}
        </button>
      </div>
      <div
        className={
          compact
            ? "grid grid-cols-2 gap-1.5 sm:grid-cols-3"
            : "grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4"
        }
      >
        {DATABASES.map((db) => {
          const on = selected.has(db.id);
          return (
            <label
              key={db.id}
              className={[
                "flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] border px-2.5 py-1.5 text-xs transition",
                on
                  ? "border-[var(--cnrs-blue)] bg-[var(--cnrs-yellow)]/30 text-[var(--cnrs-blue)]"
                  : "border-[var(--border)] bg-transparent text-[var(--text-body)] hover:bg-[var(--cnrs-grey-light)]",
                disabled && "cursor-not-allowed opacity-60",
              ].join(" ")}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(db.id)}
                disabled={disabled}
                className="h-3.5 w-3.5 cursor-pointer accent-[var(--cnrs-blue)]"
              />
              <span className="truncate font-medium">{db.label}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
