import { describe, it, expect, vi } from "vitest";
import { openSSE } from "./sse";

function makeStreamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
        // Yield to the event loop between chunks so the consumer actually sees them separately.
        await Promise.resolve();
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("openSSE", () => {
  it("parses well-formed single-block SSE events", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        makeStreamResponse([
          "event: log\ndata: {\"id\":1}\n\n",
          "event: status\ndata: {\"instance_status\":\"running\"}\n\n",
          "event: done\ndata: bye\n\n",
        ]),
      );

    const events: Array<[string, string]> = [];
    const done = new Promise<void>((resolve) => {
      openSSE("/stream", { Authorization: "Bearer t" }, {
        onEvent: (e, d) => events.push([e, d]),
        onError: () => {},
        onDone: () => resolve(),
      });
    });
    await done;

    expect(events).toEqual([
      ["log", '{"id":1}'],
      ["status", '{"instance_status":"running"}'],
      ["done", "bye"],
    ]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0];
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer t",
      Accept: "text/event-stream",
    });
  });

  it("handles events split across chunk boundaries", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeStreamResponse([
        "event: log\nda",
        'ta: {"id":42}',
        "\n\nevent: done\ndata: ok\n\n",
      ]),
    );

    const events: Array<[string, string]> = [];
    await new Promise<void>((resolve) => {
      openSSE("/stream", {}, {
        onEvent: (e, d) => events.push([e, d]),
        onError: () => {},
        onDone: () => resolve(),
      });
    });

    expect(events).toEqual([
      ["log", '{"id":42}'],
      ["done", "ok"],
    ]);
  });

  it("reports http kind when the response is not ok", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 401 }),
    );
    const errors: Array<{ kind: string; message: string }> = [];
    await new Promise<void>((resolve) => {
      openSSE("/stream", {}, {
        onEvent: () => {},
        onError: (e) => errors.push(e),
        onDone: () => resolve(),
      });
    });
    expect(errors).toEqual([{ kind: "http", message: "Stream failed: HTTP 401" }]);
  });

  it("does not call onError when the caller aborts the stream", async () => {
    // Infinite stream
    const stream = new ReadableStream({
      start() {/* never close */},
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(stream, { status: 200 }));

    const errors: unknown[] = [];
    const dones: unknown[] = [];
    const cancel = openSSE("/stream", {}, {
      onEvent: () => {},
      onError: (e) => errors.push(e),
      onDone: () => dones.push(1),
    });
    // Let fetch resolve and the reader start
    await new Promise((r) => setTimeout(r, 20));
    cancel();
    await new Promise((r) => setTimeout(r, 20));
    expect(errors).toEqual([]);
    // onDone is also not called on explicit abort (no completion signal).
    expect(dones).toEqual([]);
  });
});
