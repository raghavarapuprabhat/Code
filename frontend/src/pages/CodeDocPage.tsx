import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileCode2, FolderOpen, Plus, RefreshCw, ExternalLink, Clock } from "lucide-react";
import { useProjects } from "@/hooks/useDocs";
import { IndexProjectDialog } from "@/components/codedoc/IndexProjectDialog";
import type { CodeProject } from "@/lib/api";

function formatDate(iso: string | null) {
  if (!iso) return null;
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ProjectCard({ project, onReindex }: { project: CodeProject; onReindex: (p: CodeProject) => void }) {
  const navigate = useNavigate();
  const indexed = !!project.last_indexed;

  return (
    <div className="rounded-lg border bg-background shadow-card flex flex-col gap-0 overflow-hidden">
      <div className="flex items-start gap-3 p-4">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <FileCode2 className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-sm">
            {project.display_name || project.id}
          </p>
          <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted">
            <FolderOpen className="h-3 w-3 shrink-0" />
            {project.project_path}
          </p>
        </div>
      </div>

      <div className="border-t px-4 py-2.5 flex items-center justify-between bg-surface">
        <span className="flex items-center gap-1 text-xs text-muted">
          <Clock className="h-3 w-3" />
          {indexed ? `Indexed ${formatDate(project.last_indexed)}` : "Not yet indexed"}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onReindex(project)}
            title="Re-index"
            className="flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs text-muted hover:bg-surface-2 hover:text-foreground transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            Re-index
          </button>
          {indexed && (
            <button
              onClick={() => navigate(`/docs/${project.id}`)}
              className="flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <ExternalLink className="h-3 w-3" />
              View docs
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function CodeDocPage() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reindexProject, setReindexProject] = useState<CodeProject | null>(null);
  const projectsQ = useProjects();
  const projects = projectsQ.data ?? [];
  const openReindex = (project: CodeProject) => {
    setReindexProject(project);
    setDialogOpen(true);
  };

  const handleIndexed = () => {
    setReindexProject(null);
  };

  const handleClose = () => {
    setDialogOpen(false);
    setReindexProject(null);
  };

  return (
    <div className="mx-auto max-w-4xl p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Code Documentation Agent</h1>
          <p className="mt-1 text-sm text-muted">
            Index a Java or React project to generate exhaustive documentation and chat over it.
          </p>
        </div>
        <button
          onClick={() => { setReindexProject(null); setDialogOpen(true); }}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3.5 py-2 text-sm text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Index project
        </button>
      </div>

      {projectsQ.isLoading && (
        <p className="text-sm text-muted">Loading projects…</p>
      )}

      {!projectsQ.isLoading && projects.length === 0 && (
        <div className="rounded-lg border border-dashed bg-surface p-10 text-center">
          <FileCode2 className="mx-auto mb-3 h-8 w-8 text-muted" />
          <p className="font-medium text-sm">No projects indexed yet</p>
          <p className="mt-1 text-xs text-muted">
            Click <strong>Index project</strong> to point the agent at a local Java or React
            folder and generate full documentation.
          </p>
        </div>
      )}

      {projects.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {projects.map((p) => (
            <ProjectCard key={p.id} project={p} onReindex={openReindex} />
          ))}
        </div>
      )}

      <IndexProjectDialog
        open={dialogOpen}
        onClose={handleClose}
        onIndexed={handleIndexed}
        initialPath={reindexProject?.project_path}
        initialName={reindexProject?.display_name ?? undefined}
      />
    </div>
  );
}
