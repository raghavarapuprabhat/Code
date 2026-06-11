import { useMemo } from "react";
import { FileText } from "lucide-react";
import type { CodeProject, DocSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  projects: CodeProject[];
  selectedProjectId?: string;
  onSelectProject: (id: string) => void;
  docs: DocSummary[];
  selectedDocId?: string;
  onSelectDoc: (docId: string) => void;
  loadingDocs?: boolean;
}

const AUDIENCE_LABEL: Record<string, string> = {
  management: "Management",
  architecture: "Architecture",
  developer: "Developer",
};
const AUDIENCE_ORDER = ["management", "architecture", "developer"];

export function DocTree({
  projects,
  selectedProjectId,
  onSelectProject,
  docs,
  selectedDocId,
  onSelectDoc,
  loadingDocs,
}: Props) {
  const grouped = useMemo(() => {
    const by: Record<string, DocSummary[]> = {};
    for (const d of [...docs].sort((a, b) => a.sort_order - b.sort_order)) {
      const key = d.audience || "developer";
      (by[key] ??= []).push(d);
    }
    const keys = Object.keys(by).sort(
      (a, b) =>
        (AUDIENCE_ORDER.indexOf(a) + 1 || 99) - (AUDIENCE_ORDER.indexOf(b) + 1 || 99),
    );
    return keys.map((k) => ({ audience: k, docs: by[k] }));
  }, [docs]);

  return (
    <aside className="flex h-full w-72 flex-col border-r bg-surface">
      <div className="border-b p-3">
        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
          Project
        </label>
        <select
          value={selectedProjectId ?? ""}
          onChange={(e) => onSelectProject(e.target.value)}
          className="w-full rounded-md border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
        >
          <option value="" disabled>
            Select a project…
          </option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name || p.project_path.split("/").slice(-1)[0]}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {loadingDocs && <p className="p-2 text-sm text-muted">Loading documents…</p>}
        {!loadingDocs && selectedProjectId && docs.length === 0 && (
          <p className="p-2 text-sm text-muted">No documents for this project.</p>
        )}
        {grouped.map((g) => (
          <div key={g.audience} className="mb-3">
            <div className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted">
              {AUDIENCE_LABEL[g.audience] ?? g.audience}
            </div>
            {g.docs.map((d) => (
              <button
                key={d.doc_id}
                onClick={() => onSelectDoc(d.doc_id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                  selectedDocId === d.doc_id
                    ? "bg-surface-2 font-medium text-primary"
                    : "text-foreground hover:bg-surface-2",
                )}
              >
                <FileText className="h-4 w-4 shrink-0 text-muted" />
                <span className="truncate">{d.title}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
