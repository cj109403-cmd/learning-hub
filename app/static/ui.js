import { html } from './html.js';
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{ today: '<path d="M5 4h14v16H5zM8 2v4m8-4v4M5 9h14m-10 4h6m-6 4h3"/>', route: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="M6 7v6a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V5M15 8l3-3 3 3"/>', lab: '<path d="M4 4h16v12H9l-5 4zM8 8h8m-8 4h5"/>', journal: '<path d="M4 3h7a3 3 0 0 1 3 3v15a4 4 0 0 0-4-2H4zM14 6a3 3 0 0 1 3-3h3v16h-3a3 3 0 0 0-3 2"/>', settings: '<circle cx="12" cy="12" r="3"/><path d="m10 3 4 0 1 3 3 1 3 3v4l-3 1-1 3-3 3h-4l-1-3-3-1-3-3v-4l3-1 1-3z"/>', search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>', close: '<path d="m6 6 12 12M6 18 18 6"/>', check: '<path d="m5 12 4 4L19 6"/>', arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>', back: '<path d="M20 12H4m6-6-6 6 6 6"/>', lock: '<rect x="6" y="10" width="12" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 4v3"/>', file: '<path d="M6 3h8l4 4v14H6zM14 3v5h4m-9 5h6m-6 4h6"/>', copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>', menu: '<path d="M4 6h16M4 12h16M4 18h16"/>', help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 4 3c-1 0-1 1-1 2m0 3h.01"/>', external: '<path d="M13 4h7v7m0-7L10 14m0-10H4v16h16v-6"/>', play: '<path d="m9 5 11 7-11 7z"/>', chevron: '<path d="m6 9 6 6 6-6"/>', warn: '<path d="M12 4 3 20h18z"/><path d="M12 10v4m0 3h.01"/>', retry: '<path d="M4 12a8 8 0 1 0 3-6.2M4 4v4h4"/>', book: '<path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4zM13 7a3 3 0 0 1 3-3h4v15h-4a3 3 0 0 0-3 2"/>' }[name] || ''}</svg>`;
const mdEngine = window.markdownit({
  html: false,
  linkify: false,
  typographer: false,
  highlight: (str, lang) => {
    if (['python', 'py', 'bash', 'sh', 'shell'].includes(lang))
      return hljs.highlight(str, {
        language: lang === 'py' ? 'python' : lang === 'python' ? 'python' : 'bash',
        ignoreIllegals: true,
      }).value;
    return esc(str);
  },
});
export const md = (s) => DOMPurify.sanitize(mdEngine.render(String(s || '')));
// First rendered block as plain text, so summaries keep symbols like * and `.
export const plainLead = (s) => {
  const box = document.createElement('div');
  box.innerHTML = md(s);
  return (box.firstElementChild || box).textContent.trim();
};

export const avatar = (p) =>
  `<span class="avatar" aria-hidden="true">${esc(p?.initial || '')}</span>`;
export const checkSvg =
  '<svg class="draw-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path pathLength="1" d="m5 12 4 4L19 6"/></svg>';
export const btn = (label, action, extra = '', cls = 'secondary') =>
  `<button class="btn ${cls}" data-action="${action}" ${extra}>${label}</button>`;

export function excerpt(value, limit = 60) {
  const text = String(value || '')
    .trim()
    .replace(/[ \t]*\r?\n[\s]*/g, '');
  const chars = Array.from(text);
  if (chars.length <= limit) return text;
  const prefix = chars.slice(0, Math.max(1, limit - 2));
  const punctuation = prefix
    .map((ch, i) => (/[。！？；，、：.!?;,]/u.test(ch) ? i : -1))
    .filter((i) => i >= Math.floor(prefix.length / 2))
    .at(-1);
  return (
    prefix.slice(0, punctuation === undefined ? prefix.length : punctuation + 1).join('') + '……'
  );
}

export function greeting(now = new Date()) {
  const h = now.getHours();
  return h >= 5 && h < 11
    ? '早上好。'
    : h >= 11 && h < 13
      ? '中午好。'
      : h >= 13 && h < 18
        ? '下午好。'
        : '晚上好。';
}

export function dnaArtwork() {
  const x = (y, sign) => 120 + sign * 76 * Math.sin(((y - 40) * Math.PI) / 80);
  const derivative = (y, sign) =>
    ((sign * 76 * Math.PI) / 80) * Math.cos(((y - 40) * Math.PI) / 80);
  const strand = (sign) => {
    let d = `M ${x(40, sign)} 40`;
    for (let y = 40; y < 440; y += 8) {
      const end = y + 8;
      d += ` C ${x(y, sign) + (derivative(y, sign) * 8) / 3} ${y + 8 / 3} ${x(end, sign) - (derivative(end, sign) * 8) / 3} ${end - 8 / 3} ${x(end, sign)} ${end}`;
    }
    return d;
  };
  return html`
    <svg class="welcome-art" viewBox="0 0 240 480" fill="none" stroke="var(--line-strong)" stroke-width="1" aria-hidden="true">
      <path class="dna-strand" vector-effect="non-scaling-stroke" d="${strand(1)}"/>
        <path class="dna-strand" vector-effect="non-scaling-stroke" d="${strand(-1)}"/>${Array.from(
          { length: 26 },
          (_, i) => {
            const y = 40 + i * 16;
            return `<line class="dna-rung" vector-effect="non-scaling-stroke" x1="${x(y, 1)}" x2="${x(y, -1)}" y1="${y}" y2="${y}"/>`;
          },
        ).join('')}</svg>
  `;
}
