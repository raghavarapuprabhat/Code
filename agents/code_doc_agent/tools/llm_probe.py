"""Diagnostic probe for the configured LLM endpoint.

Useful when summaries/flows fail to parse (`summary_parse_failed` /
`cross_file_json_parse_failed`) and you need to see what the model/gateway actually
returns — especially for a `provider=custom` enterprise gateway fronting Claude/Sonnet,
where `response_format` may be honored, silently ignored (model free-forms prose), or
rejected outright.

It prints how the LLM config resolves, then fires a small "return JSON" prompt both
WITHOUT and WITH json_mode, reporting finish_reason, token count, whether the robust
extractor (`json_tools.extract_json`) parses it, and the raw content. No source files
are sent — this is a tiny, cheap request.

Run (load your endpoint's env first):
    cd <repo>/Code
    set -a; . ./.env; set +a
    python -m agents.code_doc_agent.tools.llm_probe
    # optional: --max-tokens 4000  to reproduce truncation at a given cap
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Make the repo root importable when run as a script as well as a module.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.code_doc_agent.graph import load_config  # noqa: E402
from agents.code_doc_agent.tools.json_tools import extract_json  # noqa: E402
from shared.llm_adapter import build_adapter_from_config  # noqa: E402

PROMPT = (
    "Summarize this file as JSON with keys: purpose (string), "
    "business_rules (array). Source:\n\npublic int add(int a,int b){return a+b;}"
)


async def run(max_tokens: int) -> None:
    llm = build_adapter_from_config(load_config())
    c = llm.cfg
    print("provider     :", c.provider)
    print("litellm_model:", c.litellm_model)
    print("base_url     :", c.base_url or "<none>")
    print("max_tokens   :", max_tokens, "(probe override)")
    print("json_mode?   :", llm.supports_json_mode())
    print("auth_token   :", "set" if c.auth_token_env else "(uses api_key_env)")
    print("=" * 60)

    for label, jm in (("WITHOUT json_mode", False), ("WITH json_mode", True)):
        print(f"\n--- {label} ---")
        try:
            r = await llm.chat([{"role": "user", "content": PROMPT}], json_mode=jm, max_tokens=max_tokens)
            finish = None
            try:
                finish = r.raw.choices[0].finish_reason
            except Exception:  # noqa: BLE001
                pass
            print("finish_reason:", finish, "| tokens_out:", r.tokens_out)
            print("parses?      :", extract_json(r.content) is not None)
            print("raw content  :")
            print(repr(r.content[:600]))
        except Exception as exc:  # noqa: BLE001 — surface gateway errors verbatim
            print("CALL RAISED:", type(exc).__name__, str(exc)[:400])


def main() -> None:
    ap = argparse.ArgumentParser(prog="llm_probe", description=__doc__)
    ap.add_argument("--max-tokens", type=int, default=2000,
                    help="max_tokens for the probe call (use 4096 to reproduce a truncation cap)")
    args = ap.parse_args()
    asyncio.run(run(args.max_tokens))


if __name__ == "__main__":
    main()
