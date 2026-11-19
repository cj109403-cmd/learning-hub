import { esc, icon, avatar } from '../ui.js';
import { html } from '../html.js';
export function route({ data, state, person }) {
  return html`
    <section class="shell route">
      <header class="page-header">
        <h1 class="display" tabindex="-1">路线</h1>
        <p class="lead">从第一行代码，到用数据回答问题。</p>
      </header>
      <div class="track-tabs" role="tablist" aria-label="学习路线">${data.tracks.map((t) => `<button role="tab" tabindex="${state.track === t.id ? 0 : -1}" aria-controls="track-${t.id}" aria-selected="${state.track === t.id}" data-action="track" data-track="${t.id}">${esc(t.label)} ${esc(t.title)}</button>`).join('')}</div>
      <div class="route-grid">${data.tracks
        .map(
          (t, i) =>
            html`
              <section class="track-column ${t.id === state.track ? 'selected-track' : ''} ${t.cards.length ? '' : 'upcoming'}" data-track="${esc(t.label)}" style="--track-color:var(--track-${['a', 'b', 'c'][i % 3]})" id="track-${t.id}" role="tabpanel">
                <div class="track-heading">
                  <span class="track-letter">${esc(t.label)}</span>
                  <h2>${esc(t.title)}</h2>
                </div>
                <p class="meta">${t.cards.length ? `${t.cards.filter((c) => c.status === 'done').length} / ${t.cards.length}　约 ${Math.round(t.cards.reduce((n, c) => n + c.minutes, 0) / 60)} 小时` : '即将推出'}</p>${t.dependency_note ? `<p class="meta">${esc(t.dependency_note)}</p>` : ''}${
                  !t.cards.length
                    ? html`
                      <p class="track-description">${esc(t.summary)}</p>
                      <div class="future-line" aria-hidden="true"></div>
                    `
                    : `<ol class="route-list">${t.cards
                        .map((c) => {
                          const done = c.status === 'done',
                            isCurrent = data.current === c.id,
                            locked = !c.unlocked;
                          return html`
                            <li class="route-node ${done ? 'done' : ''} ${isCurrent ? 'current' : ''} ${locked ? 'locked' : ''} ${c.capstone ? 'capstone' : ''}" id="node-${c.id}">
                              <span class="node-dot" aria-hidden="true">${done ? icon('check') : ''}</span>
                              <button class="node-content" data-action="route-card" data-id="${c.id}" aria-describedby="reason-${c.id}">
                                <span class="node-title">
                                  <span class="node-code">${c.code}</span>${esc(c.title)}</span>
                                <span class="node-meta">${isCurrent ? '<span class="you-are-here">你在这里</span>' : ''}${c.from ? `${avatar(person(c))}<span>${esc(person(c).name)}</span>` : ''}${c.capstone ? '<span class="badge">毕业任务</span>' : ''}${state.newCards.includes(c.id) ? '<span class="badge">新</span>' : ''}<span class="node-minutes">${c.minutes} 分钟</span>
                                </span>
                              </button>
                              <div class="lock-reason" id="reason-${c.id}" hidden>${locked ? '需要先完成 ' + (c.locked_reasons || []).map((x) => esc(x.code + ' ' + x.title)).join('、') : ''}</div>
                            </li>
                          `;
                        })
                        .join('')}</ol>`
                }</section>
            `,
        )
        .join('')}</div>
    </section>
  `;
}
