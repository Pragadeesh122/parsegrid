/**
 * ParseGrid — Status badge component.
 * Neutral base with emerald for success, amber for attention, red for failure.
 */

"use client";

const EMERALD = "bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
const AMBER = "bg-amber-500/10 text-amber-400 border-amber-500/20";
const NEUTRAL = "bg-zinc-800/60 text-zinc-400 border-zinc-700/60";
const NEUTRAL_LIGHT = "bg-zinc-800/60 text-zinc-300 border-zinc-700/60";

const BADGE_STYLES: Record<string, string> = {
  UPLOADED: NEUTRAL,
  OCR_PROCESSING: EMERALD,
  INDEXING: EMERALD,
  AWAITING_QUERY: AMBER,
  PROFILING: EMERALD,
  MODEL_PROPOSED: AMBER,
  AWAITING_REVIEW: AMBER,
  MODEL_LOCKED: NEUTRAL_LIGHT,
  EXTRACTING: EMERALD,
  MERGING: EMERALD,
  RECONCILING: EMERALD,
  TRANSLATING: EMERALD,
  PROVISIONING: EMERALD,
  COMPLETED: EMERALD,
  FAILED: "bg-red-500/10 text-red-400 border-red-500/20",
  APPENDING: EMERALD,
  AWAITING_APPEND_REVIEW: AMBER,
  PENDING: NEUTRAL,
  OCR_DONE: NEUTRAL_LIGHT,
  EXTRACTED: EMERALD,
  REJECTED: NEUTRAL,
};

const STATUS_LABELS: Record<string, string> = {
  UPLOADED: "Uploaded",
  OCR_PROCESSING: "Processing",
  INDEXING: "Indexing",
  AWAITING_QUERY: "Awaiting Query",
  PROFILING: "Profiling",
  MODEL_PROPOSED: "Review Model",
  AWAITING_REVIEW: "Review Model",
  MODEL_LOCKED: "Locked",
  EXTRACTING: "Extracting",
  MERGING: "Merging",
  RECONCILING: "Reconciling",
  TRANSLATING: "Translating",
  PROVISIONING: "Provisioning",
  COMPLETED: "Completed",
  FAILED: "Failed",
  APPENDING: "Adding Data",
  AWAITING_APPEND_REVIEW: "Review Append",
  PENDING: "Pending",
  OCR_DONE: "OCR Done",
  EXTRACTED: "Extracted",
  REJECTED: "Rejected",
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
