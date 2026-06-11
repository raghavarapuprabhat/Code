import { useCallback, useRef, useState } from "react";
import {
  api,
  type SreEvent,
  type SreEvidence,
  type SreHypothesis,
  type SreProbe,
  type SreQuestion,
  type SreSeverity,
  type SreStep,
  type SreVerdict,
} from "@/lib/api";

export interface TriageState {
  conversationId?: string;
  node?: string;
  rag: Array<{ path?: string; score?: number; collection?: string }>;
  hypotheses: SreHypothesis[];
  steps: SreStep[];
  evidence: SreEvidence[];
  probes: SreProbe[];
  severity?: SreSeverity;
  verdict?: SreVerdict;
  handoff?: Record<string, unknown>;
  question?: SreQuestion; // set when paused mid-loop awaiting an answer
  loading: boolean;
  error?: string;
}

const EMPTY: TriageState = {
  rag: [],
  hypotheses: [],
  steps: [],
  evidence: [],
  probes: [],
  loading: false,
};

/**
 * Drives one SRE triage investigation over SSE (architecture §9.15). Accumulates the
 * live hypothesis board, evidence ledger, probe log, severity and verdict. When the
 * investigator pauses to ask a question (§9.7B) the stream ends with `question` set;
 * calling `answer()` resumes the frozen investigation. `steer()` pins/injects/kills a
 * hypothesis on the live board (§9.17.8).
 */
export function useSreTriage() {
  const [state, setState] = useState<TriageState>(EMPTY);
  const abortRef = useRef<AbortController | null>(null);
  const convRef = useRef<string | undefined>(undefined);

  const consume = useCallback(async (gen: AsyncGenerator<SreEvent>, ac: AbortController) => {
    try {
      for await (const ev of gen) {
        switch (ev.type) {
          case "start":
            convRef.current = ev.conversation_id;
            setState((s) => ({ ...s, conversationId: ev.conversation_id, question: undefined }));
            break;
          case "node":
            setState((s) => ({ ...s, node: ev.name }));
            break;
          case "rag":
            setState((s) => ({ ...s, rag: ev.hits }));
            break;
          case "hypothesis":
            setState((s) => ({ ...s, hypotheses: upsertHypothesis(s.hypotheses, ev.hypothesis) }));
            break;
          case "step":
            setState((s) => ({ ...s, steps: [...s.steps, ev.step] }));
            break;
          case "evidence":
            setState((s) => ({ ...s, evidence: [...s.evidence, ev.evidence] }));
            break;
          case "probe":
            setState((s) => ({ ...s, probes: [...s.probes, ev.probe] }));
            break;
          case "severity":
            setState((s) => ({ ...s, severity: ev.severity }));
            break;
          case "verdict":
            setState((s) => ({ ...s, verdict: ev.verdict }));
            break;
          case "handoff":
            setState((s) => ({ ...s, handoff: ev.payload }));
            break;
          case "question":
            convRef.current = ev.conversation_id;
            setState((s) => ({
              ...s,
              conversationId: ev.conversation_id,
              question: ev.question,
            }));
            break;
          case "error":
            setState((s) => ({ ...s, error: ev.message }));
            break;
          case "final":
            if (ev.verdict) setState((s) => ({ ...s, verdict: ev.verdict }));
            break;
        }
      }
    } catch (err) {
      if (!ac.signal.aborted) {
        setState((s) => ({ ...s, error: (err as Error).message }));
      }
    } finally {
      setState((s) => ({ ...s, loading: false }));
      abortRef.current = null;
    }
  }, []);

  const start = useCallback(
    async (projectId: string, message: string) => {
      abortRef.current?.abort();
      convRef.current = undefined;
      const ac = new AbortController();
      abortRef.current = ac;
      setState({ ...EMPTY, loading: true });
      await consume(api.triage({ project_id: projectId, message }, ac.signal), ac);
    },
    [consume],
  );

  const answer = useCallback(
    async (projectId: string, text: string) => {
      const conv = convRef.current;
      if (!conv) return;
      const ac = new AbortController();
      abortRef.current = ac;
      setState((s) => ({ ...s, loading: true, question: undefined }));
      await consume(
        api.answerTriage(conv, { answer: text, project_id: projectId }, ac.signal),
        ac,
      );
    },
    [consume],
  );

  const steer = useCallback(
    async (action: "pin" | "inject" | "kill", hypothesisId?: string, statement?: string) => {
      const conv = convRef.current;
      if (!conv) return;
      // optimistic local update
      setState((s) => ({
        ...s,
        hypotheses: applySteerLocally(s.hypotheses, action, hypothesisId, statement),
      }));
      try {
        await api.steerTriage(conv, { action, hypothesis_id: hypothesisId, statement });
      } catch {
        /* best-effort; backend applies at next plan step */
      }
    },
    [],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    convRef.current = undefined;
    setState(EMPTY);
  }, []);

  return { state, start, answer, steer, reset };
}

function upsertHypothesis(list: SreHypothesis[], h: SreHypothesis): SreHypothesis[] {
  const idx = list.findIndex((x) => x.id === h.id);
  if (idx === -1) return [...list, h];
  const copy = [...list];
  copy[idx] = { ...copy[idx], ...h };
  return copy;
}

function applySteerLocally(
  list: SreHypothesis[],
  action: "pin" | "inject" | "kill",
  id?: string,
  statement?: string,
): SreHypothesis[] {
  if (action === "inject") {
    return [
      ...list,
      {
        id: id || `Hu${list.length + 1}`,
        statement: statement || "(user hypothesis)",
        posterior: 0.5,
        status: "open",
        source: "user",
      },
    ];
  }
  return list.map((h) =>
    h.id === id
      ? action === "kill"
        ? { ...h, status: "refuted", posterior: 0.05 }
        : { ...h, pinned: true }
      : h,
  );
}
