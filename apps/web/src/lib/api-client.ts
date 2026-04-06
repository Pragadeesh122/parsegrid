/**
 * ParseGrid — API client with Auth.js JWT injection.
 *
 * Server Components: Use `getServerToken()` which calls `auth()`.
 * Client Components: Use `useToken()` hook or pass token as prop from server.
 *
 * IMPORTANT: Next.js NEVER touches the database. All data flows through FastAPI.
 */

import { auth } from "@/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---- Token Helpers ----

/**
 * Get the raw JWT string from the Auth.js session cookie.
 * For use in Server Components and Server Actions only.
 * Uses `await auth()` per Next.js 16 conventions (NOT getSession).
 */
export async function getServerToken(): Promise<string | null> {
  // Auth.js stores the session token in a cookie named
  // `__Secure-authjs.session-token` (prod) or `authjs.session-token` (dev).
  // We need the RAW cookie value (the JWS string), not the decoded session.
  const { cookies } = await import("next/headers");
  const cookieStore = await cookies();

  // Try secure cookie first (production), then non-secure (development)
  const tokenCookie =
    cookieStore.get("__Secure-authjs.session-token") ??
    cookieStore.get("authjs.session-token");

  return tokenCookie?.value ?? null;
}

/**
 * Get the decoded session from Auth.js.
 * For use in Server Components when you need user data (not the raw token).
 */
export async function getServerSession() {
  return await auth();
}

// ---- HTTP Client ----

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  token?: string | null;
}

async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, token, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      errorData.detail || `Request failed: ${response.statusText}`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// ---- Type Definitions ----

export type JobType = "FULL" | "TARGETED";

export interface Job {
  id: string;
  user_id: string;
  filename: string;
  file_key: string;
  file_size: number;
  status: string;
  job_type: JobType;
  output_format: string;
  progress: number;
  proposed_schema: Record<string, unknown> | null;
  locked_schema: Record<string, unknown> | null;
  connection_string: string | null;
  error_message: string | null;
  page_count: number | null;
  provisioned_rows: number | null;
  provisioned_at: string | null;
  target_ddl: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectionTestRequest {
  connection_string: string;
  output_format?: string;
}

export interface ConnectionTestResponse {
  success: boolean;
  message: string;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
}

export interface UploadUrlResponse {
  upload_url: string;
  file_key: string;
}

export interface DataPreviewResponse {
  total_records: number;
  preview: Record<string, unknown>[];
  columns: string[];
}

// ---- API Methods ----

export const api = {
  // Upload
  getPresignedUrl: (filename: string, token: string) =>
    request<UploadUrlResponse>(
      `/api/v1/upload/presigned-url?filename=${encodeURIComponent(filename)}`,
      { method: "POST", token },
    ),

  // Jobs
  createJob: (
    data: {
      filename: string;
      file_key: string;
      file_size: number;
      output_format?: string;
      job_type?: JobType;
    },
    token: string,
  ) =>
    request<Job>("/api/v1/jobs", {
      method: "POST",
      body: data,
      token,
    }),

  listJobs: (token: string, skip = 0, limit = 20) =>
    request<JobListResponse>(
      `/api/v1/jobs?skip=${skip}&limit=${limit}`,
      { token },
    ),

  getJob: (id: string, token: string) =>
    request<Job>(`/api/v1/jobs/${id}`, { token }),

  deleteJob: (id: string, token: string) =>
    request<void>(`/api/v1/jobs/${id}`, {
      method: "DELETE",
      token,
    }),

  getJobStatus: (id: string, token: string) =>
    request<{
      id: string;
      status: string;
      progress: number;
      error_message: string | null;
      connection_string: string | null;
    }>(`/api/v1/jobs/${id}/status`, { token }),

  approveSchema: (
    id: string,
    schema: Record<string, unknown>,
    token: string,
  ) =>
    request<Job>(`/api/v1/jobs/${id}/approve-schema`, {
      method: "POST",
      body: { locked_schema: schema },
      token,
    }),

  rejectSchema: (id: string, token: string) =>
    request<Job>(`/api/v1/jobs/${id}/reject-schema`, {
      method: "POST",
      token,
    }),

  getDataPreview: (id: string, token: string) =>
    request<DataPreviewResponse>(`/api/v1/jobs/${id}/data-preview`, {
      token,
    }),

  targetQuery: (id: string, query: string, token: string) =>
    request<Job>(`/api/v1/jobs/${id}/target-query`, {
      method: "POST",
      body: { query },
      token,
    }),

  testConnection: (data: ConnectionTestRequest, token: string) =>
    request<ConnectionTestResponse>("/api/v1/connections/test", {
      method: "POST",
      body: data,
      token,
    }),
};
