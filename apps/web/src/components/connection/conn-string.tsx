/**
 * ParseGrid — Connection string display component with provisioning audit data.
 */

"use client";

import { useState } from "react";

interface ConnectionStringProps {
  connectionString: string;
  provisionedRows?: number | null;
  provisionedAt?: string | null;
}

export function ConnectionString({
  connectionString,
  provisionedRows,
  provisionedAt,
}: ConnectionStringProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(connectionString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-6 space-y-4">
      <div className="flex items-center gap-3">
        <div className="rounded-full bg-emerald-500/20 p-2">
          <svg
            className="h-5 w-5 text-emerald-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <div>
          <h4 className="font-semibold text-emerald-300">Data Ready!</h4>
          <p className="text-sm text-zinc-400">
            Connect to your structured data using the connection string below.
          </p>
        </div>
      </div>

      {/* Audit stats */}
      {(provisionedRows != null || provisionedAt) && (
        <div className="flex gap-6 text-sm">
          {provisionedRows != null && (
            <div>
              <span className="text-zinc-500">Rows inserted: </span>
              <span className="font-medium text-emerald-400">
                {provisionedRows.toLocaleString()}
              </span>
            </div>
          )}
          {provisionedAt && (
            <div>
              <span className="text-zinc-500">Provisioned: </span>
              <span className="font-medium text-zinc-300">
                {new Date(provisionedAt).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="relative group">
        <code className="block rounded-lg bg-zinc-900 border border-zinc-700 px-4 py-3 text-sm text-zinc-300 font-mono break-all">
          {connectionString}
        </code>
        <button
          onClick={handleCopy}
          className="absolute right-2 top-2 rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200 transition-colors opacity-0 group-hover:opacity-100"
        >
          {copied ? (
            <span className="text-emerald-400">Copied!</span>
          ) : (
            "Copy"
          )}
        </button>
      </div>

      <p className="text-xs text-zinc-500">
        Use this connection string with psql, DBeaver, or any PostgreSQL client.
      </p>
    </div>
  );
}
