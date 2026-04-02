/**
 * ParseGrid — Status badge component.
 */

"use client";

const BADGE_STYLES: Record<string, string> = {
  UPLOADED: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30",
  OCR_PROCESSING: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  SCHEMA_PROPOSED: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  AWAITING_REVIEW: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  SCHEMA_LOCKED: "bg-indigo-500/20 text-indigo-400 border-indigo-500/30",
  EXTRACTING: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  MERGING: "bg-violet-500/20 text-violet-400 border-violet-500/30",
  TRANSLATING: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  PROVISIONING: "bg-teal-500/20 text-teal-400 border-teal-500/30",
  COMPLETED: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  FAILED: "bg-red-500/20 text-red-400 border-red-500/30",
};

const STATUS_LABELS: Record<string, string> = {
  UPLOADED: "Uploaded",
  OCR_PROCESSING: "Processing",
  SCHEMA_PROPOSED: "Review Schema",
  AWAITING_REVIEW: "Review Schema",
  SCHEMA_LOCKED: "Schema Locked",
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
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style} ${className}`}
    >
      {label}
    </span>
  );
}
