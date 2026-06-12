/**
 * ParseGrid — Force/cancel review for an append that failed the compat gate.
 */

"use client";

import type {JobDocument} from "@/lib/api-client";

interface AppendReviewProps {
  document: JobDocument;
  onResolve: (action: "force" | "cancel") => void;
  isSubmitting: boolean;
}

export function AppendReview({document, onResolve, isSubmitting}: AppendReviewProps) {
  const report = document.compat_report;
  if (!report) return null;

  return (
    <div className='rounded-2xl border border-amber-500/20 bg-amber-500/5 p-6'>
      <h3 className='text-sm font-semibold text-amber-300'>
        &ldquo;{document.filename}&rdquo; doesn&rsquo;t fit this dataset cleanly
      </h3>
      <ul className='mt-2 list-disc pl-5 text-sm text-zinc-400'>
        {report.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      <dl className='mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4'>
        <div>
          <dt className='text-zinc-500'>Rows extracted</dt>
          <dd className='mt-1 font-mono text-zinc-200'>{report.total_rows}</dd>
        </div>
        {Object.entries(report.rows_per_table).map(([table, count]) => (
          <div key={table}>
            <dt className='truncate text-zinc-500'>{table}</dt>
            <dd className='mt-1 font-mono text-zinc-200'>{count}</dd>
          </div>
        ))}
      </dl>
      <div className='mt-5 flex gap-3'>
        <button
          onClick={() => onResolve("force")}
          disabled={isSubmitting}
          className='rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:opacity-50'>
          Add what fits
        </button>
        <button
          onClick={() => onResolve("cancel")}
          disabled={isSubmitting}
          className='rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-medium text-zinc-300 transition-all hover:border-zinc-600 active:scale-[0.98] disabled:opacity-50'>
          Discard this file
        </button>
      </div>
    </div>
  );
}
