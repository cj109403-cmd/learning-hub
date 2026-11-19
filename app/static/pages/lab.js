import { esc, icon, avatar, excerpt } from '../ui.js';
import { html } from '../html.js';
export function lab({ data, course, state, mentor, messageTime }) {
  const p = data.people.find((p) => p.id === state.contact) || data.people[0];
  if (!p) return '';
  return html`
    <section class="shell lab">
      <header class="page-header">
        <h1 class="display" tabindex="-1">课题组</h1>
        <p class="lead">学习里的问题，也是一段段对话。</p>
        <p class="meta">${esc(course.lab.name)}<span>虚构课题组</span>
        </p>
      </header>
      <div class="lab-layout ${state.conversationOpen ? 'conversation-open' : ''}">
        <aside class="contacts" aria-label="联系人">${data.people
          .map((person) => {
            const last = person.messages.at(-1);
            return html`
              <button class="contact ${p.id === person.id ? 'selected' : ''}" data-action="contact" data-person="${person.id}">${avatar(person)}<span>
                  <strong>${esc(person.name)}</strong>
                  <span class="meta">${esc(last ? excerpt(last.text, 20) : person.id === course.welcome.from ? excerpt(course.welcome.message, 20) : '还没有新任务')}</span>
                </span>${person.unread ? '<span class="unread" aria-label="有未读任务"></span>' : ''}</button>
            `;
          })
          .join('')}</aside>
        <section class="conversation" aria-label="与${esc(p.name)}的对话">
          <div class="conversation-title">
            <button class="icon-button lab-back" data-action="contacts-back" aria-label="返回联系人">${icon('back')}</button>
            <div>
              <h2>${esc(p.name)}</h2>
              <p class="meta">${esc(p.role)}</p>
            </div>
          </div>${
            p.id === course.welcome.from
              ? html`
                <div class="message">
                  <p class="conversation-date meta">入组时</p>${mentor(course.welcome, course.welcome.message)}</div>
              `
              : ''
          }${p.messages
            .map((m) =>
              m.kind === 'summary'
                ? html`
                  <div class="message mine">
                    <p class="meta">你</p>
                    <div class="bubble">${esc(m.text).replace(/\n/g, '<br>')}</div>
                  </div>
                `
                : html`
                  <div class="message">
                    <p class="conversation-date meta">${esc(messageTime(m.at))}</p>${mentor(m.card, m.text)}${
                      m.kind === 'task'
                        ? html`
                          <button class="task-link" data-action="card" data-id="${m.card.id}">
                            <span class="meta">${m.card.code}　约 ${m.card.minutes} 分钟</span>
                            <strong>${esc(m.card.title)}</strong>
                            <span>${m.card.status === 'done' ? '重新阅读' : '打开任务'} ${icon('arrow')}</span>
                          </button>
                        `
                        : ''
                    }</div>
                `,
            )
            .join(
              '',
            )}${!p.messages.length && p.id !== course.welcome.from ? '<div class="empty-state"><h3>对话从一个任务开始。</h3><p>学到对应的任务时，消息会出现在这里。</p></div>' : ''}</section>
      </div>
    </section>
  `;
}
