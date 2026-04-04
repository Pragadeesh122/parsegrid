/**
 * ParseGrid — TanStack Query hooks for job data fetching.
 */

"use client";

import {useQuery, useMutation, useQueryClient} from "@tanstack/react-query";
import {api, type Job, type JobListResponse} from "@/lib/api-client";

// --- Queries ---

export function useJobs(token: string) {
  return useQuery<JobListResponse>({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(token),
    enabled: !!token,
    refetchInterval: 3000, // Poll every 10s for new jobs
  });
}

export function useJob(id: string, token: string) {
  return useQuery<Job>({
    queryKey: ["job", id],
    queryFn: () => api.getJob(id, token),
    enabled: !!token && !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "COMPLETED" || status === "FAILED") return false;
      return 3000; // Poll every 3s as SSE fallback for active jobs
    },
  });
}

export function useJobStatus(id: string, token: string, enabled = true) {
  return useQuery({
    queryKey: ["jobStatus", id],
    queryFn: () => api.getJobStatus(id, token),
    enabled: !!token && !!id && enabled,
    refetchInterval: 3000, // Poll every 3s as SSE fallback
  });
}

// --- Mutations ---

export function useCreateJob(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: {
      filename: string;
      file_key: string;
      file_size: number;
      output_format?: string;
    }) => api.createJob(data, token),
    onSuccess: () => {
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useApproveSchema(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      jobId,
      schema,
    }: {
      jobId: string;
      schema: Record<string, unknown>;
    }) => api.approveSchema(jobId, schema, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}
