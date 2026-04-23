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

// base-ui's ScrollArea (used inside Dialog surfaces — PromoteDialog,
// HITLResumeDialog, etc.) calls ``viewport.getAnimations()`` during
// cleanup to cancel in-flight scroll animations. jsdom doesn't
// implement the Web Animations API, so that call throws. The throw
// happens AFTER the test body completes (during unmount), so tests
// pass but vitest logs the exception as an unhandled error and the
// summary says "9 errors" on a green run. Stubbing the method as a
// no-op returning an empty array matches the Web Animations spec
// ("Element has no active animations") and silences the noise.
if (typeof Element !== "undefined"
    && typeof (Element.prototype as unknown as { getAnimations?: () => unknown[] }).getAnimations
        !== "function") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (Element.prototype as any).getAnimations = function (): unknown[] {
    return [];
  };
}
