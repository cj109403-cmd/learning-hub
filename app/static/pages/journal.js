import { esc, icon, btn, excerpt, plainLead } from '../ui.js';
import { html } from '../html.js';
// Questions worth another look: self-tests marked "还没答对", and task questions answered
// wrong three or more times without a correct answer yet. Read-only view of existing records.
export function reviewItems(cards, track = '') {
  const rows = [];
  for (const c of cards) {
    if (!c.unlocked || (track && c.track_id !== track)) continue;
    const v = c.record || {};
    (c.questions || []).forEach((q, i) => {
      if (v.questions?.[q.id]?.self_mark === 'wrong')
        rows.push({
          c,
          index: i + 1,
          prompt: q.prompt,
          anchor: 'question-' + q.id,
          note: '自测时标记为还没答对',
        });
    });
    (c.checks || []).forEach((q, i) => {
      const attempts = v.checks?.[q.id]?.attempts || [],
        wrong = attempts.filter((a) => !a.correct).length;
      if (wrong >= 3 && !attempts.some((a) => a.correct))
        rows.push({
          c,
          index: i + 1,
          prompt: q.prompt,
          anchor: 'check-' + q.id,
          note: `已经答错 ${wrong} 次，参考解法可以看了`,
        });
    });
  }
  return rows;
}
function review(cards, state, markGlyph) {
  const rows = reviewItems(cards, state.journalTrack),
    groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.c.id)) groups.set(row.c.id, []);
    groups.get(row.c.id).push(row);
  }
  if (!rows.length)
    return html`
      <div class="empty-state">${icon('check')}<h2>现在没有需要回看的题。</h2>
        <p>自测时点了“还没答对”的题，和任务里答错 3 次以上、还没答对的题，会出现在这里。改对之后，它们会自己离开这个列表。</p>
      </div>
    `;
  return `<p class="review-intro">${rows.length} 道题值得再看一眼。改对之后，它们会自己离开这个列表。</p>${[
    ...groups.values(),
  ]
    .map(
      (list) => html`
        <section class="review-group">
          <h2>${list[0].c.code} ${esc(list[0].c.title)}</h2>${list
            .map(
              (row) => html`
                <button class="review-item" data-action="review-item" data-id="${row.c.id}" data-anchor="${row.anchor}">${markGlyph('wrong')}<span class="review-copy">
                    <span class="review-prompt">第 ${row.index} 题　${esc(excerpt(plainLead(row.prompt), 56))}</span>
                    <span class="meta">${esc(row.note)}</span>
                  </span>${icon('arrow')}</button>
              `,
            )
            .join('')}</section>
      `,
    )
    .join('')}`;
}
export function journal({ data, course, cards, state, messageTime, markGlyph }) {
  const entries = state.journal === 'notes' ? data.notes || [] : data.entries || [],
    groups = new Map();
  for (const entry of entries) {
    const key = state.journal === 'notes' ? entry.card.track_id : entry.week;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  return html`
    <section class="shell journal">
      <header class="page-header with-action">
        <div>
          <h1 class="display" tabindex="-1">记录</h1>
          <p class="lead">把走过的路，写成自己的话。</p>
        </div>${btn('导出学习记录', 'export')}</header>
      <div class="journal-toolbar">
        <div class="journal-tabs" role="tablist" aria-label="记录视图">${[
          ['timeline', '时间线'],
          ['notes', '我的笔记'],
          ['review', '回看'],
        ]
          .map(([id, t]) => {
            const n = id === 'review' ? reviewItems(cards, state.journalTrack).length : 0;
            return `<button role="tab" tabindex="${state.journal === id ? 0 : -1}" aria-controls="journal-content" aria-selected="${state.journal === id}" data-action="journal-tab" data-tab="${id}">${t}${n ? `<span class="tab-count" aria-label="，${n} 道">${n}</span>` : ''}</button>`;
          })
          .join('')}</div>
        <label class="journal-filter">
          <span class="sr-only">学习路线</span>
          <select id="journal-track" name="track" autocomplete="off" aria-label="学习路线">
            <option value="">全部路线</option>${course.tracks.map((t) => `<option value="${t.id}" ${state.journalTrack === t.id ? 'selected' : ''}>${esc(t.label)} ${esc(t.title)}</option>`).join('')}</select>
        </label>${state.journal === 'notes' && entries.length ? btn('复制全部笔记', 'copy-notes', '', 'quiet') : ''}</div>
      <div id="journal-content" role="tabpanel" aria-label="${state.journal === 'notes' ? '我的笔记' : state.journal === 'review' ? '回看' : '时间线'}">${
        state.journal === 'review'
          ? review(cards, state, markGlyph)
          : !entries.length
            ? html`
              <div class="empty-state">${icon('journal')}<h2>你的第一条发现，会留在这里。</h2>
                <p>完成第一张卡后，你写下的总结会出现在这里。</p>${cards[0] ? btn(`从 ${cards[0].code} 开始`, 'card', `data-id="${cards[0].id}"`, 'primary') : ''}</div>
            `
            : [...groups]
                .map(([key, rows]) => {
                  const track = course.tracks.find((t) => t.id === key);
                  return state.journal === 'notes'
                    ? html`
                      <section class="notes-list">
                        <h2>${esc(track?.label)}　${esc(track?.title)}</h2>${rows
                          .map(
                            ({ card: c, text }) =>
                              `<article class="note-entry">${
                                c.retired
                                  ? html`
                                    <h3 class="note-title">${c.code} ${esc(c.title)} <span class="meta">已退休，仅保留记录</span>
                                    </h3>
                                  `
                                  : `<button class="link-button note-title" data-action="card" data-id="${c.id}">${c.code} ${esc(c.title)}</button>`
                              }${text.trim() ? `<p>${esc(text)}</p>` : `<p class="note-missing">未写总结</p>${c.takeaway ? `<blockquote class="note-takeaway">${esc(c.takeaway)}</blockquote>` : ''}${c.retired ? '' : btn('现在补写', 'write-summary', `data-id="${c.id}"`, 'quiet')}`}</article>`,
                          )
                          .join('')}</section>
                    `
                    : html`
                      <section class="timeline-group">
                        <h2>${esc(key)}</h2>${rows
                          .map(
                            ({ card: c, text, at }) => html`
                              <${c.retired ? 'article' : 'button'} class="journal-entry ${c.retired ? 'retired' : ''}" ${c.retired ? '' : `data-action="card" data-id="${c.id}"`}>
                              <span class="entry-date meta">${esc(messageTime(at))}</span>
                              <span class="entry-content">
                                <span class="entry-title">${c.code} ${esc(c.title)}</span>${text.trim() ? `<span class="entry-summary">${esc(text)}</span>` : '<span class="entry-summary entry-no-summary">完成了，没有写总结</span>'}</span>${c.retired ? '<span class="meta">已退休，仅保留记录</span>' : icon('arrow')}</${c.retired ? 'article' : 'button'}>
                            `,
                          )
                          .join('')}</section>
                    `;
                })
                .join('')
      }</div>
    </section>
  `;
}
