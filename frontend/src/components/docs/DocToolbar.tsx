import { Copy, Download, Check } from "lucide-react";
import { useState } from "react";
import type { DocContent, DocFormat } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  doc?: DocContent;
  format: DocFormat;
  onFormatChange: (f: DocFormat) => void;
}

const FORMATS: { value: DocFormat; label: string }[] = [
  { value: "markdown", label: "Markdown" },
  { value: "confluence", label: "Confluence" },
];

export function DocToolbar({ doc, format, onFormatChange }: Props) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!doc) return;
    await navigator.clipboard.writeText(doc.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const download = () => {
    if (!doc) return;
    const ext = format === "confluence" ? "html" : "md";
    const blob = new Blob([doc.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${doc.doc_id}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex items-center justify-between border-b bg-surface px-4 py-2.5">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold">{doc?.title ?? "Documentation"}</h2>
        {doc?.generated_at && (
          <p className="text-xs text-muted">
            Generated {new Date(doc.generated_at).toLocaleString()}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <div className="flex overflow-hidden rounded-md border">
          {FORMATS.map((f) => (
            <button
              key={f.value}
              onClick={() => onFormatChange(f.value)}
              className={cn(
                "px-2.5 py-1 text-xs transition-colors",
                format === f.value
                  ? "bg-primary text-primary-foreground"
                  : "bg-background text-muted hover:bg-surface-2",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <button
          onClick={copy}
          disabled={!doc}
          className="flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs text-muted hover:bg-surface-2 disabled:opacity-40"
          title="Copy"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          onClick={download}
          disabled={!doc}
          className="flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs text-muted hover:bg-surface-2 disabled:opacity-40"
          title="Download"
        >
          <Download className="h-3.5 w-3.5" /> Download
        </button>
      </div>
    </div>
  );
}
