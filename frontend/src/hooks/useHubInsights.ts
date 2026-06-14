import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Latest golden-Q&A eval score for the Hub badge (§8.9.3). */
export function useLatestEval(projectId: string | undefined) {
  return useQuery({
    queryKey: ["eval", projectId],
    queryFn: () => api.getLatestEval(projectId!),
    enabled: !!projectId,
  });
}

/** Architecture change-digest entries — the "What changed" panel (§8.9.4). */
export function useDigest(projectId: string | undefined) {
  return useQuery({
    queryKey: ["digest", projectId],
    queryFn: () => api.getDigest(projectId!),
    enabled: !!projectId,
  });
}

/** Run-status strip data for the landing page (§13B.1 v0.7). */
export function useLatestRun(projectId: string | undefined) {
  return useQuery({
    queryKey: ["run", projectId],
    queryFn: () => api.getLatestRun(projectId!),
    enabled: !!projectId,
  });
}

/** Trigger an on-demand eval and refresh the badge. */
export function useRunEval(projectId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.runEval(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", projectId] }),
  });
}

/** Reader feedback per document section (§8.9.9). */
export function useDocFeedback(projectId: string | undefined, docId: string | undefined) {
  return useMutation({
    mutationFn: (vars: { rating: number; comment?: string }) =>
      api.submitDocFeedback(projectId!, docId!, {
        doc_id: docId!,
        rating: vars.rating,
        comment: vars.comment,
      }),
  });
}

/**
 * Set the ADO requirements area path for a project (§8.9.1). This triggers an incremental
 * re-index that ingests work items + builds the traceability matrix, so it can take a
 * while; on success we invalidate the trace + run queries so the UI refreshes.
 */
export function useSetRequirements(projectId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (areapath: string) => api.setRequirements(projectId!, areapath),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trace", projectId] });
      qc.invalidateQueries({ queryKey: ["run", projectId] });
    },
  });
}
