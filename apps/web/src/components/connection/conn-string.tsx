/**
 * ParseGrid — Connection string display with provisioning audit data.
 */

"use client";

import { useState } from "react";

interface ConnectionStringProps {
  connectionString: string;
  outputFormat?: string;
  provisionedRows?: number | null;
  provisionedAt?: string | null;
}

export function ConnectionString({
  connectionString,
  outputFormat = "SQL",
  provisionedRows,
  provisionedAt,
}: ConnectionStringProps) {
  const [copied, setCopied] = useState(false);
  const normalizedFormat = outputFormat.toUpperCase();

  const handleCopy = () => {
    navigator.clipboard.writeText(connectionString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-6 space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10">
          <svg
            className="h-4 w-4 text-emerald-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <div>
          <h4 className="font-semibold text-emerald-400">Data Ready</h4>
          <p className="text-sm text-zinc-400">
            {normalizedFormat === "GRAPH"
              ? "Connect to your graph output using the resource below."
              : normalizedFormat === "VECTOR"
                ? "Connect to your vector output using the collection endpoint below."
                : "Connect to your structured data using the connection string below."}
          </p>
        </div>
      </div>

      {(provisionedRows != null || provisionedAt) && (
        <div className="flex gap-6 text-sm">
          {provisionedRows != null && (
            <div>
              <span className="text-zinc-500">Rows inserted: </span>
              <span className="font-medium font-mono text-emerald-400">
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

      <div className="group relative">
        <code className="block rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm font-mono text-zinc-300 break-all">
          {connectionString}
        </code>
        <button
          onClick={handleCopy}
          className="absolute right-2 top-2 rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-400 transition-all hover:bg-zinc-700 hover:text-zinc-200 active:scale-[0.98] opacity-0 group-hover:opacity-100"
        >
          {copied ? (
            <span className="text-emerald-400">Copied</span>
          ) : (
            "Copy"
          )}
        </button>
      </div>

      <p className="text-xs text-zinc-600">
        {normalizedFormat === "GRAPH"
          ? "Use this with Neo4j Browser, Cypher clients, or Bolt-compatible tooling."
          : normalizedFormat === "VECTOR"
            ? "Use this with Qdrant Cloud UI/API or any Qdrant client SDK."
            : "Use this connection string with psql, DBeaver, or any PostgreSQL client."}
      </p>
    </div>
  );
}
