import { useCallback, useRef, useState } from "react";
import { api, type ChatEvent } from "@/lib/api";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/**
 * Streaming chat against POST /agents/{agentId}/chat. `scopeKey` carries the
 * project_id so the code_doc retrieval fans out over docs_<pid> + code_<pid>.
 * Ported from the Lit chat-widget (architecture.md §13.4).
 */
export function useChatStream(agentId: string, scopeKey?: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inflight, setInflight] = useState("");
  const [loading, setLoading] = useState(false);
  const conversationId = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;
      setMessages((m) => [...m, { role: "user", content: trimmed }]);
      setInflight("");
      setLoading(true);
      const ac = new AbortController();
      abortRef.current = ac;
      // Track accumulated streaming text locally so we can flush it if the
      // stream closes before a "final" event (e.g. backend exception mid-stream).
      let accumulated = "";
      let receivedFinal = false;
      try {
        for await (const ev of api.chat(
          agentId,
          {
            message: trimmed,
            conversation_id: conversationId.current,
            scope_key: scopeKey,
          },
          ac.signal,
        ) as AsyncGenerator<ChatEvent>) {
          if (ev.type === "start") {
            conversationId.current = ev.conversation_id;
          } else if (ev.type === "token") {
            accumulated += ev.delta;
            setInflight(accumulated);
          } else if (ev.type === "final") {
            receivedFinal = true;
            setMessages((m) => [...m, { role: "assistant", content: ev.content }]);
            setInflight("");
          } else if (ev.type === "error") {
            receivedFinal = true; // treat as terminal
            setMessages((m) => [...m, { role: "assistant", content: `Error: ${ev.message}` }]);
            setInflight("");
          }
        }
        // If the stream closed without a final event but we accumulated tokens,
        // commit them as a message so they don't vanish on the next send().
        if (!receivedFinal && accumulated) {
          setMessages((m) => [...m, { role: "assistant", content: accumulated }]);
          setInflight("");
        }
      } catch (err) {
        if (!ac.signal.aborted) {
          const errText = accumulated
            ? accumulated  // prefer partial response over generic error
            : `Error: ${(err as Error).message}`;
          setMessages((m) => [...m, { role: "assistant", content: errText }]);
          setInflight("");
        }
      } finally {
        setLoading(false);
        abortRef.current = null;
      }
    },
    [agentId, scopeKey, loading],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    conversationId.current = undefined;
    setMessages([]);
    setInflight("");
    setLoading(false);
  }, []);

  return { messages, inflight, loading, send, reset };
}
