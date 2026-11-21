export function focusPublicSection(id) {
  const target = document.getElementById(id);
  if (!target) return;
  if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
  target.focus({ preventScroll: true });
  target.scrollIntoView?.({ block: "start" });
}
