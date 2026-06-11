import { useQuery } from "@tanstack/react-query";
import { api, type DocFormat } from "@/lib/api";
import { qk } from "@/lib/queryKeys";

export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: () => api.listProjects() });
}

export function useDocs(projectId: string | undefined) {
  return useQuery({
    queryKey: projectId ? qk.docs(projectId) : ["docs", "none"],
    queryFn: () => api.listDocs(projectId!),
    enabled: !!projectId,
  });
}

export function useDoc(
  projectId: string | undefined,
  docId: string | undefined,
  format: DocFormat,
) {
  return useQuery({
    queryKey: projectId && docId ? qk.doc(projectId, docId, format) : ["doc", "none"],
    queryFn: () => api.getDoc(projectId!, docId!, format),
    enabled: !!projectId && !!docId,
  });
}
