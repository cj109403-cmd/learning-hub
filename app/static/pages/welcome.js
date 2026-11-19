import { esc, md, avatar, btn, dnaArtwork } from '../ui.js';
import { html } from '../html.js';
export function welcome({ course, cards, person }) {
  const p = person(course.welcome);
  return html`
    <section class="welcome">
      <div class="welcome-inner">
        <p class="meta">${esc(course.lab.name)} <span>虚构课题组</span>
        </p>
        <h1 class="hero" tabindex="-1">欢迎加入。</h1>
        <div class="welcome-person">${avatar(p)}<span>${esc(p.name)}</span>
        </div>
        <div class="welcome-message">${md(course.welcome.message)}</div>
        <p class="welcome-intro">接下来，你会从写下第一行代码开始，<br>一步一步学会用数据回答实验里的问题。</p>
        <div class="actions">${btn(`从 ${cards[0]?.code || '第一张卡'} 开始`, 'start-welcome', cards.length ? '' : 'disabled', 'primary')}<span class="meta">按 <kbd>Enter</kbd>
          </span>
        </div>
        <button class="link-button welcome-settings" data-action="modal" data-modal="settings">选择你喜欢的纸页或清透外观</button>
      </div>${dnaArtwork()}</section>
  `;
}
