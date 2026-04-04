/**
 * ParseGrid — Data preview table for completed extraction jobs.
 *
 * Fetches the first 20 records from GET /api/v1/jobs/{id}/data-preview
 * and renders them in a horizontally scrollable table with column headers
 * derived from the extracted schema.
 */

"use client";

import { useEffect, useState } from "react";
import type { DataPreviewResponse } from "@/lib/api-client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DataPreviewProps {
  jobId: string;
  token: string;
}

export function DataPreview({ jobId, token }: DataPreviewProps) {
  const [data, setData] = useState<DataPreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchPreview = async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/jobs/${jobId}/data-preview`,
          {
            headers: { Authorization: `Bearer ${token}` },
          },
        );
        if (!res.ok) {
          if (res.status === 400) {
            setError("No extracted data available yet");
          } else {
            throw new Error("Failed to load preview");
          }
          return;
        }
        const json = await res.json();
        setData(json);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    };
    fetchPreview();
  }, [jobId, token]);

  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="flex items-center gap-3">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          <span className="text-sm text-zinc-400">Loading data preview...</span>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return null;
  }

  if (data.preview.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <p className="text-sm text-zinc-500">No records extracted.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
          Extracted Data
        </h3>
        <span className="rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-400">
          {data.total_records} record{data.total_records !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-zinc-800">
        <table className="w-full min-w-[600px]">
          <thead>
            <tr className="border-b border-zinc-800 bg-zinc-900/80">
              <th className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-zinc-500 w-12">
                #
              </th>
              {data.columns.map((col) => (
                <th
                  key={col}
                  className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-zinc-500"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.preview.map((row, i) => (
              <tr
                key={i}
                className="border-b border-zinc-800/50 last:border-0 transition-colors hover:bg-zinc-800/30"
              >
                <td className="px-4 py-2 text-xs text-zinc-600 font-mono">
                  {i + 1}
                </td>
                {data.columns.map((col) => (
                  <td
                    key={col}
                    className="px-4 py-2 text-sm text-zinc-300 max-w-[300px] truncate"
                    title={String(row[col] ?? "")}
                  >
                    {row[col] != null ? String(row[col]) : (
                      <span className="text-zinc-600 italic">null</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.total_records > 20 && (
        <p className="text-xs text-zinc-500 text-center">
          Showing first 20 of {data.total_records} records
        </p>
      )}
    </div>
  );
}
