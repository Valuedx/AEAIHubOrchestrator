/**
 * Minimal fetch-based SSE reader.
 *
 * Replaces `EventSource` so we can:
 *   1. Send proper Authorization / X-Tenant-Id headers (EventSource can't).
 *   2. Keep the tenant identifier out of URL query strings — it would
 *      otherwise leak into proxy and CDN access logs.
 *
 * Parses the subset of the SSE wire format actually emitted by the backend:
 * blank-line-delimited blocks with ``event:`` and ``data:`` lines.
 * Handles multi-byte UTF-8 safely via TextDecoder streaming mode.
 */

export type SSEHandlers = {
  onEvent: (event: string, data: string) => void;
  onError: (err: { kind: "network" | "parse" | "http"; message: string }) => void;
  onDone: () => void;
};

export function openSSE(
  url: string,
  headers: Record<string, string>,
  handlers: SSEHandlers,
): () => void {
  const controller = new AbortController();

  (async () => {
    let httpErrored = false;
    try {
      const res = await fetch(url, {
        method: "GET",
        headers: { ...headers, Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        httpErrored = true;
        handlers.onError({
          kind: "http",
          message: `Stream failed: HTTP ${res.status}`,
        });
        handlers.onDone();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      // SSE blocks are separated by a blank line ("\n\n" or "\r\n\r\n").
      const BLOCK_RE = /\r?\n\r?\n/;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let match: RegExpExecArray | null;
        while ((match = BLOCK_RE.exec(buffer)) !== null) {
          const block = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          parseBlock(block, handlers);
        }
      }
      // Final flush for any trailing byte sequence.
      buffer += decoder.decode();
      if (buffer.trim()) parseBlock(buffer, handlers);
      handlers.onDone();
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      if (httpErrored) return; // already reported
      handlers.onError({
        kind: "network",
        message: String((e as Error)?.message ?? e),
      });
      handlers.onDone();
    }
  })();

  return () => controller.abort();
}

function parseBlock(block: string, handlers: SSEHandlers): void {
  if (!block.trim()) return;
  let eventName = "message";
  const dataLines: string[] = [];
  for (const raw of block.split(/\r?\n/)) {
    if (!raw || raw.startsWith(":")) continue; // comment or blank
    const colon = raw.indexOf(":");
    if (colon < 0) continue;
    const field = raw.slice(0, colon);
    // Per spec, a single leading space after the colon is ignored.
    const rest = raw.slice(colon + 1).replace(/^ /, "");
    if (field === "event") eventName = rest;
    else if (field === "data") dataLines.push(rest);
  }
  if (dataLines.length > 0) {
    handlers.onEvent(eventName, dataLines.join("\n"));
  }
}
