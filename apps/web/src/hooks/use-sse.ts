/**
 * ParseGrid — SSE hook for real-time job status updates.
 * Uses the native browser EventSource API.
 * NO WebSocket. NO socket.io.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface SSEStatus {
  status: string;
  progress: number;
  error_message?: string;
  connection_string?: string;
}

interface UseSSEOptions {
  /** The job ID to subscribe to */
  jobId: string;
  /** JWT token for authorization */
  token: string;
  /** Whether SSE is enabled (disable for completed/failed jobs) */
  enabled?: boolean;
  /** Callback when a status event is received */
  onStatus?: (data: SSEStatus) => void;
  /** Callback when an error occurs */
  onError?: (error: Event) => void;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function useSSE({
  jobId,
  token,
  enabled = true,
  onStatus,
  onError,
}: UseSSEOptions) {
  const [status, setStatus] = useState<SSEStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const onStatusRef = useRef(onStatus);
  const onErrorRef = useRef(onError);

  // Keep callback refs up to date
  onStatusRef.current = onStatus;
  onErrorRef.current = onError;

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled || !jobId || !token) {
      disconnect();
      return;
    }

    // EventSource doesn't support custom headers natively,
    // so we pass the token as a query parameter
    const url = `${API_BASE}/api/v1/jobs/${jobId}/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setError(null);
    };

    es.addEventListener("status", (event) => {
      try {
        const data: SSEStatus = JSON.parse(event.data);
        setStatus(data);
        onStatusRef.current?.(data);

        // Auto-close on terminal states
        if (data.status === "COMPLETED" || data.status === "FAILED") {
          es.close();
          setConnected(false);
        }
      } catch {
        console.error("Failed to parse SSE data:", event.data);
      }
    });

    es.addEventListener("error", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        setError(data.error || "SSE error");
      } catch {
        // Generic SSE error (connection lost, etc.)
      }
      onErrorRef.current?.(event);
    });

    es.onerror = () => {
      setConnected(false);
      // EventSource auto-reconnects by default
    };

    return () => {
      es.close();
      setConnected(false);
    };
  }, [jobId, token, enabled, disconnect]);

  return { status, connected, error, disconnect };
}
