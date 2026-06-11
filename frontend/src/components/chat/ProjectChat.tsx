import { useEffect, useRef, useState } from "react";
import { Send, RefreshCw } from "lucide-react";
import { useChatStream } from "@/hooks/useChatStream";
import { MarkdownView } from "./MarkdownView";
import { cn } from "@/lib/utils";

interface Props {
  projectId?: string;
}

/**
 * Docked chat panel for the Documentation Hub. Answers are streamed from the
 * code_doc agent, whose retrieval fans out over the generated docs + code
 * summaries for `projectId` (architecture.md §13A.5).
 */
export function ProjectChat({ projectId }: Props) {
  const { messages, inflight, loading, send, reset } = useChatStream("code_doc", projectId);
  const [text, setText] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, inflight]);

  // New project => fresh conversation.
  useEffect(() => {
    reset();
  }, [projectId, reset]);

  const submit = () => {
    if (!text.trim() || loading) return;
    send(text);
    setText("");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b bg-surface px-4 py-2.5">
        <h3 className="text-sm font-semibold">Ask about this project</h3>
        <button
          onClick={reset}
          className="flex items-center gap-1 text-xs text-muted hover:text-foreground"
          title="New conversation"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Reset
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && !inflight && (
          <p className="mt-6 text-center text-sm text-muted">
            {projectId
              ? "Ask anything about the generated documentation or the code."
              : "Select a project to start chatting."}
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={cn(
              "max-w-[90%] rounded-lg px-3 py-2 text-sm",
              m.role === "user"
                ? "ml-auto bg-primary text-primary-foreground"
                : "border bg-surface",
            )}
          >
            {m.role === "assistant" ? (
              <MarkdownView markdown={m.content} />
            ) : (
              <span className="whitespace-pre-wrap">{m.content}</span>
            )}
          </div>
        ))}
        {inflight && (
          <div className="max-w-[90%] rounded-lg border bg-surface px-3 py-2">
            <MarkdownView markdown={inflight} />
          </div>
        )}
      </div>

      <div className="flex items-end gap-2 border-t bg-surface p-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={projectId ? "Ask a question…" : "Select a project first"}
          disabled={!projectId || loading}
          rows={2}
          className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={!projectId || loading || !text.trim()}
          className="flex h-9 w-9 items-center justify-center rounded-md bg-primary text-primary-foreground disabled:opacity-40"
          title="Send"
        >
          <Send className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
