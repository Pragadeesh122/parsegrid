/**
 * ParseGrid — Real-time job status progress component.
 */

"use client";

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; bgColor: string; step: number }
> = {
  UPLOADED: {
    label: "Uploaded",
    color: "text-zinc-400",
    bgColor: "bg-zinc-600",
    step: 0,
  },
  OCR_PROCESSING: {
    label: "OCR Processing",
    color: "text-blue-400",
    bgColor: "bg-blue-500",
    step: 1,
  },
  SCHEMA_PROPOSED: {
    label: "Schema Proposed",
    color: "text-amber-400",
    bgColor: "bg-amber-500",
    step: 2,
  },
  AWAITING_REVIEW: {
    label: "Awaiting Review",
    color: "text-amber-400",
    bgColor: "bg-amber-500",
    step: 2,
  },
  SCHEMA_LOCKED: {
    label: "Schema Locked",
    color: "text-indigo-400",
    bgColor: "bg-indigo-500",
    step: 3,
  },
  EXTRACTING: {
    label: "Extracting Data",
    color: "text-purple-400",
    bgColor: "bg-purple-500",
    step: 4,
  },
  MERGING: {
    label: "Merging Results",
    color: "text-violet-400",
    bgColor: "bg-violet-500",
    step: 5,
  },
  TRANSLATING: {
    label: "Translating to SQL",
    color: "text-cyan-400",
    bgColor: "bg-cyan-500",
    step: 6,
  },
  PROVISIONING: {
    label: "Provisioning Database",
    color: "text-teal-400",
    bgColor: "bg-teal-500",
    step: 7,
  },
  COMPLETED: {
    label: "Completed",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500",
    step: 8,
  },
  FAILED: {
    label: "Failed",
    color: "text-red-400",
    bgColor: "bg-red-500",
    step: -1,
  },
};

const STEPS = [
  "Upload",
  "OCR",
  "Schema",
  "Lock",
  "Extract",
  "Merge",
  "Translate",
  "Provision",
  "Done",
];

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
    <div className="space-y-4">
      {/* Status Badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {/* Animated pulse for active states */}
          {!isCompleted && !isFailed && (
            <span className="relative flex h-3 w-3">
              <span
                className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${config.bgColor}`}
              />
              <span
                className={`relative inline-flex h-3 w-3 rounded-full ${config.bgColor}`}
              />
            </span>
          )}
          <span className={`text-sm font-semibold ${config.color}`}>
            {config.label}
          </span>
        </div>
        <span className="text-sm font-mono text-zinc-500">
          {Math.round(progress)}%
        </span>
      </div>

      {/* Progress Bar */}
      <div className="relative h-2 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={`h-full rounded-full transition-all duration-700 ease-out ${
            isFailed ? "bg-red-500" : isCompleted ? "bg-emerald-500" : config.bgColor
          }`}
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>

      {/* Step Indicators */}
      <div className="flex justify-between">
        {STEPS.map((step, idx) => {
          const isActive = config.step >= idx;
          const isCurrent = config.step === idx;
          return (
            <div key={step} className="flex flex-col items-center">
              <div
                className={`h-2 w-2 rounded-full transition-all ${
                  isCurrent
                    ? `${config.bgColor} ring-2 ring-offset-1 ring-offset-zinc-950 ring-${config.bgColor.replace("bg-", "")}/50`
                    : isActive
                      ? config.bgColor
                      : "bg-zinc-700"
                }`}
              />
              <span
                className={`mt-1 text-[10px] ${
                  isActive ? "text-zinc-400" : "text-zinc-600"
                }`}
              >
                {step}
              </span>
            </div>
          );
        })}
      </div>

      {/* Error Message */}
      {isFailed && errorMessage && (
        <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <span className="font-medium">Error:</span> {errorMessage}
        </div>
      )}
    </div>
  );
}
