// The shell owns its process; the page only reports protected input and appearance.
const native = !!window.__LEARNING_DESKTOP__ || !!window.webkit?.messageHandlers?.learningHub;
if (native) document.documentElement.dataset.desktop = 'macos';
export function syncAppearance(prefs) {
  if (!native) return;
  const mode =
    prefs.mode === 'auto'
      ? (
          typeof window.__LEARNING_SYSTEM_DARK__ === 'boolean'
            ? window.__LEARNING_SYSTEM_DARK__
            : matchMedia('(prefers-color-scheme: dark)').matches
        )
        ? 'dark'
        : 'light'
      : prefs.mode;
  const background = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  window.webkit?.messageHandlers?.learningHub?.postMessage({ type: 'theme', mode, background });
}
export function installDesktop({
  flushPending,
  protectionState,
  reconnect,
  openSettings,
  setReconnecting,
  api,
}) {
  window.learningHub = { flushPending, protectionState, reconnect, openSettings, setReconnecting };
  const heartbeat = () => api.post('heartbeat', {}).catch(() => {});
  heartbeat();
  setInterval(heartbeat, 15000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') heartbeat();
  });
  window.addEventListener('pageshow', heartbeat);
  window.addEventListener('focus', heartbeat);
}
