/**
 * ParseGrid — TanStack Query hooks for job data fetching.
 */

"use client";

import {useQuery, useMutation, useQueryClient} from "@tanstack/react-query";
import {api, type DatabaseModel, type Job, type JobListResponse} from "@/lib/api-client";

// Job statuses where polling should pause — the user (or the LLM) needs to do
// something before progress resumes. Includes Phase 7 review states.
const IDLE_STATUSES = new Set([
  "COMPLETED",
  "FAILED",
  "MODEL_PROPOSED",
  "AWAITING_REVIEW",
  "AWAITING_QUERY",
]);

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
      if (status && IDLE_STATUSES.has(status)) return false;
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

export function useApproveModel(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      jobId,
      model,
    }: {
      jobId: string;
      model: DatabaseModel;
    }) => api.approveModel(jobId, model, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useRejectModel(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId: string) => api.rejectModel(jobId, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useDeleteJob(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId: string) => api.deleteJob(jobId, token),
    onSuccess: (_, jobId) => {
      queryClient.removeQueries({queryKey: ["job", jobId]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useTargetQuery(token: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({jobId, query}: {jobId: string; query: string}) =>
      api.targetQuery(jobId, query, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}
