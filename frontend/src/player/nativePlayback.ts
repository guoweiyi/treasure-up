/** UI compatibility only; this marker never grants native permissions. */
export function canUseElementFullscreen(
  element: { requestFullscreen?: unknown },
  environment: { isTauri?: unknown; navigator?: { userAgent?: string } },
): boolean {
  // Tauri 2 injects the immutable isTauri=true property on every main-frame document.
  // Wry 0.57's Android onShowCustomView immediately closes system fullscreen.
  const androidShell =
    environment.isTauri === true && /\bAndroid\b/i.test(environment.navigator?.userAgent || '');
  return typeof element.requestFullscreen === 'function' && !androidShell;
}
