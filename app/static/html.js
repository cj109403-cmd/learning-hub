/**
 * Tag for HTML templates that are written across several lines.
 *
 * Templates are indented like ordinary HTML so they are easy to read. The line
 * breaks and indentation *between two tags* are only layout: this tag removes
 * them, so the page receives exactly the same HTML as a one-line template would.
 * Spaces on a single line and every ${value} are kept as they are.
 *
 *   html`
 *     <p class="meta">
 *       <span>${esc(name)}</span>
 *     </p>`  ===  `<p class="meta"><span>${esc(name)}</span></p>`
 */
export function html(strings, ...values) {
  const last = strings.length - 1;
  let out = '';
  for (let i = 0; i <= last; i++) {
    let text = strings[i].replace(/>\n\s*</g, '><');
    if (i === 0) text = text.replace(/^\n\s*/, '');
    if (i === last) text = text.replace(/\n\s*$/, '');
    out += text;
    if (i < last) out += String(values[i]);
  }
  return out;
}
