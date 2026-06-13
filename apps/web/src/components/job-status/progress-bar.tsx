/**
 * ParseGrid — Real-time job progress component.
 * 5 pipeline phases, emerald accent throughout.
 */

"use client";

// Phase 7 pipeline: Upload → OCR → Profile → Review → Extract → Reconcile → Provision.
const PHASES = [
  { key: "upload", label: "Upload" },
  { key: "ocr", label: "OCR" },
  { key: "profile", label: "Profile" },
  { key: "review", label: "Review" },
  { key: "extract", label: "Extract" },
  { key: "reconcile", label: "Reconcile" },
  { key: "provision", label: "Provision" },
] as const;

// Each status owns a band of the overall 0–100 bar (`phase` drives the phase
// indicators below). `band` is [start, end]: the bar fills from `start` to
// `end` as the backend's per-stage progress goes 0→100, so overall progress
// only ever moves forward — it never resets to 0 between pipeline stages.
const STATUS_CONFIG: Record<
  string,
  { label: string; phase: number; band: [number, number] }
> = {
  UPLOADED: { label: "Uploaded", phase: 0, band: [0, 5] },
  OCR_PROCESSING: { label: "Reading document", phase: 1, band: [5, 25] },
  INDEXING: { label: "Indexing chunks", phase: 1, band: [5, 25] },
  AWAITING_QUERY: { label: "Ready for your query", phase: 3, band: [25, 25] },
  PROFILING: { label: "Profiling document", phase: 2, band: [25, 40] },
  MODEL_PROPOSED: { label: "Model ready for review", phase: 3, band: [40, 40] },
  AWAITING_REVIEW: { label: "Awaiting your review", phase: 3, band: [40, 40] },
  MODEL_LOCKED: { label: "Model locked", phase: 3, band: [45, 48] },
  EXTRACTING: { label: "Extracting data", phase: 4, band: [48, 70] },
  MERGING: { label: "Merging results", phase: 4, band: [70, 78] },
  RECONCILING: { label: "Reconciling rows", phase: 5, band: [78, 86] },
  TRANSLATING: { label: "Generating DDL", phase: 5, band: [86, 92] },
  PROVISIONING: { label: "Provisioning database", phase: 6, band: [92, 99] },
  COMPLETED: { label: "Completed", phase: 7, band: [100, 100] },
  FAILED: { label: "Failed", phase: -1, band: [0, 0] },
  APPENDING: { label: "Adding Data", phase: 5, band: [48, 78] },
  AWAITING_APPEND_REVIEW: { label: "Review Append", phase: 5, band: [78, 78] },
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

  // Map the per-stage progress (0–100, resets each stage) into this status's
  // band so the displayed bar is monotonic across the whole pipeline.
  const [bandStart, bandEnd] = config.band;
  const stageFraction = Math.min(Math.max(progress, 0), 100) / 100;
  const overall = isFailed
    ? 0
    : Math.round(bandStart + (bandEnd - bandStart) * stageFraction);

  // Awaiting-user and terminal states are not "working"; everything else is.
  const isActive = !isCompleted && !isFailed && !status.startsWith("AWAITING");

  return (
    <div className="space-y-5">
      {/* Status header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {!isCompleted && !isFailed && (
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
          )}
          {isCompleted && (
            <svg className="h-4 w-4 text-emerald-500" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clipRule="evenodd" />
            </svg>
          )}
          {isFailed && (
            <svg className="h-4 w-4 text-red-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
            </svg>
          )}
          <span className={`text-sm font-medium ${isFailed ? "text-red-400" : "text-zinc-200"}`}>
            {config.label}
          </span>
        </div>
        <span className="text-sm font-mono text-zinc-500">
          {isFailed ? "—" : `${overall}%`}
        </span>
      </div>

      {/* Progress bar — overall (monotonic) fill with an activity shimmer. */}
      <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={`relative h-full overflow-hidden rounded-full transition-all duration-700 ease-out ${
            isFailed ? "bg-red-500" : "bg-emerald-500"
          } ${isActive ? "progress-shimmer" : ""}`}
          style={{ width: isFailed ? "100%" : `${Math.max(overall, 2)}%` }}
        />
      </div>

      {/* Phase indicators */}
      <div className="flex items-center gap-1">
        {PHASES.map((phase, idx) => {
          const isDone = config.phase > idx;
          const isActive = config.phase === idx;
          return (
            <div key={phase.key} className="flex flex-1 items-center gap-1">
              <div
                className={`flex-1 rounded-full transition-all ${
                  isDone
                    ? "h-1 bg-emerald-500/40"
                    : isActive
                      ? "h-1.5 bg-emerald-500"
                      : "h-1 bg-zinc-800"
                }`}
              />
              {idx < PHASES.length - 1 && (
                <div
                  className={`h-0.5 w-0.5 shrink-0 rounded-full ${
                    isDone ? "bg-emerald-500/40" : "bg-zinc-800"
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
                  ? "font-medium text-emerald-400"
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
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300">
          <span className="font-medium">Error:</span> {errorMessage}
        </div>
      )}
    </div>
  );
}
