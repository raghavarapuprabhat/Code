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
