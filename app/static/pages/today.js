import { esc, icon, avatar, btn, excerpt, greeting } from '../ui.js';
import { html } from '../html.js';
export function today({ data, course, rec, person, summaryPhases, miniRoute, state, messageTime }) {
  const c = data.main,
    recent = data.recent || [],
    date = new Intl.DateTimeFormat('zh-CN', {
      month: 'long',
      day: 'numeric',
      weekday: 'long',
    }).format(new Date());
  const first = state.firstToday;
  state.firstToday = false;
  const latest = data.latest_message;
  const lab = latest
    ? html`
      <div class="person-line">${avatar(latest.person || person(latest.card))}<span>${esc((latest.person || person(latest.card)).name)}</span>${latest.kind === 'task' && !latest.card.record?.started_at ? '<span class="unread" aria-label="有未读任务"></span>' : ''}</div>
      <p class="message-preview">${esc(excerpt(latest.text))}</p>
      <time class="meta" datetime="${esc(latest.at)}">${esc(messageTime(latest.at))}</time>
    `
    : `<div class="today-people">${course.people
        .map(
          (p) => html`
            <div class="today-person">${avatar(p)}<span class="today-person-copy">
                <strong class="today-person-name">${esc(p.name)}</strong>
                <span class="today-person-role">${esc(p.role)}</span>
              </span>
            </div>
          `,
        )
        .join('')}</div>`;
  return html`
    <section class="shell today ${first ? 'today-first' : ''}">
      <header class="page-header today-stagger">
        <p class="meta">${esc(date)}</p>
        <h1 class="hero" tabindex="-1">${greeting()}</h1>
        <p class="lead">${esc(data.sentence)}</p>
      </header>
      <div class="today-grid">
        <div class="today-main today-stagger">${
          c
            ? html`
              <article class="hero-card" data-action="hero-open" data-id="${c.id}">
                <div class="hero-meta meta">
                  <span>
                    <i class="track-dot"></i>${c.code} ${c.type === 'task' ? '导师任务' : '学习卡'}</span>
                  <span>约 ${c.minutes} 分钟</span>
                </div>
                <h2 class="card-title" style="view-transition-name:card-title">${esc(c.title)}</h2>
                <p class="hero-subtitle">${esc(c.subtitle)}</p>${
                  c.type === 'task'
                    ? html`
                      <p class="hero-mentor">${avatar(person(c))}<span>${esc(person(c).name)}：${esc(excerpt(c.message, 54))}</span>
                      </p>
                    `
                    : ''
                }${summaryPhases(c)}<div class="actions">${btn(rec(c).started ? '继续学习' : '开始学习', 'card', `data-id="${c.id}"`, 'primary')}<span class="meta">
                    <kbd>Enter</kbd>
                  </span>
                </div>${c.reason && !rec(c).started ? `<p class="meta">${esc(c.reason)}</p>` : ''}</article>
            `
            : html`
              <div class="hero-card all-done">
                <span class="completion-emblem">${icon('check')}</span>
                <h2 class="card-title">把学到的，留在手边。</h2>
                <p class="lead">翻翻笔记，或者重看一张想再练练的卡。</p>${btn('看看我的笔记', 'notes', '', 'primary')}</div>
            `
        }</div>
        <aside class="today-aside today-stagger">
          <section class="aside-section">
            <div class="section-label">
              <h2>课题组</h2>
              <button class="icon-button" data-action="nav" data-view="lab" aria-label="打开课题组">${icon('arrow')}</button>
            </div>${lab}</section>
          <section class="aside-section">
            <h2>你的路线</h2>${(data.tracks || [])
              .map((t) =>
                t.total
                  ? html`
                    <div class="route-summary">
                      <span>${esc(t.label)}　${esc(t.title)}</span>
                      <span class="number-roll">${t.done} / ${t.total}</span>
                    </div>${miniRoute(t.cards || [], '', t)}`
                  : html`
                    <p class="upcoming-small">${esc(t.label)} ${esc(t.title)} <span>即将推出</span>
                    </p>
                  `,
              )
              .join('')}</section>
        </aside>
      </div>${
        data.others?.length
          ? html`
            <section class="recent-section today-stagger">
              <h2 class="section-title">也可以学</h2>
              <div class="today-others">${data.others
                .map(
                  (x) => html`
                    <button class="recommendation-card" data-action="card" data-id="${x.id}">
                      <span class="meta">${esc(x.code)} <span>约 ${x.minutes} 分钟</span>
                      </span>
                      <h3>${esc(x.title)}</h3>
                      <p>${esc(x.subtitle)}</p>
                      <span class="meta">${esc(x.reason || '')}</span>
                    </button>
                  `,
                )
                .join('')}</div>
            </section>
          `
          : ''
      }${
        recent.length
          ? html`
            <section class="recent-section today-stagger">
              <h2 class="section-title">最近学到的</h2>${recent
                .map(
                  (x) => html`
                    <button class="quote-row" data-action="card" data-id="${x.id}">
                      <span class="quote-text">“${esc(x.record?.summary || rec(x).summary)}”</span>
                      <span class="meta">${x.code} ${esc(x.title)}<span>${esc(messageTime(x.record?.done_at || rec(x).doneAt))}</span>
                      </span>
                    </button>
                  `,
                )
                .join('')}</section>
          `
          : ''
      }</section>
  `;
}
