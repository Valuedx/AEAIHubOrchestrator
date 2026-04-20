/**
 * DV-06 — shared keyboard-shortcut helpers.
 *
 * Single-key global shortcuts (?, S, 1, Tab, ...) must NOT fire while the
 * user is typing in an input, textarea, select, or a contenteditable
 * region — otherwise the Node Palette filter / Property Inspector fields
 * break. Centralise the check so every handler uses the same rule.
 */

export function isTextEditingTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}
