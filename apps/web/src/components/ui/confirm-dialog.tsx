"use client";

import { useEffect } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  confirmIcon?: React.ReactNode;
  isPending?: boolean;
  errorMessage?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  confirmIcon,
  isPending = false,
  errorMessage,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isPending) {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, isPending, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center px-4">
      <button
        type="button"
        aria-label="Close dialog"
        onClick={isPending ? undefined : onClose}
        className="absolute inset-0 bg-zinc-950/78 backdrop-blur-sm"
      />

      <div className="relative w-full max-w-md rounded-[28px] border border-zinc-800/80 bg-zinc-950/98 p-6 shadow-[0_24px_80px_rgba(0,0,0,0.55)]">
        <div className="flex items-start gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-red-500/20 bg-red-500/10 text-red-300">
            {confirmIcon}
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight text-zinc-100">
              {title}
            </h2>
            <p className="mt-2 text-sm leading-6 text-zinc-400">
              {description}
            </p>
          </div>
        </div>

        {errorMessage && (
          <div className="mt-5 rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {errorMessage}
          </div>
        )}

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={isPending}
            className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-2.5 text-sm font-medium text-zinc-300 transition-colors hover:border-zinc-700 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isPending}
            className="inline-flex min-w-[132px] items-center justify-center gap-2 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isPending ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            ) : (
              confirmIcon
            )}
            {isPending ? "Deleting..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
