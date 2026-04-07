/**
 * ParseGrid — Phase 7 multi-table data preview.
 *
 * Backend now returns `{tables: {table_name: {total_records, preview, columns}}}`.
 * When there's more than one table the user gets a small tab strip; with one
 * table the strip collapses out of the way.
 */

"use client";

import {useEffect, useMemo, useState} from "react";
import type {DataPreviewResponse, TablePreview} from "@/lib/api-client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DataPreviewProps {
  jobId: string;
  token: string;
}

export function DataPreview({jobId, token}: DataPreviewProps) {
  const [data, setData] = useState<DataPreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTable, setActiveTable] = useState<string | null>(null);

  useEffect(() => {
    const fetchPreview = async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/jobs/${jobId}/data-preview`,
          {headers: {Authorization: `Bearer ${token}`}},
        );
        if (!res.ok) {
          if (res.status === 400) {
            setError("No extracted data available yet");
          } else {
            throw new Error("Failed to load preview");
          }
          return;
        }
        const json: DataPreviewResponse = await res.json();
        setData(json);
        const firstName = Object.keys(json.tables ?? {})[0] ?? null;
        setActiveTable(firstName);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    };
    fetchPreview();
  }, [jobId, token]);

  const tableNames = useMemo(
    () => (data ? Object.keys(data.tables) : []),
    [data],
  );

  if (loading) {
    return (
      <div className='rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6'>
        <div className='flex items-center gap-3'>
          <div className='h-4 w-4 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent' />
          <span className='text-sm text-zinc-500'>Loading preview...</span>
        </div>
      </div>
    );
  }

  if (error || !data) return null;

  if (tableNames.length === 0) {
    return (
      <div className='rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6'>
        <p className='text-sm text-zinc-500'>No tables extracted.</p>
      </div>
    );
  }

  const current: TablePreview | null =
    (activeTable && data.tables[activeTable]) || data.tables[tableNames[0]];

  if (!current) return null;

  return (
    <div className='rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6 space-y-4'>
      <div className='flex items-center justify-between'>
        <h3 className='text-xs font-semibold uppercase tracking-wider text-zinc-500'>
          Extracted Data
        </h3>
        <span className='rounded-lg bg-emerald-500/10 px-2.5 py-1 text-xs font-medium font-mono text-emerald-400'>
          {current.total_records} record{current.total_records !== 1 ? "s" : ""}
        </span>
      </div>

      {tableNames.length > 1 && (
        <div className='flex flex-wrap gap-1'>
          {tableNames.map((name) => {
            const isActive = name === activeTable;
            const count = data.tables[name].total_records;
            return (
              <button
                key={name}
                type='button'
                onClick={() => setActiveTable(name)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-mono transition-colors ${
                  isActive
                    ? "border-emerald-600/40 bg-emerald-500/10 text-emerald-300"
                    : "border-zinc-800 bg-zinc-900/40 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
                }`}>
                {name}
                <span className='ml-2 text-zinc-600'>{count}</span>
              </button>
            );
          })}
        </div>
      )}

      {current.preview.length === 0 ? (
        <p className='text-sm text-zinc-500'>No rows extracted for this table.</p>
      ) : (
        <div className='overflow-x-auto rounded-xl border border-zinc-800/60'>
          <table className='w-full min-w-[600px]'>
            <thead>
              <tr className='border-b border-zinc-800/60'>
                <th className='px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-zinc-600 w-10'>
                  #
                </th>
                {current.columns.map((col) => (
                  <th
                    key={col}
                    className='px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-zinc-500'>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className='divide-y divide-zinc-800/40'>
              {current.preview.map((row, i) => (
                <tr key={i} className='transition-colors hover:bg-zinc-800/20'>
                  <td className='px-4 py-2 text-xs font-mono text-zinc-600'>
                    {i + 1}
                  </td>
                  {current.columns.map((col) => (
                    <td
                      key={col}
                      className='px-4 py-2 text-sm text-zinc-300 max-w-[300px] truncate'
                      title={String(row[col] ?? "")}>
                      {row[col] != null ? String(row[col]) : (
                        <span className='text-zinc-700 italic'>null</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {current.total_records > current.preview.length && (
        <p className='text-xs text-zinc-600 text-center'>
          Showing first {current.preview.length} of {current.total_records} records
        </p>
      )}
    </div>
  );
}
