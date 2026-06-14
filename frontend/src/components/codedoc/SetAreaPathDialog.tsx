import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { useSetRequirements } from "@/hooks/useHubInsights";

interface Props {
  open: boolean;
  projectId?: string;
  initialAreaPath?: string;
  onClose: () => void;
  onSaved?: () => void;
}

/**
 * Set the ADO requirements area path for a project (architecture §8.9.1). Saving triggers
 * an incremental re-index that ingests the matching work items and builds the traceability
 * matrix — so the dialog warns that it can take a while.
 */
export function SetAreaPathDialog({ open, projectId, initialAreaPath, onClose, onSaved }: Props) {
  const [areaPath, setAreaPath] = useState(initialAreaPath ?? "");
  const setReq = useSetRequirements(projectId);

  useEffect(() => {
    if (open) setAreaPath(initialAreaPath ?? "");
  }, [open, initialAreaPath]);

  if (!open) return null;

  const submit = async () => {
    if (!areaPath.trim() || !projectId) return;
    try {
      await setReq.mutateAsync(areaPath.trim());
      onSaved?.();
      onClose();
    } catch {
      // surfaced below via setReq.isError
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 p-4">
      <div className="w-full max-w-md rounded-lg border bg-background shadow-card">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h3 className="text-sm font-semibold">Set requirements area path</h3>
          <button onClick={onClose} className="text-muted hover:text-foreground" title="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              ADO area path (work items under this path are ingested + traced)
            </label>
            <input
              value={areaPath}
              onChange={(e) => setAreaPath(e.target.value)}
              placeholder={"Contoso\\Team A\\OrderService"}
              autoFocus
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <p className="mt-1 text-[11px] text-muted">
              Use the ADO area path exactly as shown in Azure Boards (backslash-separated).
              Requires ADO MCP configured in <code>.env</code>.
            </p>
          </div>

          {setReq.isError && (
            <p className="text-xs text-danger">
              {(setReq.error as Error)?.message ?? "Failed to set area path."}
            </p>
          )}
          {setReq.isPending && (
            <p className="text-xs text-muted">
              Ingesting requirements + re-indexing… this can take a few minutes.
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t px-4 py-3">
          <button
            onClick={onClose}
            disabled={setReq.isPending}
            className="rounded-md border px-3 py-1.5 text-sm text-muted hover:bg-surface-2 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={setReq.isPending || !areaPath.trim()}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-40"
          >
            {setReq.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {setReq.isPending ? "Saving…" : "Save & ingest"}
          </button>
        </div>
      </div>
    </div>
  );
}
