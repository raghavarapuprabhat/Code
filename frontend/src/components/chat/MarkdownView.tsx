import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import mermaid from "mermaid";
import { cn } from "@/lib/utils";

// White-canvas theme to match the app (architecture.md §13.2).
mermaid.initialize({ startOnLoad: false, theme: "neutral" });

interface Props {
  markdown: string;
  className?: string;
}

/**
 * Shared markdown renderer for both chat and the Documentation Hub. Renders
 * GFM markdown safely (rehype-sanitize) and post-processes ```mermaid fences
 * into rendered SVG. One renderer = consistent code/diagram styling everywhere.
 */
export function MarkdownView({ markdown, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const blocks = root.querySelectorAll<HTMLElement>("code.language-mermaid");
    blocks.forEach(async (code, i) => {
      const pre = code.closest("pre");
      if (!pre) return;
      const id = `mmd-${Date.now()}-${i}`;
      try {
        const { svg } = await mermaid.render(id, code.textContent ?? "");
        const wrap = document.createElement("div");
        wrap.className = "mermaid my-3 overflow-auto rounded-md border bg-surface p-3";
        wrap.innerHTML = svg;
        pre.replaceWith(wrap);
      } catch (err) {
        pre.innerHTML = `<span class="text-danger text-sm">Mermaid render error: ${
          (err as Error).message
        }</span>`;
      }
    });
  }, [markdown]);

  return (
    <div
      ref={ref}
      className={cn(
        "prose-doc max-w-none text-sm leading-relaxed text-foreground",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
