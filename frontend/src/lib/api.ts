/** Typed REST + SSE client for the FastAPI backend (subset needed for F5). */
import { streamSse } from "./sse";

export interface CodeProject {
  id: string;
  project_path: string;
  display_name: string | null;
  last_indexed: string | null;
}

export type DocAudience = "management" | "architecture" | "developer" | string;

export interface DocSummary {
  doc_id: string;
  title: string;
  audience: DocAudience | null;
  sort_order: number;
  generated_at: string | null;
}

export type DocFormat = "markdown" | "html" | "confluence";

export interface DocContent {
  doc_id: string;
  title: string;
  audience: DocAudience | null;
  format: DocFormat;
  content: string;
  generated_at: string | null;
}

export type ChatEvent =
  | { type: "start"; conversation_id: string }
  | { type: "token"; delta: string }
  | { type: "tool"; name: string; args: Record<string, unknown> }
  | { type: "final"; content: string; conversation_id: string }
  | { type: "error"; message: string };

export interface ChatRequest {
  message: string;
  conversation_id?: string;
  scope_key?: string;
  user_id?: string;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  async listProjects(): Promise<CodeProject[]> {
    const data = await getJson<{ projects: CodeProject[] }>("/agents/code_doc/projects");
    return data.projects;
  },

  async listDocs(projectId: string): Promise<DocSummary[]> {
    const data = await getJson<{ docs: DocSummary[] }>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/docs`,
    );
    return data.docs;
  },

  async getDoc(projectId: string, docId: string, format: DocFormat = "markdown"): Promise<DocContent> {
    return getJson<DocContent>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(
        docId,
      )}?format=${format}`,
    );
  },

  async indexProject(projectPath: string, mode: "full" | "incremental", displayName?: string) {
    const res = await fetch("/agents/code_doc/index", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ project_path: projectPath, mode, display_name: displayName }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
  },

  chat(agentId: string, body: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    return streamSse<ChatEvent>(`/agents/${agentId}/chat`, body, signal);
  },
};
