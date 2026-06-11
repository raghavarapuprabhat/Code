import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MessageSquare, X, AlertTriangle } from "lucide-react";
import { useProjects, useDocs, useDoc } from "@/hooks/useDocs";
import type { DocFormat } from "@/lib/api";
import { DocTree } from "@/components/docs/DocTree";
import { DocToolbar } from "@/components/docs/DocToolbar";
import { DocViewer } from "@/components/docs/DocViewer";
import { HubInsights } from "@/components/docs/HubInsights";
import { ProjectChat } from "@/components/chat/ProjectChat";
import { cn } from "@/lib/utils";

export function DocsPage() {
  const params = useParams<{ projectId?: string; docId?: string }>();
  const navigate = useNavigate();
  const [format, setFormat] = useState<DocFormat>("markdown");
  const [chatOpen, setChatOpen] = useState(true);

  const projectsQ = useProjects();
  const projects = projectsQ.data ?? [];

  // Default the selected project to the URL param or the first project.
  const projectId = params.projectId ?? projects[0]?.id;
  const docsQ = useDocs(projectId);
  const docs = useMemo(() => docsQ.data ?? [], [docsQ.data]);

  // Default the selected doc to the URL param or the first doc.
  const docId = params.docId ?? docs[0]?.doc_id;
  const docQ = useDoc(projectId, docId, format);

  // Keep the URL in sync once we have resolved defaults.
  useEffect(() => {
    if (projectId && docId && (!params.projectId || !params.docId)) {
      navigate(`/docs/${projectId}/${docId}`, { replace: true });
    }
  }, [projectId, docId, params.projectId, params.docId, navigate]);

  const selectProject = (id: string) => navigate(`/docs/${id}`);
  const selectDoc = (d: string) => projectId && navigate(`/docs/${projectId}/${d}`);

  const currentProject = projects.find((p) => p.id === projectId);
  const notIndexed = currentProject && !currentProject.last_indexed;

  return (
    <div className="relative flex h-full">
      <DocTree
        projects={projects}
        selectedProjectId={projectId}
        onSelectProject={selectProject}
        docs={docs}
        selectedDocId={docId}
        onSelectDoc={selectDoc}
        loadingDocs={docsQ.isLoading}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        <DocToolbar doc={docQ.data} format={format} onFormatChange={setFormat} />
        <HubInsights projectId={projectId} />

        {notIndexed ? (
          <div className="p-8 text-sm text-muted">
            No documentation generated yet for this project. Index it from the Code Doc
            page to populate the hub.
          </div>
        ) : projects.length === 0 && !projectsQ.isLoading ? (
          <div className="p-8 text-sm text-muted">
            No indexed projects yet. Index a project to generate documentation.
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <DocViewer
              doc={docQ.data}
              isLoading={docQ.isLoading}
              isError={docQ.isError}
              emptyHint="Select a document from the left to view it."
              projectId={projectId}
            />
          </div>
        )}
      </section>

      {/* Project chatbot — collapsible right rail */}
      <div
        className={cn(
          "flex flex-col border-l bg-background transition-all",
          chatOpen ? "w-96" : "w-0 overflow-hidden",
        )}
      >
        {chatOpen && <ProjectChat projectId={projectId} />}
      </div>

      <button
        onClick={() => setChatOpen((o) => !o)}
        className="absolute bottom-6 right-6 flex h-11 w-11 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-card"
        title={chatOpen ? "Hide chat" : "Ask about this project"}
      >
        {chatOpen ? <X className="h-5 w-5" /> : <MessageSquare className="h-5 w-5" />}
      </button>

      {notIndexed && (
        <div className="pointer-events-none absolute left-1/2 top-16 -translate-x-1/2">
          <span className="flex items-center gap-1.5 rounded-full border bg-warning/10 px-3 py-1 text-xs text-warning">
            <AlertTriangle className="h-3.5 w-3.5" /> Source not indexed
          </span>
        </div>
      )}
    </div>
  );
}
