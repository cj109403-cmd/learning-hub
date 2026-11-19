import { fileManager } from '../platform.js';
import { html } from '../html.js';
export function cardComponents(ctx) {
  const {
    $,
    $$,
    esc,
    icon,
    md,
    plainLead,
    avatar,
    checkSvg,
    btn,
    excerpt,
    markGlyph,
    prefs,
    rec,
    person,
    people,
    cards,
    course,
    state,
    current,
    phases,
    phaseNames,
    phaseDone,
    miniRoute,
    missing,
    draftNotice,
  } = ctx;
  function lessonLearn(c) {
    return html`${md(c.sections['讲解'])}<section id="resources">
        <h2>学习资源</h2>${resources(c)}</section>
      <section id="how">
        <h2>怎么学</h2>${md(c.sections['怎么学'])}</section>${
          c.sections['延伸']
            ? html`
              <section id="extend">
                <h2>延伸</h2>${md(c.sections['延伸'])}</section>
            `
            : ''
        }<div class="phase-footer">${btn('开始自测', 'next-phase', '', 'primary')}<span class="meta">也可以按 <kbd>→</kbd>
        </span>
      </div>`;
  }
  function resources(c) {
    return (c.resources || [])
      .map(
        (r, i) =>
          html`
            <article class="resource-card">
              <div class="resource-meta meta">
                <span>${esc(r.source)}</span>
                <span>${r.required ? '必看' : '选看'}　${r.minutes} 分钟</span>
              </div>
              <h3>${esc(r.title)}</h3>
              <p class="resource-focus">${esc(r.focus)}</p>${
                ['youtube', 'bilibili'].includes(r.embed)
                  ? html`
                    <button class="video-placeholder" data-action="video" data-url="${esc(r.url)}" data-embed="${esc(r.embed)}" aria-label="播放视频：${esc(r.title)}">${icon('play')}<span>点击加载播放器</span>
                    </button>
                  `
                  : ''
              }<div class="resource-actions">
                <a class="btn secondary" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">打开 ${icon('external')}<span class="sr-only">（在新窗口打开）</span>
                </a>
                <label class="check-label">
                  <input type="checkbox" name="resource-${i}" autocomplete="off" data-resource="${i}" ${rec(c).resources.includes(i) ? 'checked' : ''}>标记为已完成</label>
              </div>
            </article>
          `,
      )
      .join('');
  }
  function mentor(c, text = c.message) {
    return html`
      <div class="mentor-message">${avatar(person(c))}<div>
          <div class="message-header">
            <strong>${esc(person(c).name)}</strong>
            <span class="meta">${esc(person(c).role)}</span>
          </div>
          <div class="bubble prose">${md(text)}</div>
        </div>
      </div>
    `;
  }
  function taskLearn(c) {
    return html`${mentor(c)}<p class="simulation-note meta">${c.simulated ? '本任务使用模拟数据' : '真实公开数据'}</p>${
      !c.simulated && c.source
        ? html`
          <p class="source-note">${esc(c.source.citation)} <a href="${esc(c.source.url)}" target="_blank" rel="noopener noreferrer">查看来源</a> <span class="meta">${esc(c.source.license)}</span>
          </p>
        `
        : ''
    }${md(c.sections['任务说明'])}${
      c.data?.length
        ? html`
          <section class="files" id="data-files">
            <h2>数据文件</h2>${c.data
              .map(
                (f) => html`
                  <div class="file-row">${icon('file')}<div>
                      <code>${esc(f.file)}</code>
                      <p class="meta">${esc(f.description)}</p>
                    </div>
                    <div class="file-actions">${btn('在' + fileManager() + '中显示', 'reveal-file', `data-file="${esc(f.file)}"`, 'quiet')}${btn('复制路径', 'copy-path', `data-file="${esc(f.file)}"`, 'quiet')}</div>
                  </div>
                `,
              )
              .join('')}</section>
        `
        : ''
    }<div class="phase-footer">${btn('开始动手', 'next-phase', '', 'primary')}<span class="meta">也可以按 <kbd>→</kbd>
        </span>
      </div>`;
  }
  function questionHTML(c, q, i) {
    const r = rec(c),
      mark = r.marks[q.id],
      opened = r.allQuestions || r.activeQ === i;
    return html`
      <article class="question ${opened ? 'active' : ''} ${mark ? 'marked' : ''}" id="question-${q.id}" data-qid="${q.id}">
        <button class="question-summary" data-action="question-open" data-index="${i}" ${opened ? 'hidden' : ''}>
          <span>${i + 1}</span>
          <span>${esc(excerpt(plainLead(q.prompt), 20))}</span>${markGlyph(mark)}</button>
        <div class="question-body reveal ${opened ? 'open' : ''}" ${opened ? '' : 'inert'}>
          <div class="reveal-inner">
            <div class="question-number meta">第 ${i + 1} 题，共 ${c.questions.length} 题</div>
            <div class="question-prompt prose">${md(q.prompt)}</div>
            <label class="field">
              <span>先写下你的想法 <span class="meta">可选</span>
              </span>
              <textarea name="answer-${q.id}" data-answer="${q.id}" rows="3" autocomplete="off">${esc(r.answers[q.id] || '')}</textarea>
            </label>
            <div class="actions">${btn('对照答案', 'reveal-answer', `data-qid="${q.id}"`)}<kbd>⌘Enter</kbd>
            </div>
            <div class="reveal answer-reveal ${r.reveals[q.id] ? 'open' : ''}" ${r.reveals[q.id] ? '' : 'inert'}>
              <div class="reveal-inner">
                <div class="answer-panel prose">
                  <p class="meta">参考答案</p>${md(q.answer)}</div>
                <div class="question-actions">${btn(`${icon('check')} 我答对了 <kbd>Y</kbd>`, 'mark-question', `data-qid="${q.id}" data-mark="correct"`)}${btn('还没答对 <kbd>N</kbd>', 'mark-question', `data-qid="${q.id}" data-mark="wrong"`)}${btn('复制给 AI 批改', 'copy-question', `data-qid="${q.id}"`, 'quiet')}</div>
              </div>
            </div>
          </div>
        </div>
      </article>
    `;
  }
  function lessonPractice(c) {
    return html`
      <div class="quiz-toolbar">
        <h2>把想法说出来。</h2>
        <label class="check-label">
          <input type="checkbox" id="all-questions" name="all-questions" autocomplete="off" ${rec(c).allQuestions ? 'checked' : ''}>全部展开</label>
      </div>
      <p class="meta quiz-progress">已答对 ${Object.values(rec(c).marks).filter((x) => x === 'correct').length} / ${c.questions.length}</p>
      <div class="questions">${c.questions.map((q, i) => questionHTML(c, q, i)).join('')}</div>
      <div class="quiz-finish phase-footer" ${c.questions.every((q) => rec(c).marks[q.id]) ? '' : 'hidden'}>${btn('去写总结', 'next-phase', '', 'primary')}<span class="meta">答错的题可以随时回来再看。</span>
      </div>
    `;
  }
  function checkHTML(c, q, i) {
    const r = rec(c).checks[q.id] || {},
      correct = r.correct,
      shown =
        q.kind === 'choice' ? (q.options.find((o) => o.id === r.value)?.text ?? r.value) : r.value;
    return html`
      <article class="check-card ${correct ? 'correct' : ''}" id="check-${q.id}" data-checkid="${q.id}">
        <div class="check-heading">
          <span class="meta">${i + 1} / ${c.checks.length}</span>
          <span class="meta">已尝试 ${r.attempts || 0} 次</span>
        </div>
        <div class="prose">${md(q.prompt)}</div>${
          correct && !r.editing
            ? html`
              <div class="check-success">${checkSvg}<div>
                  <strong>已答对</strong>
                  <p>你的答案：${esc(shown ?? '')}</p>
                </div>${btn('重新查看', 'reopen-check', `data-checkid="${q.id}"`, 'quiet')}</div>
            `
            : html`
              <label class="field">
                <span class="sr-only">${esc(q.prompt)}</span>${
                  q.kind === 'choice'
                    ? html`
                      <select name="check-${q.id}" data-check="${q.id}">
                        <option value="">请选择</option>${q.options.map((o) => `<option value="${esc(o.id)}" ${(r.draft ?? r.value) === o.id ? 'selected' : ''}>${esc(o.text)}</option>`).join('')}</select>
                    `
                    : `<input name="check-${q.id}" data-check="${q.id}" value="${esc(r.draft ?? r.value ?? '')}" autocomplete="off" ${['number', 'integer'].includes(q.kind) ? 'inputmode="decimal"' : ''} placeholder="写下你的答案…" spellcheck="false">`
                }</label>
              <div class="actions">${btn('检查答案', 'check', `data-checkid="${q.id}"`)}<kbd>⌘Enter</kbd>
              </div>${r.wrong ? '<p class="wrong-feedback" role="status">不对，再检查一下。</p>' : ''}`
        }</article>
    `;
  }
  function solutionHTML(c) {
    const unlocked = c.solution_unlocked;
    return html`
      <div class="solution-block" id="solution">
        <h3>参考解法</h3>${
          unlocked
            ? html`${btn(rec(c).solutionOpen ? '收起参考解法' : '展开参考解法', 'solution')}<div class="reveal ${rec(c).solutionOpen ? 'open' : ''}">
                <div class="reveal-inner prose">${rec(c).solutionOpen ? md(c.sections['参考解法']) : ''}</div>
              </div>`
            : `<p class="meta">${icon('lock')} 完成任务后，或同一道题答错 3 次后，可以查看。</p>`
        }</div>
    `;
  }
  function taskPractice(c) {
    return html`
      <h2>让代码回答问题。</h2>
      <div class="checks">${c.checks.map((q, i) => checkHTML(c, q, i)).join('')}</div>
      <section class="hints" id="hints">
        <h2>需要一点思路？</h2>${c.hints
          .map(
            (h, i) => html`
              <div class="hint-block">
                <button class="hint-toggle" data-action="hint" data-index="${i}" ${i > rec(c).hints ? 'disabled' : ''} aria-expanded="${i < rec(c).hints}">
                  <span>${esc((people[c.hints_from] || people[course.hints_from] || person(c)).name)}的提示 ${i + 1}/${c.hints.length}</span>
                  <span class="hint-state">${i > 0 ? `<span class="hint-lock" ${i > rec(c).hints ? '' : 'hidden'}>先看提示 ${i}</span>` : ''}${icon('chevron')}</span>
                </button>
                <div class="reveal ${i < rec(c).hints ? 'open' : ''}">
                  <div class="reveal-inner prose">${md(h)}</div>
                </div>
              </div>
            `,
          )
          .join(
            '',
          )}</section>${solutionHTML(c)}<div class="phase-footer">${btn('去写总结', 'next-phase', '', 'primary')}</div>
    `;
  }
  function completionHTML(c, animate = false) {
    const result = state.completionResult,
      next = result?.card?.id === c.id ? result.next : null,
      track = course.tracks.find((t) => t.id === c.track_id),
      trackCards = cards.filter((x) => x.track_id === c.track_id),
      done = trackCards.filter((x) => x.status === 'done').length;
    return html`
      <section class="completion-card ${animate ? 'is-revealing' : ''}" aria-live="polite">
        <div class="completion-heading">${checkSvg}<h2>完成了 ${c.code} ${esc(c.title)}</h2>
        </div>
        <div class="completion-route">
          <span class="meta">${esc(track?.label)}　${esc(track?.title)}</span>
          <span class="number-roll">${
            animate
              ? html`
                <span class="number-window">
                  <span class="number-old">${done - 1}</span>
                  <span class="number-new">${done}</span>
                </span>
              `
              : done
          } / ${trackCards.length}</span>${miniRoute(trackCards, animate ? c.id : '')}</div>${c.type === 'task' ? `<p class="completion-delivered">你的总结已发给${esc(person(c).name)}</p>${c.debrief ? mentor(c, c.debrief) : ''}` : ''}<div class="completion-next actions">${next ? btn(`下一张：${next.code} ${esc(next.title)} ${icon('arrow')}`, 'card', `data-id="${next.id}"`, 'primary') : btn('看看我的笔记', 'notes', '', 'primary')}${btn('回到今天', 'nav', 'data-view="today"', 'quiet')}</div>
      </section>
    `;
  }
  function wrap(c) {
    const r = rec(c);
    return html`
      <div class="wrap-form">${
        c.writeup
          ? html`
            <section id="writeup">
              <h2>把你的判断写清楚。</h2>
              <div class="prose">${md(c.writeup.prompt)}</div>
              <label class="field">
                <span class="sr-only">文字结论</span>
                <textarea name="writeup" autocomplete="off" id="writeup-input" rows="7">${esc(r.writeup)}</textarea>
              </label>
              <fieldset id="rubrics">
                <legend>写完后，逐条核对</legend>${c.writeup.rubric
                  .map(
                    (x, i) => html`
                      <label class="check-label">
                        <input type="checkbox" id="rubric-${i}" name="rubric-${i}" autocomplete="off" data-rubric="${i}" ${r.rubric.includes(i) ? 'checked' : ''}>${esc(x)}</label>
                    `,
                  )
                  .join(
                    '',
                  )}</fieldset>${btn('复制给 AI 点评', 'copy-writeup', '', 'quiet')}</section>
          `
          : ''
      }<section id="summary">
          <h2>这一节，你学到了什么？${prefs.require_summary ? '' : '<span class="optional-tag">可选</span>'}</h2>
          <p class="meta">用一句话写下来。以后会出现在“今天”页上提醒你。</p>
          <label class="field">
            <span class="sr-only">一句话总结</span>
            <textarea name="summary" autocomplete="off" id="summary-input" class="summary-input" rows="4" placeholder="写下一点想记住的发现…">${esc(r.summary)}</textarea>
          </label>${takeawayHTML(c)}<div class="complete-actions actions" id="complete-actions">${r.done ? btn(`${icon('check')} 已完成`, 'complete', 'disabled', 'primary is-done') : btn('完成这张卡', 'complete', missing(c).length ? 'disabled' : '', 'primary')}<div id="missing-reasons">${missing(
            c,
          )
            .map(
              (m) =>
                `<button class="link-button" data-action="missing" ${m.phase ? `data-phase="${m.phase}" data-anchor="${esc(m.anchor || '')}"` : `data-anchor="${m.anchor}"`}>${esc(m.label)}</button>`,
            )
            .join('')}</div>
            <p class="save-status" role="status" id="save-status"></p>
          </div>
        </section>
        <div id="completion-slot">${r.done ? completionHTML(c) : ''}</div>
      </div>
    `;
  }
  // Shown once, the first time any card opens; dismissed by the button or by switching phase.
  function cardTip(c) {
    const [a, b, d] = phaseNames(c);
    return html`
      <aside class="card-tip" id="card-tip" aria-label="怎么用这张卡">
        <div class="card-tip-body">
          <p>每张卡分三步：<strong>${a}</strong> → <strong>${b}</strong> → <strong>${d}</strong>。顶部的三个阶段可以随时切换，写下的内容会自动保存。</p>
          <p class="card-tip-keys">
            <span>
              <kbd>←</kbd>
              <kbd>→</kbd> 切换阶段</span>
            <span>
              <kbd>⌘Enter</kbd> ${c.type === 'task' ? '检查答案' : '对照答案'}</span>
            <span>
              <kbd>?</kbd> 全部快捷键</span>
          </p>
        </div>${btn('知道了', 'dismiss-tip', '', 'quiet')}</aside>
    `;
  }
  // learning-ui §14.1: the reference point stays folded; try to recall first, then look.
  function takeawayHTML(c) {
    if (!c.takeaway) return '';
    return html`
      <div class="takeaway">
        <button class="link-button" data-action="toggle-takeaway" aria-expanded="false" aria-controls="takeaway-panel">写不出来？看看这节的要点</button>
        <div class="reveal" id="takeaway-panel" inert>
          <div class="reveal-inner">
            <div class="takeaway-panel">
              <p class="takeaway-text">${esc(c.takeaway)}</p>
              <div class="actions">${btn('用这句作为我的总结', 'use-takeaway', '', 'secondary')}<span class="takeaway-confirm" hidden>替换你写的内容吗？${btn('替换', 'confirm-takeaway', '', 'quiet')}${btn('取消', 'cancel-takeaway', '', 'quiet')}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }
  function phaseHTML(c) {
    return rec(c).phase === 'learn'
      ? c.type === 'lesson'
        ? lessonLearn(c)
        : taskLearn(c)
      : rec(c).phase === 'practice'
        ? c.type === 'lesson'
          ? lessonPractice(c)
          : taskPractice(c)
        : wrap(c);
  }
  function phaseTabs(c) {
    return html`
      <div class="phase-tabs" role="tablist" aria-label="学习阶段" style="--phase:${phases.indexOf(rec(c).phase)}">
        <span class="phase-pill" aria-hidden="true"></span>${phaseNames(c)
          .map(
            (name, i) =>
              `<button role="tab" tabindex="${rec(c).phase === phases[i] ? 0 : -1}" aria-controls="phase-host" aria-selected="${rec(c).phase === phases[i]}" data-action="phase" data-phase="${phases[i]}">${phaseDone(c, phases[i]) ? icon('check') : i + 1} <span>${name}</span>${i === 1 && Object.values(rec(c).marks).includes('wrong') ? '<span class="tab-dot" role="img" aria-label="有题目需要重看"></span>' : ''}</button>`,
          )
          .join('')}</div>
    `;
  }
  function card() {
    const c = current();
    return html`
      <div class="card-topbar">
        <button class="icon-button" data-action="nav" data-view="route" aria-label="返回路线">${icon('back')}</button>
        <span class="topbar-card">${c.code} ${esc(c.title)}</span>${phaseTabs(c)}<button class="btn quiet" data-action="modal" data-modal="help">卡住了</button>
        <button class="icon-button" data-action="modal" data-modal="command" aria-label="搜索 ⌘K">${icon('search')}</button>
        <div class="reading-progress" aria-hidden="true"></div>
      </div>
      <div class="reader-grid">
        <aside class="toc" aria-label="本页内容"></aside>
        <article class="article" data-phase="${rec(c).phase}">
          <div class="toc-mobile"></div>
          <header class="article-header">
            <p class="meta">${c.code}<span>${esc(c.track_title)}</span>
              <span>${c.type === 'task' ? '导师任务' : '学习卡'}</span>
            </p>
            <h1 class="display" tabindex="-1" style="view-transition-name:card-title">${esc(c.title)}</h1>
            <p class="lead">${esc(c.subtitle)}</p>
            <p class="meta">约 ${c.minutes} 分钟${c.capstone ? '<span>毕业任务</span>' : ''}${c.simulated ? '<span>本任务使用模拟数据</span>' : ''}</p>
          </header>${prefs.card_tips_seen ? '' : cardTip(c)}${draftNotice(c)}<div id="phase-host" role="tabpanel" aria-label="当前学习阶段">
            <div class="phase-content prose">${phaseHTML(c)}</div>
          </div>
        </article>
      </div>
    `;
  }
  return { card, phaseHTML, phaseTabs, checkHTML, solutionHTML, completionHTML, mentor };
}
