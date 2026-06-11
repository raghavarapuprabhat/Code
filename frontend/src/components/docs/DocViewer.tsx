import type { DocContent } from "@/lib/api";
import { MarkdownView } from "@/components/chat/MarkdownView";
import { DocFeedback } from "@/components/docs/DocFeedback";

interface Props {
  doc?: DocContent;
  isLoading?: boolean;
  isError?: boolean;
  emptyHint?: string;
  projectId?: string;
}

export function DocViewer({ doc, isLoading, isError, emptyHint, projectId }: Props) {
  if (isLoading) {
    return <div className="p-8 text-sm text-muted">Loading document…</div>;
  }
  if (isError) {
    return <div className="p-8 text-sm text-danger">Failed to load this document.</div>;
  }
  if (!doc) {
    return (
      <div className="p-8 text-sm text-muted">
        {emptyHint ?? "Select a document from the left to view it."}
      </div>
    );
  }

  // Confluence storage-format is HTML; render it directly. Markdown goes through
  // the shared MarkdownView (which also renders mermaid diagrams).
  if (doc.format === "confluence" || doc.format === "html") {
    return (
      <div className="p-6">
        <div
          className="prose-doc max-w-none text-sm"
          dangerouslySetInnerHTML={{ __html: doc.content }}
        />
        <DocFeedback projectId={projectId} docId={doc.doc_id} />
      </div>
    );
  }

  return (
    <div className="p-6">
      <MarkdownView markdown={doc.content} />
      <DocFeedback projectId={projectId} docId={doc.doc_id} />
    </div>
  );
}
