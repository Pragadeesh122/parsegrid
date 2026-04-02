/**
 * ParseGrid — API client.
 * Wraps fetch with JWT Bearer token from Auth.js session.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  token?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
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

  return response.json();
}

// --- Job API ---

export interface Job {
  id: string;
  user_id: string;
  filename: string;
  file_key: string;
  file_size: number;
  status: string;
  output_format: string;
  progress: number;
  proposed_schema: Record<string, unknown> | null;
  locked_schema: Record<string, unknown> | null;
  connection_string: string | null;
  error_message: string | null;
  page_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
}

export interface UploadUrlResponse {
  upload_url: string;
  file_key: string;
}

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

  getJobStatus: (id: string, token: string) =>
    request<{ id: string; status: string; progress: number; error_message: string | null; connection_string: string | null }>(
      `/api/v1/jobs/${id}/status`,
      { token },
    ),

  approveSchema: (id: string, schema: Record<string, unknown>, token: string) =>
    request<Job>(`/api/v1/jobs/${id}/approve-schema`, {
      method: "POST",
      body: { locked_schema: schema },
      token,
    }),
};
