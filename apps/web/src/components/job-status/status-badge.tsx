/**
 * ParseGrid — Status badge component.
 * Neutral base with emerald for success, amber for attention, red for failure.
 */

"use client";

const BADGE_STYLES: Record<string, string> = {
  UPLOADED: "bg-zinc-800/60 text-zinc-400 border-zinc-700/60",
  OCR_PROCESSING: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  SCHEMA_PROPOSED: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  AWAITING_REVIEW: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  SCHEMA_LOCKED: "bg-zinc-800/60 text-zinc-300 border-zinc-700/60",
  EXTRACTING: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  MERGING: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  TRANSLATING: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  PROVISIONING: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  COMPLETED: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  FAILED: "bg-red-500/10 text-red-400 border-red-500/20",
};

const STATUS_LABELS: Record<string, string> = {
  UPLOADED: "Uploaded",
  OCR_PROCESSING: "Processing",
  SCHEMA_PROPOSED: "Review Schema",
  AWAITING_REVIEW: "Review Schema",
  SCHEMA_LOCKED: "Locked",
  EXTRACTING: "Extracting",
  MERGING: "Merging",
  TRANSLATING: "Translating",
  PROVISIONING: "Provisioning",
  COMPLETED: "Completed",
  FAILED: "Failed",
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const style = BADGE_STYLES[status] || BADGE_STYLES.UPLOADED;
  const label = STATUS_LABELS[status] || status;

  return (
    <span
      className={`inline-flex items-center rounded-lg border px-2.5 py-0.5 text-xs font-medium ${style} ${className}`}
    >
      {label}
    </span>
  );
}
