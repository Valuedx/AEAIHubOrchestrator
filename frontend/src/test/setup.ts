import "@testing-library/jest-dom/vitest";

// jsdom doesn't ship ResizeObserver — React Flow uses it to track the
// viewport size. Provide a no-op polyfill so tests that mount RF can
// run without hitting a ReferenceError.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver = ResizeObserverStub;
}

// Same story for DOMMatrix (used internally by React Flow for viewport
// transforms). jsdom ships DOMMatrixReadOnly but not DOMMatrix — alias.
if (typeof globalThis.DOMMatrix === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).DOMMatrix =
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).DOMMatrixReadOnly ??
    class {
      m11 = 1; m12 = 0; m21 = 0; m22 = 1; m41 = 0; m42 = 0;
    };
}
