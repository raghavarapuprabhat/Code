/**
 * SSE over POST. EventSource only supports GET, so we POST a JSON body and read
 * the response as a stream, parsing `data:` lines into typed events.
 * Ported from the Lit api-client's streamSse() (architecture.md §13.4).
 */
export async function* streamSse<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function* parseBuffer(): Generator<T> {
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        yield JSON.parse(json) as T;
      } catch {
        // ignore malformed events
      }
    }
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      // Flush any final event that arrived without a trailing blank line.
      if (buffer.trim()) {
        buffer += "\n\n";
        yield* parseBuffer();
      }
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    yield* parseBuffer();
  }
}
