/**
 * ParseGrid — Real-time job status progress component.
 *
 * Consolidates the pipeline into 5 major phases for a cleaner layout.
 * Each pipeline status maps to a phase + phase-local progress weight.
 */

"use client";

const PHASES = [
  { key: "upload", label: "Upload" },
  { key: "process", label: "Process" },
  { key: "review", label: "Review" },
  { key: "extract", label: "Extract" },
  { key: "provision", label: "Provision" },
] as const;

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; bgColor: string; phase: number }
> = {
  UPLOADED: {
    label: "Uploaded",
    color: "text-zinc-400",
    bgColor: "bg-zinc-500",
    phase: 0,
  },
  OCR_PROCESSING: {
    label: "Processing document",
    color: "text-blue-400",
    bgColor: "bg-blue-500",
    phase: 1,
  },
  SCHEMA_PROPOSED: {
    label: "Schema ready for review",
    color: "text-amber-400",
    bgColor: "bg-amber-500",
    phase: 2,
  },
  AWAITING_REVIEW: {
    label: "Awaiting your review",
    color: "text-amber-400",
    bgColor: "bg-amber-500",
    phase: 2,
  },
  SCHEMA_LOCKED: {
    label: "Schema locked",
    color: "text-indigo-400",
    bgColor: "bg-indigo-500",
    phase: 2,
  },
  EXTRACTING: {
    label: "Extracting data",
    color: "text-purple-400",
    bgColor: "bg-purple-500",
    phase: 3,
  },
  MERGING: {
    label: "Merging results",
    color: "text-violet-400",
    bgColor: "bg-violet-500",
    phase: 3,
  },
  TRANSLATING: {
    label: "Translating schema",
    color: "text-cyan-400",
    bgColor: "bg-cyan-500",
    phase: 3,
  },
  PROVISIONING: {
    label: "Provisioning database",
    color: "text-teal-400",
    bgColor: "bg-teal-500",
    phase: 4,
  },
  COMPLETED: {
    label: "Completed",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500",
    phase: 5,
  },
  FAILED: {
    label: "Failed",
    color: "text-red-400",
    bgColor: "bg-red-500",
    phase: -1,
  },
};

interface ProgressBarProps {
  status: string;
  progress: number;
  errorMessage?: string | null;
}

export function ProgressBar({
  status,
  progress,
  errorMessage,
}: ProgressBarProps) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.UPLOADED;
  const isFailed = status === "FAILED";
  const isCompleted = status === "COMPLETED";

  return (
    <div className="space-y-5">
      {/* Status header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {!isCompleted && !isFailed && (
            <span className="relative flex h-2.5 w-2.5">
              <span
                className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${config.bgColor}`}
              />
              <span
                className={`relative inline-flex h-2.5 w-2.5 rounded-full ${config.bgColor}`}
              />
            </span>
          )}
          {isCompleted && (
            <svg className="h-4 w-4 text-emerald-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clipRule="evenodd" />
            </svg>
          )}
          {isFailed && (
            <svg className="h-4 w-4 text-red-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
            </svg>
          )}
          <span className={`text-sm font-semibold ${config.color}`}>
            {config.label}
          </span>
        </div>
        <span className="text-sm font-mono text-zinc-500">
          {Math.round(progress)}%
        </span>
      </div>

      {/* Progress bar */}
      <div className="relative h-1.5 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={`h-full rounded-full transition-all duration-700 ease-out ${
            isFailed ? "bg-red-500" : isCompleted ? "bg-emerald-500" : config.bgColor
          }`}
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>

      {/* Phase indicators */}
      <div className="flex items-center gap-1.5">
        {PHASES.map((phase, idx) => {
          const isDone = config.phase > idx;
          const isActive = config.phase === idx;
          return (
            <div key={phase.key} className="flex flex-1 items-center gap-1.5">
              <div
                className={`flex-1 rounded-full transition-all ${
                  isDone
                    ? "h-1 bg-emerald-500/60"
                    : isActive
                      ? `h-1.5 ${config.bgColor}`
                      : "h-1 bg-zinc-800"
                }`}
              />
              {idx < PHASES.length - 1 && (
                <div
                  className={`h-1 w-1 shrink-0 rounded-full ${
                    isDone ? "bg-emerald-500/60" : "bg-zinc-800"
                  }`}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Phase labels */}
      <div className="flex">
        {PHASES.map((phase, idx) => {
          const isDone = config.phase > idx;
          const isActive = config.phase === idx;
          return (
            <span
              key={phase.key}
              className={`flex-1 text-center text-xs ${
                isActive
                  ? config.color + " font-medium"
                  : isDone
                    ? "text-zinc-400"
                    : "text-zinc-600"
              }`}
            >
              {phase.label}
            </span>
          );
        })}
      </div>

      {/* Error */}
      {isFailed && errorMessage && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <span className="font-medium">Error:</span> {errorMessage}
        </div>
      )}
    </div>
  );
}
