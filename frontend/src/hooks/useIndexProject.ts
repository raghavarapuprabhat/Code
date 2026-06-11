import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { qk } from "@/lib/queryKeys";

export interface IndexArgs {
  projectPath: string;
  mode: "full" | "incremental";
  displayName?: string;
}

/**
 * Trigger code_doc indexing. On success, refresh the project list so the new
 * (or re-indexed) project and its updated last_indexed appear.
 */
export function useIndexProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectPath, mode, displayName }: IndexArgs) =>
      api.indexProject(projectPath, mode, displayName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects });
    },
  });
}
