import { html } from './html.js';
export const defaults = {
  theme: 'clear',
  mode: 'auto',
  reading: 'sans',
  size: 'm',
  motion: 'system',
  sidebar_collapsed: false,
  card_tips_seen: false,
  require_summary: false,
};
// A small preview of each theme, read from tokens.css so it never drifts from the real palette.
function themeSample(theme) {
  const value = {};
  try {
    for (const sheet of document.styleSheets)
      for (const rule of sheet.cssRules || [])
        if (rule.selectorText?.split(',').some((x) => x.trim() === `:root[data-theme="${theme}"]`))
          for (const key of ['bg', 'text', 'accent', 'line-strong'])
            value[key] ||= rule.style.getPropertyValue('--' + key).trim();
  } catch {}
  return html`
    <span class="theme-sample theme-sample-${theme}" aria-hidden="true" style="--sample-bg:${value.bg || 'var(--bg)'};--sample-text:${value.text || 'var(--text)'};--sample-accent:${value.accent || 'var(--accent)'};--sample-line:${value['line-strong'] || 'var(--line-strong)'}">
      <span class="theme-sample-type">文 Aa</span>
      <span class="theme-sample-dot"></span>
    </span>
  `;
}
export function applySettings(prefs) {
  const root = document.documentElement;
  root.dataset.theme = prefs.theme || 'clear';
  root.dataset.reading = prefs.reading || 'sans';
  root.dataset.size = prefs.size || 'm';
  if (prefs.mode === 'auto') {
    if (typeof window.__LEARNING_SYSTEM_DARK__ === 'boolean')
      root.dataset.mode = window.__LEARNING_SYSTEM_DARK__ ? 'dark' : 'light';
    else delete root.dataset.mode;
  } else root.dataset.mode = prefs.mode;
  root.dataset.motion =
    prefs.motion === 'reduce' || matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'reduce'
      : 'system';
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = getComputedStyle(root).getPropertyValue('--bg').trim();
}
export function settingsHTML(prefs, version, { esc, btn }) {
  const row = (label, key, options) =>
    html`
      <fieldset class="settings-row">
        <legend>${label}</legend>
        <div class="segmented${key === 'theme' ? ' theme-choices' : ''}">${options.map(([v, t]) => `<button data-action="setting" data-key="${key}" data-value="${v}" aria-pressed="${prefs[key] === v}">${key === 'theme' ? themeSample(v) : ''}${t}</button>`).join('')}</div>
      </fieldset>
    `;
  return (
    row('主题', 'theme', [
      ['paper', '纸页'],
      ['clear', '清透'],
    ]) +
    row('外观', 'mode', [
      ['auto', '跟随系统'],
      ['light', '浅色'],
      ['dark', '深色'],
    ]) +
    row('正文字体', 'reading', [
      ['sans', '黑体'],
      ['serif', '宋体'],
    ]) +
    row('字号', 'size', [
      ['m', '标准'],
      ['l', '大'],
    ]) +
    row('减弱动态效果', 'motion', [
      ['system', '跟随系统'],
      ['reduce', '开启'],
    ]) +
    html`
      <fieldset class="settings-row">
        <legend>完成卡片时要求写总结</legend>
        <div class="segmented">${[
          ['false', '不要求'],
          ['true', '要求'],
        ]
          .map(
            ([v, t]) =>
              `<button data-action="setting" data-key="require_summary" data-bool data-value="${v}" aria-pressed="${String(!!prefs.require_summary) === v}">${t}</button>`,
          )
          .join('')}</div>
      </fieldset>
    ` +
    `${btn('重看欢迎页', 'nav', 'data-view="welcome"', 'quiet')}`
  );
}
