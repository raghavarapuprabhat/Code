import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { useIndexProject } from "@/hooks/useIndexProject";

interface Props {
  open: boolean;
  onClose: () => void;
  onIndexed?: (result: unknown) => void;
  initialPath?: string;
  initialName?: string;
}

export function IndexProjectDialog({ open, onClose, onIndexed, initialPath, initialName }: Props) {
  const [projectPath, setProjectPath] = useState(initialPath ?? "");
  const [displayName, setDisplayName] = useState(initialName ?? "");
  const [mode, setMode] = useState<"full" | "incremental">("full");
  const index = useIndexProject();

  useEffect(() => {
    if (open) {
      setProjectPath(initialPath ?? "");
      setDisplayName(initialName ?? "");
      setMode("full");
    }
  }, [open, initialPath, initialName]);

  if (!open) return null;

  const submit = async () => {
    if (!projectPath.trim()) return;
    try {
      const result = await index.mutateAsync({
        projectPath: projectPath.trim(),
        mode,
        displayName: displayName.trim() || undefined,
      });
      onIndexed?.(result);
      onClose();
      setProjectPath("");
      setDisplayName("");
      setMode("full");
    } catch {
      // error surfaced via index.isError below
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 p-4">
      <div className="w-full max-w-md rounded-lg border bg-background shadow-card">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h3 className="text-sm font-semibold">Index a project</h3>
          <button onClick={onClose} className="text-muted hover:text-foreground" title="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Absolute path to project folder (Java or React)
            </label>
            <input
              value={projectPath}
              onChange={(e) => setProjectPath(e.target.value)}
              placeholder="/Users/me/code/my-service"
              autoFocus
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Display name (optional)
            </label>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="My Service"
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Mode</label>
            <div className="flex overflow-hidden rounded-md border">
              {(["full", "incremental"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={
                    "flex-1 px-3 py-1.5 text-sm capitalize transition-colors " +
                    (mode === m
                      ? "bg-primary text-primary-foreground"
                      : "bg-background text-muted hover:bg-surface-2")
                  }
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          {index.isError && (
            <p className="text-xs text-danger">
              {(index.error as Error)?.message ?? "Indexing failed."}
            </p>
          )}
          {index.isPending && (
            <p className="text-xs text-muted">
              Indexing… this can take several minutes for a large repo.
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t px-4 py-3">
          <button
            onClick={onClose}
            disabled={index.isPending}
            className="rounded-md border px-3 py-1.5 text-sm text-muted hover:bg-surface-2 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={index.isPending || !projectPath.trim()}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-40"
          >
            {index.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {index.isPending ? "Indexing…" : "Index"}
          </button>
        </div>
      </div>
    </div>
  );
}
