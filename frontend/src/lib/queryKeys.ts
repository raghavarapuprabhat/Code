export const qk = {
  projects: ["projects"] as const,
  docs: (projectId: string) => ["docs", projectId] as const,
  doc: (projectId: string, docId: string, format: string) =>
    ["doc", projectId, docId, format] as const,
};
