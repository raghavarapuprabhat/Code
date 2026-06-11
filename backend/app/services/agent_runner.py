"""Dispatches chat requests to the appropriate agent and yields SSE events.

Events emitted:
  - {"type": "start",  "conversation_id": "..."}
  - {"type": "token",  "delta": "..."}
  - {"type": "tool",   "name": "...", "args": {...}}
  - {"type": "final",  "content": "...", "conversation_id": "..."}
  - {"type": "error",  "message": "..."}
"""
from __future__ import annotations

from typing import AsyncIterator

import structlog
import yaml
import os

from shared.llm_adapter import build_adapter_from_config
from shared.memory import MemoryConfig, MemoryManager
from shared.storage import ChromaStore, get_session

logger = structlog.get_logger()


def _load_agent_config(agent_id: str) -> dict:
    """Load <repo>/agents/<agent_id>_agent/config.yaml.

    Falls back to a sane default if the file is missing — handy during early
    scaffolding before all agents exist.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, f"../../../agents/{agent_id}_agent/config.yaml")),
        os.path.normpath(os.path.join(here, f"../../../agents/{agent_id}/config.yaml")),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r") as fh:
                return yaml.safe_load(fh) or {}
    return {
        "agent": {"name": agent_id},
        "llm": {
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "memory": {"recent_window": 3, "summarize_every": 5},
    }


async def run_agent_chat(
    *,
    agent_id: str,
    message: str,
    conversation_id: str | None,
    scope_key: str | None,
    user_id: str | None,
) -> AsyncIterator[dict]:
    cfg = _load_agent_config(agent_id)
    llm = build_adapter_from_config(cfg)
    mem_cfg = MemoryConfig.from_dict(cfg.get("memory", {}))

    async with get_session() as session:
        memory = MemoryManager(session, llm, mem_cfg)
        conv_id = await memory.get_or_create_conversation(
            agent_name=agent_id,
            scope_key=scope_key,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        yield {"type": "start", "conversation_id": conv_id}

        context = await memory.get_context(conv_id)
        system_prompt = _system_prompt_for(agent_id)
        messages = memory.build_prompt_messages(
            system_prompt=system_prompt,
            context=context,
            new_user_message=message,
        )

        # RAG: inject relevant indexed code summaries for code_doc agent.
        if agent_id == "code_doc" and scope_key:
            rag_ctx = await _fetch_rag_context(scope_key, message)
            if rag_ctx:
                # Insert just before the final user message so it reads naturally.
                messages.insert(-1, {"role": "system", "content": rag_ctx})

        # Persist user turn before calling the model.
        await memory.append_message(conv_id, "user", message)

        full = []
        try:
            async for delta in llm.stream(messages):
                full.append(delta)
                yield {"type": "token", "delta": delta}
        except Exception as e:  # noqa: BLE001
            logger.exception("llm_stream_failed", agent=agent_id, conv=conv_id)
            yield {"type": "error", "message": str(e)}
            return

        final_text = "".join(full).strip()
        await memory.append_message(conv_id, "assistant", final_text)
        yield {"type": "final", "content": final_text, "conversation_id": conv_id}


def _query_collection(store: "ChromaStore", collection: str, question: str, n: int) -> list[dict]:
    """Best-effort similarity query; returns [] if the collection is missing."""
    try:
        res = store.query(collection, query_texts=[question], n_results=n)
    except Exception:  # noqa: BLE001
        return []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out: list[dict] = []
    for doc, meta in zip(docs, metas):
        out.append({"text": doc, "meta": meta or {}})
    return out


async def _fetch_rag_context(project_id: str, question: str, n: int = 5) -> str | None:
    """Build chat context from the generated docs AND per-file code summaries.

    Fans out to two Chroma collections (v0.2):
      * ``docs_<pid>`` — chunks of the generated documentation (cite by doc_id)
      * ``code_<pid>`` — per-file summaries (cite by file path)
    """
    try:
        store = ChromaStore()
    except Exception:  # noqa: BLE001
        logger.warning("rag_store_init_failed", project_id=project_id)
        return None

    doc_hits = _query_collection(store, f"docs_{project_id}", question, n)
    code_hits = _query_collection(store, f"code_{project_id}", question, n)
    if not doc_hits and not code_hits:
        return None

    blocks: list[str] = [
        "Use the project context below to answer. Cite the document title or "
        "file:line when you rely on a passage. If the answer isn't here, say so.",
    ]

    if doc_hits:
        blocks.append("\n## From the generated documentation")
        for h in doc_hits:
            meta = h["meta"]
            title = meta.get("title") or meta.get("doc_id") or "doc"
            heading = meta.get("heading_path")
            label = f"{title} — {heading}" if heading else title
            blocks.append(f"--- [{label}]")
            blocks.append(h["text"])

    if code_hits:
        blocks.append("\n## From per-file code summaries")
        for h in code_hits:
            path = h["meta"].get("relative_path", "")
            blocks.append(f"--- [{path}]" if path else "---")
            blocks.append(h["text"])

    return "\n".join(blocks)


def _system_prompt_for(agent_id: str) -> str:
    if agent_id == "code_doc":
        return (
            "You are the Code Documentation Agent. Answer questions about an indexed "
            "codebase using the project documentation generated previously. Cite "
            "file:line whenever possible. If the answer isn't in the indexed material, "
            "say so explicitly."
        )
    if agent_id == "sre":
        return (
            "You are the SRE Triage Agent. Help the user determine whether a reported "
            "issue is a bug or expected behavior. Ask focused follow-up questions, "
            "consult the indexed documentation, and produce a clear verdict with next steps."
        )
    if agent_id == "ado_dev":
        return (
            "You are the Developer's ADO assistant. You help with workitem status "
            "reports and updates. Always confirm areapath at the start, and never "
            "modify a workitem without explicit user consent."
        )
    return "You are a helpful assistant."
