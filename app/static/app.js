// The learning page. One module on purpose: every part below reads and writes the same page
// state (course, cards, records, settings), declared once at the top. Pages render through
// pages/*.js; talking to the local service goes through api.js; typed text is protected by
// drafts.js before it is saved. Sections, in order:
//   State · Rendering and navigation · Saving · Feedback · Page decoration · Dialogs ·
//   Card actions · Events · Recovery · Start
import { $, $$, esc, icon, md, plainLead, avatar, checkSvg, btn, excerpt } from './ui.js';
import { API } from './api.js';
import { Drafts } from './drafts.js';
import { setPlatform, shortcut, fileManager, machine } from './platform.js';
import { installDesktop, syncAppearance } from './desktop.js';
import { Updates } from './updates.js';
import { defaults, applySettings, settingsHTML as settingsContent } from './settings.js';
import { welcome as renderWelcome } from './pages/welcome.js';
import { today as renderToday } from './pages/today.js';
import { route as renderRoute } from './pages/route.js';
import { lab as renderLab } from './pages/lab.js';
import { journal as renderJournal } from './pages/journal.js';
import { cardComponents } from './pages/card.js';
import { html } from './html.js';

// ─── State: course, cards, records and settings kept by this page ────────────────────────────────
let cards = [],
  course = { people: [], tracks: [], welcome: {}, lab: {} },
  people = {},
  snapshot = {};
const app = $('#app'),
  sidebar = $('#sidebar'),
  modal = $('#modal');
const phases = ['learn', 'practice', 'wrap'];
const phaseNames = (c) => (c.type === 'task' ? ['任务', '动手', '结'] : ['学', '练', '结']);
let prefs = { ...defaults, ...window.__INITIAL_SETTINGS__ };
let state = {
  view: 'welcome',
  cardId: null,
  records: {},
  track: null,
  journal: 'timeline',
  journalTrack: '',
  contact: null,
  conversationOpen: false,
  firstToday: true,
  recent: [],
  newCards: [],
  collapsed: !!prefs.sidebar_collapsed,
  completionId: null,
  completionResult: null,
};
let pageData = {},
  searchIndex = [],
  activeTransition = null,
  toastTimer,
  gPressedAt = 0,
  modalReturn = null,
  commandIndex = 0,
  commandItems = [],
  completionTimer,
  tocEntries = [],
  loadingView = 0,
  settingsQueue = Promise.resolve();
let settingsPending = 0,
  settingsVersion = 0,
  settingsFailed = false;
const uiWrites = new Set();
let connectionRecovered = false;
let saveMessage = '',
  booted = false,
  sessionKey = '',
  restoringHistory = true,
  lastHandledHash = '';
const blank = () => ({
  phase: 'learn',
  scroll: 0,
  anchor: '',
  learnDone: false,
  done: false,
  started: false,
  answers: {},
  marks: {},
  reveals: {},
  activeQ: 0,
  allQuestions: false,
  checks: {},
  resources: [],
  summary: '',
  writeup: '',
  rubric: [],
  hints: 0,
  solutionOpen: false,
  visited: false,
});
const rec = (c) => (state.records[typeof c === 'string' ? c : c?.id] ||= blank());
const current = () => cards.find((c) => c.id === state.cardId) || cards[0];
const person = (c) =>
  people[c?.from] ||
  people[course.welcome.from] ||
  course.people[0] || { name: '', initial: '', role: '' };
const countDone = () => cards.filter((c) => c.status === 'done').length;
const unlocked = (c) => !!c?.unlocked;
const phaseDone = (c, p) => !!c.phase_status?.[p];
const missing = (c) => c.missing || [];
const reduced = () =>
  prefs.motion === 'reduce' || matchMedia('(prefers-reduced-motion: reduce)').matches;
const duration = (token) =>
  reduced()
    ? 0
    : parseFloat(getComputedStyle(document.documentElement).getPropertyValue(token)) || 0;
const api = new API(acceptSnapshot, reportError);
const drafts = new Drafts((message) => {
  if (message) saveMessage = message;
  else if (saveMessage === '还有输入尚未写入磁盘，请保持窗口打开。' && drafts.protected())
    saveMessage = '';
  updateSaveNotice();
  updateComplete();
  if (booted) renderNotices();
});
const updates = new Updates(api, {
  changed: updateUpdateUI,
  flush: flushPending,
  completed: afterUpdate,
  error: reportError,
  pendingDrafts: () => drafts.entries().length,
});
function reportError(error) {
  saveMessage = error.message;
  toast(error.message);
  updateSaveNotice();
  if (error.status === 409) {
    connectionRecovered = false;
    drafts.cancelTimers();
    renderNotices();
  }
}
function acceptSnapshot(data) {
  snapshot = data;
  if (data.project_id) drafts.configure(api, data.project_id);
  setPlatform(data.desktop?.platform);
  cards = data.cards;
  course = data.course;
  people = Object.fromEntries(course.people.map((p) => [p.id, p]));
  state.newCards = data.progress?.pending_new_cards || data.new_cards || [];
  if (!state.cardId) state.cardId = cards[0]?.id;
  if (!state.track) state.track = course.tracks[0]?.id;
  if (!people[state.contact]) state.contact = course.welcome.from || course.people[0]?.id;
  for (const c of cards) {
    const existing = state.records[c.id],
      r = rec(c),
      v = c.record || {};
    r.done = c.status === 'done';
    r.started = !!v.started_at;
    r.visited = !!v.started_at;
    r.learnDone = !!v.ui?.learn_completed || !!r.learnPending;
    r.doneAt = v.done_at;
    r.resources = v.resources_done || [];
    r.rubric = v.writeup?.rubric_checked || [];
    r.marks = Object.fromEntries(
      Object.entries(v.questions || {}).map(([k, q]) => [k, q.self_mark]),
    );
    if (!existing) {
      r.phase = v.ui?.phase || 'learn';
      r.anchor = v.ui?.anchor || '';
      r.activeQ = (c.questions || []).findIndex((q) => !r.marks[q.id]);
    }
    for (const q of c.questions || [])
      if (!drafts.get(c.id, 'answer:' + q.id) || !existing)
        r.answers[q.id] = v.questions?.[q.id]?.my_answer || '';
    for (const q of c.checks || []) {
      const previous = r.checks[q.id] || {},
        attempts = v.checks?.[q.id]?.attempts || [],
        last = attempts.at(-1);
      r.checks[q.id] = {
        ...previous,
        value: last?.value ?? '',
        correct: !!last?.correct,
        attempts: attempts.length,
        wrong: last ? !last.correct : false,
      };
    }
    if (!existing || !drafts.get(c.id, 'summary')) r.summary = v.summary || '';
    if (!existing || !drafts.get(c.id, 'writeup')) r.writeup = v.writeup?.text || '';
    r.solutionOpen = r.solutionOpen && !!c.sections?.['参考解法'];
  }
  if (data.settings && !settingsPending) {
    prefs = { ...defaults, ...data.settings };
    applyPrefs();
  }
  if (booted) {
    updateSaveNotice();
    if (state.view === 'card' && current()?.unlocked) {
      updatePhaseTabs(current());
      updateComplete();
    }
  }
}
function applyPrefs() {
  applySettings(prefs);
  syncAppearance(prefs);
}
async function changeSetting(key, value) {
  prefs[key] = value;
  applyPrefs();
  const saving = { ...prefs },
    version = ++settingsVersion,
    entry = drafts.put('@settings', 'settings', JSON.stringify(saving));
  settingsPending++;
  settingsQueue = settingsQueue.then(async () => {
    try {
      const result = await api.settings(saving);
      await drafts.confirm(entry);
      settingsFailed = false;
      if (version === settingsVersion) prefs = { ...prefs, ...(result.settings || result) };
      saveMessage = '';
    } catch (error) {
      settingsFailed = true;
      reportError(error);
    } finally {
      settingsPending--;
    }
  });
  await settingsQueue;
}
function miniRoute(items, completingId = '', track) {
  const rows = (items || [])
    .map((c) => (typeof c === 'string' ? cards.find((x) => x.id === c) : c))
    .filter(Boolean);
  const trackId = track?.id || rows[0]?.track_id,
    index = Math.max(
      0,
      course.tracks.findIndex((t) => t.id === trackId),
    );
  return `<div class="mini-route" style="--mini-track:var(--track-${['a', 'b', 'c'][index % 3]})" aria-label="${esc(track?.label || rows[0]?.track_label || '')} 线进度，已完成 ${rows.filter((c) => c.status === 'done').length} / ${rows.length}">${rows
    .map(
      (c) => html`
        <span title="${esc(c.code + ' ' + c.title)}" class="dot ${c.status === 'done' ? 'done' : ''} ${pageData.today?.main?.id === c.id ? 'current' : ''} ${completingId === c.id ? 'just-filled' : ''}">
        </span>
      `,
    )
    .join('')}</div>`;
}
function context() {
  return {
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
    summaryPhases,
    messageTime,
  };
}
const card = () => cardComponents(context()).card();
const phaseHTML = (c) => cardComponents(context()).phaseHTML(c);
const phaseTabs = (c) => cardComponents(context()).phaseTabs(c);
const checkHTML = (...args) => cardComponents(context()).checkHTML(...args);
const solutionHTML = (c) => cardComponents(context()).solutionHTML(c);
const completionHTML = (...args) => cardComponents(context()).completionHTML(...args);
const mentor = (...args) => cardComponents(context()).mentor(...args);
const welcome = () => renderWelcome(context());
const today = () => renderToday({ ...context(), data: pageData.today });
const route = () => renderRoute({ ...context(), data: pageData.route });
const lab = () => renderLab({ ...context(), data: pageData.lab, mentor });
const journal = () => renderJournal({ ...context(), data: pageData.journal });
function settingsHTML() {
  return (
    settingsContent(prefs, course.content_version, { esc, btn }) +
    `<div id="updates-panel" data-render-key="${esc(updates.renderKey(course.content_version))}">${updates.html({ esc, btn }, course.content_version)}</div>`
  );
}
// Drafts this window wrote moments ago are still in their normal save window
// (500ms text debounce, 2s position throttle). Only show drafts from earlier
// windows, ones that stayed unconfirmed, or any draft once a save has failed.
// Another window's very recent draft first gets the server check in drafts.reconcile().
let draftRecheck;
function settledDrafts(card) {
  const now = Date.now(),
    waits = [],
    failing = saveMessage && saveMessage !== '正在保存…';
  const rows = drafts.entries(card).filter((d) => {
    if (failing) return true;
    const grace = d.client_id === drafts.client ? 4000 : 6500,
      age = now - Date.parse(d.at);
    if (!(age < grace)) return true;
    waits.push(grace - age);
    return false;
  });
  if (waits.length) {
    clearTimeout(draftRecheck);
    draftRecheck = setTimeout(
      () => {
        updateSaveNotice();
        if (booted) renderNotices();
      },
      Math.max(...waits) + 50,
    );
  }
  return rows;
}
// Notices count only text the user typed (CODEX_BRIEF 18.8): answers, summary, write-up, task inputs.
const typedDraft = (d) =>
  d.field === 'summary' ||
  d.field === 'writeup' ||
  d.field.startsWith('answer:') ||
  d.field.startsWith('check:');
function draftNotice(c) {
  const pending = settledDrafts(c.id).filter(typedDraft);
  return pending.length
    ? html`
      <div class="draft-notice" id="draft-notice" role="status">
        <span>这张卡有 ${pending.length} 处输入尚未确认保存。草稿会一直保留，等你选择恢复或丢弃。</span>
        <div class="actions">${btn('查看草稿', 'review-drafts', `data-card="${esc(c.id)}"`, 'quiet')}</div>
      </div>
    `
    : '<div id="draft-notice" hidden></div>';
}
function updateSaveNotice() {
  const el = $('#save-status');
  if (el)
    el.textContent =
      saveMessage ||
      (current() && settledDrafts(current().id).filter(typedDraft).length
        ? '尚未确认的输入保留为草稿。'
        : '已保存到' + machine());
  const notice = $('#draft-notice');
  if (notice && current()) {
    const active = document.activeElement;
    if (!notice.contains(active)) notice.outerHTML = draftNotice(current());
  }
}
function renderNotices() {
  let panel = $('#service-notices');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'service-notices';
    panel.className = 'service-notices';
    app.prepend(panel);
  }
  const issues = (snapshot.issues || []).map((x) =>
      typeof x === 'string' ? x : x.message || JSON.stringify(x),
    ),
    notices = [...(snapshot.notices || []), ...(drafts.notices || [])].map((x) =>
      typeof x === 'string' ? x : x.message || JSON.stringify(x),
    );
  if (snapshot.read_only)
    notices.unshift('学习记录当前只能读取。请先处理上方提示；输入仍可保留为草稿。');
  if (api.conflicted)
    notices.unshift(
      connectionRecovered
        ? '后台已重新连接。尚未确认的输入仍保留为草稿，请核对后恢复。'
        : '记录已在别处更新，已重新读取。尚未保存的草稿保留，请核对后恢复。',
    );
  panel.innerHTML = [...notices, ...issues]
    .map((x) => `<p class="service-notice" role="status">${esc(x)}</p>`)
    .join('');
  if (api.conflicted)
    panel.innerHTML += btn('继续使用已更新的记录', 'acknowledge-conflict', '', 'quiet');
  const pending = state.view === 'card' ? [] : settledDrafts().filter(typedDraft);
  if (pending.length)
    panel.innerHTML += html`
      <div class="service-notice draft-global">
        <span>有 ${pending.length} 份草稿等待确认，不会自动删除。</span>${btn('查看草稿', 'review-drafts', '', 'quiet')}</div>
    `;
  panel.hidden = !panel.innerHTML;
}
// ─── Rendering and navigation ────────────────────────────────────────────────────────────────────
function rememberNavigation(hash) {
  if (!restoringHistory && location.hash !== hash) history.pushState(null, '', hash);
  lastHandledHash = hash;
}
function render() {
  history.replaceState(
    null,
    '',
    `#${state.view}${state.view === 'card' ? '/' + state.cardId + '/' + rec(current()).phase : state.view === 'journal' ? '/' + state.journal : ''}`,
  );
  document.body.dataset.view = state.view;
  document.body.classList.toggle('focus-mode', state.view === 'card' && !state.collapsed);
  document.body.classList.toggle('sidebar-collapsed', state.collapsed && state.view !== 'card');
  document.title =
    (state.view === 'card'
      ? current()?.title
      : { welcome: '欢迎加入', today: '今天', route: '路线', lab: '课题组', journal: '记录' }[
          state.view
        ]) + ' — 学习台';
  renderSidebar();
  app.innerHTML = { welcome, today, route, card, lab, journal }[state.view]();
  if (state.view === 'today') {
    try {
      sessionStorage.setItem(sessionKey, 'seen');
    } catch {}
  }
  renderNotices();
  decorate();
  updateSaveNotice();
}
async function loadPage(view) {
  if (['today', 'route', 'lab'].includes(view)) pageData[view] = await api.get(view);
  if (view === 'journal')
    pageData.journal = await api.get(
      `journal?view=${state.journal}&track=${encodeURIComponent(state.journalTrack)}`,
    );
}
async function navigate(view) {
  if (!['welcome', 'today', 'route', 'lab', 'journal'].includes(view)) return;
  const turn = ++loadingView;
  savePosition();
  await drafts.flush();
  await api.queue;
  await loadPage(view);
  if (turn !== loadingView) return;
  state.completionId = null;
  clearTimeout(completionTimer);
  closeModal();
  rememberNavigation('#' + view + (view === 'journal' ? '/' + state.journal : ''));
  transition(
    () => {
      state.view = view;
      render();
    },
    () => {
      window.scrollTo(0, 0);
      focusPage();
      if (view === 'route')
        requestAnimationFrame(() =>
          $('.route-node.current')?.scrollIntoView({ block: 'nearest', behavior: 'instant' }),
        );
    },
  );
}
async function openCard(id, source) {
  let c = cards.find((c) => c.id === id);
  if (!c) return;
  savePosition();
  await drafts.flush();
  await api.queue;
  if (!snapshot.read_only && !api.conflicted) await api.op('seen_card', { card: id });
  c = cards.find((c) => c.id === id);
  if (!c.unlocked) {
    toast('需要先完成 ' + (c.locked_reasons || []).map((x) => x.code + ' ' + x.title).join('、'));
    if (state.view !== 'route') {
      await navigate('route');
    }
    const reason = $(`#reason-${id}`);
    if (reason) {
      reason.hidden = false;
      reason.scrollIntoView({ block: 'nearest' });
    }
    return;
  }
  if (!c.record?.started_at && !snapshot.read_only && !api.conflicted)
    await api.op('start_card', { card: id });
  const response = await api.get('card/' + encodeURIComponent(id));
  if (response.card) {
    const index = cards.findIndex((x) => x.id === id);
    cards[index] = response.card;
  }
  if (response.revision) api.revision = response.revision;
  if (source?.isConnected) {
    $$('[style*="view-transition-name"]').forEach((e) => (e.style.viewTransitionName = ''));
    source.style.viewTransitionName = 'card-title';
  }
  rememberNavigation('#card/' + id + '/' + rec(id).phase);
  state.cardId = id;
  state.completionId = null;
  clearTimeout(completionTimer);
  state.recent = [id, ...state.recent.filter((x) => x !== id)].slice(0, 8);
  const r = rec(id),
    savedAnchor = r.anchor,
    savedScroll = r.scroll;
  closeModal();
  transition(
    () => {
      state.view = 'card';
      render();
    },
    () => {
      window.scrollTo(0, savedScroll || 0);
      if (savedAnchor) requestAnimationFrame(() => anchorTo(savedAnchor, true));
      $('.article h1')?.focus({ preventScroll: true });
    },
  );
}
// ─── Saving: reading position (program state) and typed text (answers, summary, write-up) ────────
function savePosition() {
  if (state.view !== 'card' || !current()?.unlocked) return;
  const r = rec(current());
  r.scroll = window.scrollY;
  const heads = tocEntries.map((e) => document.getElementById(e.id)).filter(Boolean);
  r.anchor = heads.filter((h) => h.getBoundingClientRect().top < 150).at(-1)?.id || '';
  scheduleUI();
}
function normalizedUI(value = {}) {
  return {
    phase: phases.includes(value.phase) ? value.phase : 'learn',
    anchor: value.anchor || '',
    learn_completed: !!value.learn_completed,
  };
}
function sameUI(a, b) {
  return JSON.stringify(normalizedUI(a)) === JSON.stringify(normalizedUI(b));
}
function positionPayload(c, r) {
  return {
    card: c.id,
    phase: r.phase,
    anchor: r.anchor,
    ...(r.learnDone || r.learnPending ? { learn_completed: true } : {}),
  };
}
// Reading position and phase are program state, never drafts (CODEX_BRIEF 18.8). Each card keeps
// its own pending position, written at most every 2s, retried quietly and dropped on failure;
// the next visit records it again. Leaving or quitting writes every card that is still pending.
const pendingPositions = new Map();
function writePosition(payload) {
  const confirmed = cards.find((c) => c.id === payload.card)?.record?.ui;
  if (sameUI(confirmed, { ...confirmed, ...payload })) return Promise.resolve(true);
  if (snapshot.read_only || updates.updating) return Promise.resolve(false);
  const request = (async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (api.conflicted) return false;
      try {
        await api.op('set_ui', payload, { quiet: true });
        rec(payload.card).learnPending = false;
        return true;
      } catch {
        await new Promise((done) => setTimeout(done, 400 * (attempt + 1)));
      }
    }
    return false;
  })();
  uiWrites.add(request);
  request.finally(() => uiWrites.delete(request));
  return request;
}
function queuePosition(payload) {
  const slot = pendingPositions.get(payload.card) || { last: 0 };
  pendingPositions.set(payload.card, slot);
  clearTimeout(slot.timer);
  slot.payload = payload;
  slot.pending = true;
  const write = () => {
    slot.pending = false;
    slot.last = Date.now();
    return writePosition(slot.payload);
  };
  const wait = 2000 - (Date.now() - slot.last);
  if (wait <= 0) write();
  else slot.timer = setTimeout(write, wait);
}
function flushPositions() {
  const writes = [];
  for (const slot of pendingPositions.values())
    if (slot.pending) {
      clearTimeout(slot.timer);
      slot.pending = false;
      slot.last = Date.now();
      writes.push(writePosition(slot.payload));
    }
  return Promise.all(writes);
}
function scheduleUI(learnCompleted = false) {
  if (state.view !== 'card' || !current()?.unlocked || snapshot.read_only) return;
  const c = current(),
    r = rec(c);
  if (learnCompleted) {
    r.learnDone = true;
    r.learnPending = true;
  }
  queuePosition(positionPayload(c, r));
}
// Older versions kept unsaved positions in this browser; they are state, so they are simply dropped.
function dropLegacyPositions() {
  try {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith('learning-hub-pending-position-v2:')) keys.push(key);
    }
    keys.forEach((key) => localStorage.removeItem(key));
  } catch {}
}
// The text each edit started from, per field: what the box showed when typing began (its
// rendered value), then whatever this window last saved. The server compares it with the
// stored text, so only a real change made elsewhere in the meantime is a conflict.
const editBases = new Map();
function trackEditBase(card, field, el) {
  const key = card + '|' + field,
    known = editBases.get(key);
  if (!known || known.el !== el) editBases.set(key, { el, base: el.defaultValue });
}
// Saves of one field run one after another, so each sends the text the previous one stored.
const fieldWrites = new Map();
function writeField(card, field, text, base) {
  const key = card + '|' + field;
  const params = { card, text };
  let op;
  if (field === 'summary') op = 'save_summary';
  else if (field === 'writeup') op = 'save_writeup';
  else if (field.startsWith('answer:')) {
    op = 'save_answer';
    params.q = field.slice(7);
  } else return Promise.resolve();
  const write = async () => {
    params.base =
      base ??
      editBases.get(key)?.base ??
      recordValue(
        cards.find((c) => c.id === card),
        field,
      ) ??
      '';
    await api.op(op, params);
    const known = editBases.get(key);
    if (known) known.base = text;
    saveMessage = '';
    updateComplete();
  };
  const result = (fieldWrites.get(key) || Promise.resolve()).catch(() => {}).then(write);
  fieldWrites.set(key, result);
  return result;
}

// The toast lives in the top layer (popover) so an open dialog can never hide it;
// browsers without popover keep the old fixed-position behaviour.
// Put the reference point into the summary box; the normal input path saves it, and the user can edit.
function useTakeaway(c) {
  const input = $('#summary-input');
  input.value = c.takeaway;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  $('.takeaway-confirm').hidden = true;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}
async function dismissTip(fromButton) {
  const tip = $('#card-tip');
  if (!tip && prefs.card_tips_seen) return;
  if (tip) {
    if (fromButton) $('.phase-tabs [aria-selected="true"]')?.focus({ preventScroll: true });
    if (reduced()) tip.remove();
    else
      tip
        .animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: duration('--dur-fast'),
          easing: 'ease-out',
        })
        .finished.then(
          () => tip.remove(),
          () => tip.remove(),
        );
  }
  if (!prefs.card_tips_seen) await changeSetting('card_tips_seen', true);
}
// ─── Feedback: reconnect line, toast, page transitions ───────────────────────────────────────────
// Quiet top line while the service is unreachable or being restarted (learning-ui §14.3).
let shellReconnecting = false;
function setReconnecting(on) {
  shellReconnecting = !!on;
  showReconnecting(shellReconnecting || api.waiting > 0);
}
function showReconnecting(on) {
  let line = $('#reconnecting');
  if (!line) {
    line = document.createElement('div');
    line.id = 'reconnecting';
    line.setAttribute('role', 'status');
    line.setAttribute('aria-live', 'polite');
    line.textContent = '正在重新连接……';
    document.body.append(line);
  }
  line.classList.toggle('visible', !!on);
}
api.onConnection = (connected) => {
  showReconnecting(!connected || shellReconnecting);
  if (connected) recoverAfterReconnect().catch(reportError);
};
function toast(text) {
  const el = $('#toast');
  clearTimeout(toastTimer);
  if (el.showPopover) {
    try {
      if (el.matches(':popover-open')) el.hidePopover();
      el.showPopover();
    } catch {}
  }
  el.textContent = text;
  requestAnimationFrame(() => el.classList.add('visible'));
  toastTimer = setTimeout(() => {
    el.classList.remove('visible');
    toastTimer = setTimeout(() => {
      try {
        if (el.matches?.(':popover-open')) el.hidePopover();
      } catch {}
    }, duration('--dur-base'));
  }, 2600);
}

function transition(update, after) {
  activeTransition?.skipTransition?.();
  if (document.startViewTransition && !reduced()) {
    activeTransition = document.startViewTransition(() => {
      update();
      after?.();
    });
    activeTransition.finished.catch(() => {});
  } else {
    update();
    after?.();
  }
}

function focusPage() {
  if (!['today', 'welcome'].includes(state.view)) $('h1', app)?.focus({ preventScroll: true });
}

function localizeShortcuts() {
  for (const el of $$('kbd,[aria-label],[title]')) {
    if (el.tagName === 'KBD' && el.textContent.includes('⌘'))
      el.textContent = shortcut(el.textContent);
    for (const attr of ['aria-label', 'title'])
      if (el.getAttribute(attr)?.includes('⌘'))
        el.setAttribute(attr, shortcut(el.getAttribute(attr)));
  }
}
// ─── Page decoration: sidebar, table of contents, questions ──────────────────────────────────────
function renderSidebar() {
  const items = [
    ['today', '今天'],
    ['route', '路线'],
    ['lab', '课题组'],
    ['journal', '记录'],
  ];
  sidebar.innerHTML = html`
    <button class="brand" data-action="nav" data-view="today" aria-label="学习台，回到今天">${icon('book')}<span class="nav-label">学习台</span>
    </button>
    <nav aria-label="主导航">${items
      .map(
        ([v, t]) => html`
          <button class="nav-link ${state.view === v ? 'selected' : ''}" data-action="nav" data-view="${v}" ${state.view === v ? 'aria-current="page"' : ''} title="${t}" aria-label="${t}">
            <span class="nav-icon">${icon(v)}</span>
            <span class="nav-label">${t}</span>
          </button>
        `,
      )
      .join('')}</nav>
    <div class="sidebar-bottom">
      <button class="nav-link" data-action="modal" data-modal="command" title="搜索 ⌘K" aria-label="搜索 ⌘K">${icon('search')}<span class="nav-label">搜索 <kbd>⌘K</kbd>
        </span>
      </button>
      <button class="nav-link" data-action="modal" data-modal="settings" title="设置" aria-label="设置">${icon('settings')}<span class="nav-label">设置</span>
      </button>
      <div class="sidebar-note nav-label">一张卡，一点新发现。</div>
    </div>
  `;
}

// One marker language for self-test status: check = correct, red dot = needs another look, ring = not marked yet.
function markGlyph(mark) {
  return mark === 'correct'
    ? `<span class="mark-correct">${icon('check')}<span class="sr-only">已答对</span></span>`
    : mark === 'wrong'
      ? '<span class="mark-wrong" role="img" aria-label="还没答对"></span>'
      : '<span class="mark-open" role="img" aria-label="未标记"></span>';
}
function summaryPhases(c) {
  return `<div class="phase-summary">${phaseNames(c)
    .map(
      (name, i) =>
        `<span class="${rec(c).phase === phases[i] ? 'current' : ''}">${phaseDone(c, phases[i]) ? icon('check') : `<span class="phase-number">${i + 1}</span>`}${name}${i === 1 ? ` <span class="meta">${c.type === 'lesson' ? Object.values(rec(c).marks).filter((x) => x === 'correct').length : Object.values(rec(c).checks).filter((x) => x.correct).length}/${(c.questions || c.checks || []).length}</span>` : ''}</span>`,
    )
    .join('<span class="phase-divider" aria-hidden="true"></span>')}</div>`;
}
// Paragraphs retain their structure in full messages; only previews are flattened.

function messageTime(at) {
  if (!at || Number.isNaN(new Date(at).getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(at));
}

function decorateNavigation() {
  for (const button of $$(
    'button[data-action="nav"],button[data-action="card"],button[data-action="route-card"]',
  )) {
    const action = button.dataset.action;
    if (action === 'route-card' && !cards.find((c) => c.id === button.dataset.id)?.unlocked)
      continue;
    const link = document.createElement('a');
    for (const attr of button.attributes) link.setAttribute(attr.name, attr.value);
    link.href = action === 'nav' ? '#' + button.dataset.view : '#card/' + button.dataset.id;
    link.innerHTML = button.innerHTML;
    button.replaceWith(link);
  }
}
function decorate() {
  decorateNavigation();
  localizeShortcuts();
  $$('p code,li code,td code', app).forEach((code) => {
    if (!code.closest('button') && /[/\\]|^[ACGTU]{6,}$/i.test(code.textContent)) {
      const b = document.createElement('button');
      b.className = 'inline-copy';
      b.dataset.action = 'copy-inline';
      b.setAttribute('aria-label', '复制 ' + code.textContent);
      code.before(b);
      b.append(code);
    }
  });
  $$('a[href]', app).forEach((a) => {
    if (/^https?:/.test(a.getAttribute('href') || '')) {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.setAttribute('aria-label', a.textContent + '（在新窗口打开）');
    }
  });
  $$('pre', app).forEach((pre) => {
    if (pre.parentElement.classList.contains('code-wrap')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'code-wrap';
    pre.before(wrapper);
    wrapper.append(pre);
    const b = document.createElement('button');
    b.className = 'copy-code icon-button';
    b.dataset.action = 'copy-code';
    b.setAttribute('aria-label', '复制代码');
    b.innerHTML = icon('copy');
    wrapper.append(b);
  });
  $$('.prose table', app).forEach((t) => {
    if (t.parentElement.classList.contains('table-scroll')) return;
    const w = document.createElement('div');
    w.className = 'table-scroll';
    w.tabIndex = 0;
    w.setAttribute('aria-label', '可横向滚动的表格');
    t.before(w);
    w.append(t);
  });
  if (state.view === 'card' && current()?.unlocked) {
    const heads = $$('.phase-content h2,.phase-content h3').filter(
      (h) => !h.closest('.question,.check-card,.mentor-message'),
    );
    heads.forEach((h, i) => {
      h.id ||= `section-${i}`;
      if (h.textContent.trim() === '常见错误' && !h.closest('.mistakes')) {
        h.insertAdjacentHTML('afterbegin', icon('warn'));
        const box = document.createElement('section');
        box.className = 'mistakes';
        h.before(box);
        let n = h;
        while (n && !(n !== h && n.matches('h2,h3,section,.phase-footer'))) {
          const next = n.nextElementSibling;
          box.append(n);
          n = next;
        }
      }
    });
    renderToc();
  }
}

function phaseToc() {
  const c = current(),
    r = rec(c);
  if (r.phase === 'practice')
    return (c.questions || c.checks).map((q, i) => ({
      id: `${c.type === 'lesson' ? 'question' : 'check'}-${q.id}`,
      label: `第 ${i + 1} 题`,
      status:
        c.type === 'lesson'
          ? r.marks[q.id] || 'unanswered'
          : r.checks[q.id]?.correct
            ? 'correct'
            : r.checks[q.id]?.wrong
              ? 'wrong'
              : 'unanswered',
    }));
  if (r.phase === 'wrap')
    return [
      ...(c.writeup ? [{ id: 'writeup', label: '文字结论' }] : []),
      { id: 'summary', label: '总结' },
      { id: 'complete-actions', label: '完成' },
    ];
  const heads = $$('.phase-content h2,.phase-content h3').filter(
    (h) => !h.closest('#resources,#how,#extend,.mentor-message,.files,.question,.check-card'),
  );
  return [
    ...heads.map((h) => ({ id: h.id, label: h.textContent })),
    ...(c.type === 'lesson'
      ? [
          { id: 'resources', label: '学习资源' },
          { id: 'how', label: '怎么学' },
          ...(c.sections['延伸'] ? [{ id: 'extend', label: '延伸' }] : []),
        ]
      : c.data?.length
        ? [{ id: 'data-files', label: '数据文件' }]
        : []),
  ];
}

function renderToc() {
  if (state.view !== 'card') return;
  tocEntries = phaseToc();
  const statusText = { correct: '已答对', wrong: '没答对', unanswered: '未作答' },
    statusGlyph = { correct: '✓', wrong: '●', unanswered: '○' };
  const iconHTML = (e) =>
    e.status
      ? `<span class="toc-status ${e.status}">${markGlyph(e.status === 'unanswered' ? '' : e.status)}</span>`
      : '';
  $('.toc').innerHTML =
    `<p class="meta">本页内容</p>${tocEntries.map((e) => `<button class="toc-link" data-action="anchor" data-anchor="${e.id}">${iconHTML(e)}${esc(e.label)}</button>`).join('')}`;
  $('.toc-mobile').innerHTML = html`
    <label>
      <span>本页内容</span>
      <select id="toc-select" name="section" autocomplete="off">
        <option value="">选择小节</option>${tocEntries.map((e) => `<option value="${e.id}">${e.status ? statusGlyph[e.status] + ' ' : ''}${esc(e.label)}${e.status ? '（' + statusText[e.status] + '）' : ''}</option>`).join('')}</select>
    </label>
  `;
  updateScroll();
}

function syncQuestions() {
  const c = current(),
    r = rec(c);
  c.questions.forEach((q, i) => {
    const el = $(`#question-${q.id}`);
    const open = r.allQuestions || r.activeQ === i;
    el.classList.toggle('active', open);
    el.classList.toggle('marked', !!r.marks[q.id]);
    const summary = $('.question-summary', el);
    summary.hidden = open;
    summary.innerHTML = html`
      <span>${i + 1}</span>
      <span>${esc(excerpt(plainLead(q.prompt), 20))}</span>${markGlyph(r.marks[q.id])}`;
    $('.question-body', el).classList.toggle('open', open);
    $('.question-body', el).inert = !open;
  });
  $('.quiz-progress').textContent =
    `已答对 ${Object.values(r.marks).filter((x) => x === 'correct').length} / ${c.questions.length}`;
  $('.quiz-finish').hidden = !c.questions.every((q) => r.marks[q.id]);
  updatePhaseTabs(c);
  renderToc();
}

function revealAnswer(qid) {
  const c = current(),
    r = rec(c);
  r.reveals[qid] = true;
  const el = $(`#question-${qid} .answer-reveal`);
  el.inert = false;
  el.classList.add('open');
  setTimeout(() => {
    if (el.isConnected)
      el.scrollIntoView({ behavior: reduced() ? 'instant' : 'smooth', block: 'nearest' });
  }, duration('--dur-base'));
}

function updatePhaseTabs(c) {
  const tabs = $('.phase-tabs');
  if (!tabs) return;
  tabs.style.setProperty('--phase', phases.indexOf(rec(c).phase));
  const holder = document.createElement('div');
  holder.innerHTML = phaseTabs(c);
  const fresh = $$('button', holder);
  $$('button', tabs).forEach((b, i) => {
    b.innerHTML = fresh[i].innerHTML;
    b.setAttribute('aria-selected', fresh[i].getAttribute('aria-selected'));
    b.tabIndex = rec(c).phase === phases[i] ? 0 : -1;
  });
}

// ─── Dialogs: settings, command palette (⌘K), draft review ───────────────────────────────────────
function modalFrame(title, body, footer = '') {
  return html`
    <div class="modal-card">
      <header class="modal-header">
        <h2 id="modal-title">${title}</h2>
        <button class="icon-button" data-action="close-modal" aria-label="关闭">${icon('close')}</button>
      </header>
      <div class="modal-body">${body}</div>${footer ? `<footer class="modal-footer">${footer}</footer>` : ''}</div>
  `;
}

function openModal(name) {
  if (!modal.open) modalReturn = document.activeElement;
  modal.dataset.kind = name;
  modal.dataset.from = '';
  commandIndex = 0;
  if (name === 'settings') modal.innerHTML = modalFrame('让这里像你的书桌。', settingsHTML());
  if (name === 'help')
    modal.innerHTML = modalFrame(
      '卡在哪里？',
      html`
        <p class="meta">整理一下问题，再复制给你常用的 AI。</p>
        <label class="field">
          <span>卡在哪里？</span>
          <textarea id="help-problem" name="problem" rows="3" autocomplete="off" placeholder="哪一步没想明白？"></textarea>
        </label>
        <label class="field">
          <span>代码或报错 <span class="meta">可选</span>
          </span>
          <textarea id="help-code" name="code" rows="5" autocomplete="off" spellcheck="false"></textarea>
        </label>
      `,
      btn('复制给 AI', 'copy-help', '', 'primary'),
    );
  if (name === 'shortcuts')
    modal.innerHTML = modalFrame(
      '用键盘，也很顺手。',
      html`
        <dl class="shortcut-list">${shortcuts
          .map(
            ([key, t]) => html`
              <div>
                <dt>
                  <kbd>${esc(shortcut(key))}</kbd>
                </dt>
                <dd>${t}</dd>
              </div>
            `,
          )
          .join('')}</dl>
        <p class="meta">输入文字时，单字母快捷键不会生效。</p>
      `,
    );
  if (name === 'command')
    modal.innerHTML = modalFrame(
      '想去哪里？',
      html`
        <label class="command-search">${icon('search')}<input class="command-input" id="command-input" name="search" type="search" autocomplete="off" placeholder="搜索卡片、讲解小节或操作…" aria-label="搜索卡片和操作" role="combobox" aria-controls="command-results" aria-expanded="true">
        </label>
        <div id="command-results" role="listbox" aria-label="搜索结果"></div>
      `,
      html`
        <span class="meta">
          <kbd>↑</kbd> <kbd>↓</kbd> 选择　<kbd>Enter</kbd> 打开　<kbd>Esc</kbd> 关闭</span>
      `,
    );
  if (!modal.open) modal.showModal();
  localizeShortcuts();
  focusModal(name);
  if (name === 'command') {
    renderCommands('');
    api
      .get('search-index')
      .then((data) => {
        searchIndex = data.entries || [];
        if (modal.open && modal.dataset.kind === 'command')
          renderCommands($('#command-input').value);
      })
      .catch(reportError);
  }
}
// Focus synchronously so a fast first keystroke after ⌘K is not lost. Settings start on the
// current choice; read-only dialogs focus their title, so no focus ring appears on the close button.
function focusModal(name) {
  const target =
    name === 'command'
      ? $('#command-input')
      : name === 'help'
        ? $('#help-problem')
        : name === 'settings'
          ? $('[aria-pressed="true"]', modal)
          : $('#modal-title');
  if (target === $('#modal-title')) target.tabIndex = -1;
  target?.focus({ preventScroll: true });
}

function closeModal() {
  if (!modal.open) return;
  modal.close();
  if (modalReturn?.isConnected) modalReturn.focus({ preventScroll: true });
}

function renderCommands(query) {
  commandItems = searchEntries(query);
  commandIndex = Math.min(commandIndex, commandItems.length - 1);
  if (commandIndex < 0) commandIndex = 0;
  let last = '';
  $('#command-results').innerHTML = commandItems.length
    ? commandItems
        .map((e, i) => {
          const header = e.group !== last ? `<p class="command-group meta">${e.group}</p>` : '';
          last = e.group;
          return html`${header}<button id="command-${i}" class="command-result ${i === commandIndex ? 'selected' : ''}" role="option" tabindex="-1" aria-selected="${i === commandIndex}" data-action="command-run" data-index="${i}">
              <span>${esc(e.label)}</span>
              <span class="meta">${e.kind === 'card' ? esc(e.detail) : '打开'}</span>
            </button>`;
        })
        .join('')
    : '<p class="empty-state">没有找到。试试卡片编号或标题。</p>';
  $('#command-input').setAttribute(
    'aria-activedescendant',
    commandItems.length ? `command-${commandIndex}` : '',
  );
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制');
    if (button) {
      const old = button.innerHTML;
      button.innerHTML = icon('check');
      setTimeout(() => {
        if (button.isConnected) button.innerHTML = old;
      }, 1500);
    }
    return true;
  } catch {
    if (!modal.open) modalReturn = document.activeElement;
    modal.dataset.kind = 'copy';
    modal.innerHTML = modalFrame(
      '复制这段文字',
      html`
        <p class="meta">浏览器没有允许自动复制。选中下面的文字，按 ${shortcut('⌘C')} 复制。</p>
        <textarea class="copy-fallback" name="copy-text" aria-label="待复制文字" rows="10" readonly>${esc(text)}</textarea>
      `,
    );
    if (!modal.open) modal.showModal();
    $('.copy-fallback', modal).select();
    return false;
  }
}

// ─── Card actions: phases, self-test, task checks, completion ────────────────────────────────────
function anchorTo(id, instant = false) {
  let wait = false;
  if (state.view === 'card' && rec(current()).phase === 'practice') {
    const c = current(),
      r = rec(c);
    if (c.type === 'lesson') {
      const i = c.questions.findIndex((q) => `question-${q.id}` === id);
      if (i >= 0) {
        r.activeQ = i;
        syncQuestions();
        wait = true;
      }
    } else {
      const q = c.checks.find((q) => `check-${q.id}` === id);
      if (q && r.checks[q.id]?.correct && !r.checks[q.id].editing) {
        r.checks[q.id].editing = true;
        $(`#${id}`).outerHTML = checkHTML(c, q, c.checks.indexOf(q));
        decorate();
      }
    }
  }
  const jump = () => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: instant || reduced() ? 'instant' : 'smooth', block: 'start' });
    if (!el.matches('input,textarea,select,button,a')) el.tabIndex = -1;
    el.focus({ preventScroll: true });
  };
  if (wait) setTimeout(jump, duration('--dur-base'));
  else jump();
}
async function switchPhase(phase, { instant = false } = {}) {
  if (state.view !== 'card' || !phases.includes(phase)) return;
  const c = current(),
    r = rec(c),
    previous = r.phase;
  if (previous === phase) return;
  await drafts.flush();
  await api.queue;
  const old = $('.phase-content'),
    ghost = old.cloneNode(true),
    host = $('#phase-host'),
    dir = phases.indexOf(phase) > phases.indexOf(previous) ? 1 : -1;
  r.phase = phase;
  r.scroll = 0;
  r.anchor = '';
  state.completionId = null;
  $('.article').dataset.phase = phase;
  dismissTip(false).catch(reportError);
  rememberNavigation(`#card/${c.id}/${phase}`);
  history.replaceState(null, '', `#card/${c.id}/${phase}`);
  const replacement = document.createElement('div');
  replacement.className = 'phase-content prose';
  replacement.innerHTML = phaseHTML(c);
  old.replaceWith(replacement);
  updatePhaseTabs(c);
  decorate();
  updateSaveNotice();
  window.scrollTo({ top: 0, behavior: 'instant' });
  scheduleUI();
  if (!reduced() && !instant) {
    ghost.classList.add('stage-ghost');
    ghost.inert = true;
    ghost.setAttribute('aria-hidden', 'true');
    ghost.removeAttribute('id');
    $$('[id]', ghost).forEach((e) => e.removeAttribute('id'));
    host.append(ghost);
    const opts = {
      duration: duration('--dur-base'),
      easing: getComputedStyle(document.documentElement).getPropertyValue('--ease-in-out').trim(),
    };
    ghost
      .animate(
        [
          { opacity: 1, transform: 'translateX(0)' },
          { opacity: 0, transform: `translateX(${-24 * dir}px)` },
        ],
        opts,
      )
      .finished.finally(() => ghost.remove());
    replacement.animate(
      [
        { opacity: 0, transform: `translateX(${24 * dir}px)` },
        { opacity: 1, transform: 'translateX(0)' },
      ],
      opts,
    );
  }
  $('.phase-tabs [aria-selected="true"]')?.focus({ preventScroll: true });
}
async function markQuestion(qid, mark) {
  const c = current(),
    r = rec(c);
  await drafts.flush();
  await api.op('mark_question', { card: c.id, q: qid, mark });
  if (state.view !== 'card' || state.cardId !== c.id) return;
  const idx = c.questions.findIndex((q) => q.id === qid);
  r.activeQ = c.questions.findIndex((q, i) => i > idx && !r.marks[q.id]);
  if (r.activeQ < 0) r.activeQ = c.questions.findIndex((q) => !r.marks[q.id]);
  syncQuestions();
  const target =
    r.activeQ >= 0
      ? $(`#question-${c.questions[r.activeQ].id} textarea`)
      : $('.quiz-finish button');
  setTimeout(() => {
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ behavior: reduced() ? 'instant' : 'smooth', block: 'center' });
  }, duration('--dur-base'));
}
async function checkAnswer(qid) {
  const c = current(),
    q = c.checks.find((q) => q.id === qid),
    input = $(`[data-check="${qid}"]`),
    value = input?.value || rec(c).checks[qid]?.value || '';
  if (!value.trim()) {
    input?.focus();
    toast('先写下你的答案，再检查。');
    return;
  }
  const b = $(`#check-${qid} [data-action="check"]`);
  if (b) b.disabled = true;
  try {
    await drafts.flush();
    await api.op('check', { card: c.id, c: qid, value });
    await drafts.remove(c.id, 'check:' + qid, value);
    const restored = drafts.restoredChecks?.get(c.id + ':' + qid);
    if (restored?.text === value) {
      await drafts.confirm(restored);
      drafts.restoredChecks.delete(c.id + ':' + qid);
    }
    const fresh = cards.find((x) => x.id === c.id);
    if (state.cardId !== c.id || rec(c).phase !== 'practice') return;
    rec(c).checks[qid].editing = false;
    const index = fresh.checks.findIndex((x) => x.id === qid);
    $('#check-' + qid).outerHTML = checkHTML(fresh, fresh.checks[index], index);
    $('#solution').outerHTML = solutionHTML(fresh);
    updatePhaseTabs(fresh);
    decorate();
    if (rec(c).checks[qid].correct) {
      const next = fresh.checks.find((x) => !rec(c).checks[x.id]?.correct);
      if (next) $(`[data-check="${next.id}"]`)?.focus({ preventScroll: true });
      else $('.phase-footer .primary')?.focus({ preventScroll: true });
    } else $(`#check-${qid} [data-action="check"]`)?.focus({ preventScroll: true });
    updateSaveNotice();
  } finally {
    if (b?.isConnected) b.disabled = false;
  }
}
function updateComplete() {
  if (state.view !== 'card' || !current()) return;
  const c = current(),
    button = $('[data-action="complete"]');
  if (!button) return;
  button.disabled =
    c.status === 'done' ||
    missing(c).length > 0 ||
    snapshot.read_only ||
    drafts.entries(c.id).some((d) => d.field === 'summary' || d.field === 'writeup');
  const reasons = $('#missing-reasons');
  if (reasons)
    reasons.innerHTML =
      c.status === 'done'
        ? ''
        : missing(c)
            .map(
              (m) =>
                `<button class="link-button" data-action="missing" data-phase="${esc(m.phase || 'wrap')}" data-anchor="${esc(m.anchor || '')}">${esc(m.label)}</button>`,
            )
            .join('');
}
async function complete() {
  const id = current().id;
  if (!(await drafts.flush()))
    throw new Error('还有输入尚未保存，请先保存或恢复草稿，再完成这张卡。');
  const b = $('[data-action="complete"]');
  b.disabled = true;
  let response;
  try {
    response = await api.op('complete', { card: id });
  } catch (error) {
    updateComplete();
    throw error;
  }
  const c = current();
  state.completionId = id;
  state.completionResult = response.result;
  b.classList.add('completing');
  b.innerHTML = `<span class="complete-old">完成这张卡</span>${checkSvg}`;
  updatePhaseTabs(c);
  $('#missing-reasons').innerHTML = '';
  $('#completion-slot').innerHTML = completionHTML(c, true);
  decorate();
  completionTimer = setTimeout(
    () => {
      if (state.view === 'card' && state.cardId === id && rec(c).phase === 'wrap') {
        b.classList.remove('completing');
        b.classList.add('is-done');
        b.innerHTML = `${icon('check')} 已完成`;
        $('.completion-card')?.scrollIntoView({
          behavior: reduced() ? 'instant' : 'smooth',
          block: 'center',
        });
        $('.completion-next .primary')?.focus({ preventScroll: true });
      }
    },
    reduced() ? 0 : 1200,
  );
}
function updateScroll() {
  if (state.view !== 'card' || !current()?.unlocked) return;
  const total = Math.max(1, document.documentElement.scrollHeight - innerHeight),
    progress = Math.min(1, Math.max(0, scrollY / total));
  $('.reading-progress')?.style.setProperty('transform', `scaleX(${progress})`);
  $('.card-topbar')?.classList.toggle('scrolled', scrollY > 4);
  const headings = tocEntries.map((e) => document.getElementById(e.id)).filter(Boolean),
    heading = headings.filter((e) => e.getBoundingClientRect().top < 180).at(-1) || headings[0];
  $$('.toc-link').forEach((b) => {
    const selected = b.dataset.anchor === heading?.id;
    b.classList.toggle('current', selected);
    if (selected) b.setAttribute('aria-current', 'location');
    else b.removeAttribute('aria-current');
  });
  const r = rec(current());
  r.scroll = scrollY;
  r.anchor = scrollY > 0 ? heading?.id || '' : '';
  if (
    scrollY + innerHeight >= document.documentElement.scrollHeight - 12 &&
    r.phase === 'learn' &&
    !r.learnDone
  )
    scheduleUI(true);
}
const shortcuts = [
  ['⌘K', '搜索卡片和操作'],
  ['G 然后 T / R / L / J', '今天 / 路线 / 课题组 / 记录'],
  ['← / →', '切换卡片阶段'],
  ['⌘Enter', '对照答案或检查答案'],
  ['Y / N', '我答对了 / 还没答对'],
  ['⌘\\', '展开或收起侧栏'],
  ['Esc', '关闭浮层'],
  ['?', '显示快捷键一览'],
];
function searchEntries(query) {
  const q = query.trim().toLocaleLowerCase();
  const entries = (searchIndex.length ? searchIndex : cards).map((c) => ({
    group: state.recent.includes(c.id) ? '最近打开' : '卡片',
    label: `${c.code} ${c.title}`,
    detail: c.subtitle,
    id: c.id,
    kind: 'card',
    search: `${c.code} ${c.id} ${c.title} ${c.subtitle} ${(c.headings || []).join(' ')}`,
  }));
  const recency = (e) => (state.recent.includes(e.id) ? state.recent.indexOf(e.id) : 99);
  // With a query, rank where it matched: code, then title, then subtitle, then section headings.
  const score = (e) => {
    if (!q) return 0;
    const c = searchIndex.find((x) => x.id === e.id) || cards.find((x) => x.id === e.id) || {};
    const code = String(c.code || '').toLocaleLowerCase(),
      title = String(c.title || '').toLocaleLowerCase();
    return code === q
      ? 0
      : code.startsWith(q)
        ? 1
        : title.includes(q)
          ? 2
          : String(c.subtitle || '')
                .toLocaleLowerCase()
                .includes(q)
            ? 3
            : 4;
  };
  entries.sort((a, b) => score(a) - score(b) || recency(a) - recency(b));
  if (q)
    entries.forEach((e) => {
      e.group = '卡片';
    });
  const actions = [
    ['今天', 'nav', 'today'],
    ['路线', 'nav', 'route'],
    ['课题组', 'nav', 'lab'],
    ['记录', 'nav', 'journal'],
    ['打开设置', 'modal', 'settings'],
    ['切换到纸页主题', 'theme', 'paper'],
    ['切换到清透主题', 'theme', 'clear'],
    ['切换到浅色', 'mode', 'light'],
    ['切换到深色', 'mode', 'dark'],
    ['回看没答对的题', 'review', ''],
    ['导出学习记录', 'export', ''],
    ['快捷键一览', 'modal', 'shortcuts'],
  ].map(([label, kind, id]) => ({
    group: '页面与操作',
    label,
    detail: '',
    kind,
    id,
    search: label,
  }));
  return [...entries, ...actions].filter((e) => !q || e.search.toLocaleLowerCase().includes(q));
}
async function runCommand(index) {
  const e = commandItems[index];
  if (!e) return;
  closeModal();
  if (e.kind === 'card') await openCard(e.id);
  else if (e.kind === 'nav') await navigate(e.id);
  else if (e.kind === 'modal') openModal(e.id);
  else if (e.kind === 'review') {
    state.journal = 'review';
    await navigate('journal');
  } else if (['theme', 'mode'].includes(e.kind)) {
    await changeSetting(e.kind, e.id);
    toast('外观已切换');
  } else await exportText('record');
}
async function exportText(kind, extra = {}) {
  if (!(await drafts.flush()))
    throw new Error('还有输入尚未保存，请先恢复草稿或重试，再复制或导出。');
  const data = await api.post('export', { kind, ...extra });
  const copied = await copyText(data.text);
  if (kind === 'record' && data.path)
    toast((copied ? '已复制，并保存到 ' : '文件已保存，请手动复制。保存位置：') + data.path);
}
let reviewedDrafts = [];
function draftLabel(d) {
  return d.field === 'settings'
    ? '外观设置'
    : d.field === 'position'
      ? '阅读位置'
      : d.field === 'summary'
        ? '一句话总结'
        : d.field === 'writeup'
          ? '文字结论'
          : d.field.startsWith('answer:')
            ? '自测题 ' + d.field.slice(7)
            : '任务答案 ' + d.field.slice(6);
}
function draftText(d) {
  if (d.field === 'position') {
    try {
      const value = JSON.parse(d.text);
      return (
        '阶段：' +
        ({ learn: '学', practice: '练', wrap: '结' }[value.phase] || value.phase) +
        (value.anchor ? '；上次的小节位置也会恢复。' : '；从阶段开头继续。')
      );
    } catch {
      return d.text;
    }
  }
  if (d.field === 'settings') {
    try {
      const value = JSON.parse(d.text),
        labels = {
          theme: '主题',
          mode: '外观',
          reading: '正文字体',
          size: '字号',
          motion: '减弱动态效果',
          sidebar_collapsed: '侧栏',
          require_summary: '完成时要求写总结',
        },
        values = {
          paper: '纸页',
          clear: '清透',
          auto: '跟随系统',
          light: '浅色',
          dark: '深色',
          sans: '黑体',
          serif: '宋体',
          m: '标准',
          l: '大',
          system: '跟随系统',
          reduce: '开启',
          true: '收起',
          false: '展开',
        };
      return Object.entries(value)
        .filter(([key]) => labels[key])
        .map(
          ([key, v]) =>
            labels[key] +
            '：' +
            (key === 'require_summary' ? (v ? '开启' : '关闭') : values[String(v)] || v),
        )
        .join('\n');
    } catch {
      return d.text;
    }
  }
  return d.text;
}
function reviewDrafts(card) {
  if (!modal.open) modalReturn = document.activeElement;
  modal.dataset.kind = 'drafts';
  modal.dataset.card = card || '';
  reviewedDrafts = drafts.entries(card);
  modal.innerHTML = modalFrame(
    '等待你确认的草稿',
    `<p class="meta">未确认的草稿会一直保留。逐条恢复或丢弃；任务答案恢复后仍需点击“检查答案”。</p>${
      reviewedDrafts.length
        ? reviewedDrafts
            .map((d, index) => {
              const c = cards.find((c) => c.id === d.card);
              return html`
                <section class="draft-review">
                  <h3>${esc(c ? c.code + ' ' + c.title : d.card === '@settings' ? '学习台设置' : d.card)}</h3>
                  <label class="field">
                    <span>${esc(draftLabel(d))}</span>
                    <textarea name="draft-${index}" autocomplete="off" readonly rows="3">${esc(draftText(d))}</textarea>
                  </label>
                  <p class="meta">${esc(messageTime(d.at))}${d.client_id !== drafts.client ? '　来自此前打开的窗口' : ''}</p>
                  <div class="actions">${btn('恢复', 'restore-draft', `data-index="${index}" ${snapshot.read_only || (d.card !== '@settings' && !c?.unlocked) ? 'disabled' : ''}`, 'secondary')}${btn('丢弃', 'discard-draft', `data-index="${index}"`, 'quiet')}</div>
                </section>
              `;
            })
            .join('')
        : '<p>这里的草稿已经全部处理好了。</p>'
    }`,
    modal.dataset.from === 'settings'
      ? btn(
          '回到设置',
          'modal',
          'data-modal="settings"',
          reviewedDrafts.length ? 'quiet' : 'primary',
        )
      : '',
  );
  if (!modal.open) modal.showModal();
}
async function restoreDraft(index) {
  const d = reviewedDrafts[index];
  if (!d) return;
  api.conflicted = false;
  connectionRecovered = false;
  if (d.field === 'settings') {
    const value = JSON.parse(d.text);
    await api.settings(value);
    prefs = { ...defaults, ...value };
    applyPrefs();
    await drafts.confirm(d);
  } else {
    const c = cards.find((c) => c.id === d.card);
    if (!c?.unlocked) throw new Error('这张卡当前不能继续学习，草稿已保留。');
    const r = rec(c);
    if (d.field.startsWith('check:')) {
      const q = d.field.slice(6);
      (r.checks[q] ||= {}).draft = d.text;
      r.checks[q].editing = true;
      r.phase = 'practice';
      drafts.restoredChecks ||= new Map();
      drafts.restoredChecks.set(c.id + ':' + q, d);
    } else {
      // Restoring is an explicit choice to replace what the record holds now.
      await writeField(c.id, d.field, d.text, recordValue(c, d.field) ?? '');
      editBases.delete(c.id + '|' + d.field);
      await drafts.confirm(d);
      if (d.field === 'summary') r.summary = d.text;
      else if (d.field === 'writeup') r.writeup = d.text;
      else r.answers[d.field.slice(7)] = d.text;
    }
    closeModal();
    if (state.view !== 'card' || state.cardId !== c.id) await openCard(c.id);
    else render();
  }
  saveMessage = '';
  updateSaveNotice();
  renderNotices();
  toast(
    d.field.startsWith('check:')
      ? '答案已填回，点击“检查答案”后才会正式保存。'
      : '草稿已恢复并保存',
  );
  if (d.field === 'settings') reviewDrafts(modal.dataset.card || undefined);
}
async function discardDraft(index) {
  const row = reviewedDrafts[index];
  if (!row) return;
  await drafts.discard(row);
  reviewDrafts(modal.dataset.card || undefined);
  renderNotices();
  updateSaveNotice();
}
function playVideo(button) {
  let url;
  try {
    url = new URL(button.dataset.url);
  } catch {
    toast('视频地址无法识别，请用“打开”链接。');
    return;
  }
  let src;
  if (button.dataset.embed === 'youtube') {
    const id =
      url.hostname === 'youtu.be'
        ? url.pathname.slice(1)
        : url.pathname.startsWith('/embed/')
          ? url.pathname.split('/')[2]
          : url.searchParams.get('v');
    if (id && /^[A-Za-z0-9_-]{11}$/.test(id)) src = 'https://www.youtube-nocookie.com/embed/' + id;
  } else if (button.dataset.embed === 'bilibili') {
    const id = url.pathname.match(/\/(BV[A-Za-z0-9]+|av\d+)/)?.[1];
    if (id)
      src =
        'https://player.bilibili.com/player.html?' +
        (id.startsWith('BV') ? 'bvid=' + id : 'aid=' + id.slice(2)) +
        '&autoplay=0';
  }
  if (!src) {
    toast('视频地址无法识别，请用“打开”链接。');
    return;
  }
  const frame = document.createElement('iframe');
  frame.className = 'video-frame';
  frame.src = src;
  frame.title = button.getAttribute('aria-label');
  frame.allow = 'fullscreen; picture-in-picture';
  frame.referrerPolicy = 'strict-origin-when-cross-origin';
  frame.allowFullscreen = true;
  button.replaceWith(frame);
}
// ─── Events: clicks, typing, keyboard, window lifecycle ──────────────────────────────────────────
async function handleClick(e) {
  const b = e.target.closest('[data-action]');
  if (!b || b.disabled) return;
  if (b.tagName === 'A') {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
  }
  const action = b.dataset.action,
    c = current(),
    r = c ? rec(c) : null;
  switch (action) {
    case 'nav':
      await navigate(b.dataset.view);
      break;
    case 'start-welcome':
      if (!snapshot.read_only && !api.conflicted) await api.op('welcome_done');
      if (cards[0]) await openCard(cards[0].id);
      break;
    case 'card':
    case 'hero-open':
      await openCard(b.dataset.id, b.closest('.hero-card')?.querySelector('.card-title'));
      break;
    case 'route-card': {
      const target = cards.find((x) => x.id === b.dataset.id);
      if (!unlocked(target)) {
        if (!snapshot.read_only && !api.conflicted) await api.op('seen_card', { card: target.id });
        const reason = $(`#reason-${target.id}`);
        reason.hidden = !reason.hidden;
        $$('.badge', b)
          .filter((el) => el.textContent === '新')
          .forEach((el) => el.remove());
      } else await openCard(target.id, $('.node-title', b));
      break;
    }
    case 'phase':
      await switchPhase(b.dataset.phase);
      break;
    case 'next-phase':
      if (r.phase === 'learn') {
        r.learnDone = true;
        scheduleUI(true);
      }
      await switchPhase(phases[Math.min(2, phases.indexOf(r.phase) + 1)]);
      break;
    case 'modal':
      openModal(b.dataset.modal);
      break;
    case 'close-modal':
      closeModal();
      break;
    case 'setting': {
      const key = b.dataset.key,
        value = b.dataset.bool !== undefined ? b.dataset.value === 'true' : b.dataset.value;
      $$('[data-action="setting"]', modal)
        .filter((x) => x.dataset.key === key)
        .forEach((x) => x.setAttribute('aria-pressed', x.dataset.value === b.dataset.value));
      await changeSetting(key, value);
      if (key === 'require_summary') {
        await api.bootstrap();
        if (state.view === 'card' && current()?.unlocked && rec(current()).phase === 'wrap') {
          const host = $('.phase-content');
          if (host) {
            host.innerHTML = phaseHTML(current());
            decorate();
            updateSaveNotice();
            updateComplete();
          }
        }
      }
      break;
    }
    case 'track':
      state.track = b.dataset.track;
      render();
      $(`.track-tabs [data-track="${state.track}"]`).focus();
      break;
    case 'anchor':
      anchorTo(b.dataset.anchor);
      break;
    case 'reveal-answer':
      revealAnswer(b.dataset.qid);
      break;
    case 'mark-question':
      await markQuestion(b.dataset.qid, b.dataset.mark);
      break;
    case 'question-open':
      r.activeQ = Number(b.dataset.index);
      syncQuestions();
      $(`#question-${c.questions[r.activeQ].id} textarea`)?.focus({ preventScroll: true });
      break;
    case 'check':
      await checkAnswer(b.dataset.checkid);
      break;
    case 'reopen-check': {
      const q = c.checks.find((q) => q.id === b.dataset.checkid);
      r.checks[q.id].editing = true;
      r.checks[q.id].wrong = false;
      $(`#check-${q.id}`).outerHTML = checkHTML(c, q, c.checks.indexOf(q));
      updatePhaseTabs(c);
      $(`[data-check="${q.id}"]`).focus();
      break;
    }
    case 'hint': {
      const i = Number(b.dataset.index),
        open = b.getAttribute('aria-expanded') !== 'true';
      r.hints = Math.max(r.hints, i + 1);
      b.setAttribute('aria-expanded', String(open));
      b.parentElement.querySelector('.reveal').classList.toggle('open', open);
      $$('.hint-toggle').forEach((x, j) => {
        x.disabled = j > r.hints;
        x.querySelector('.hint-lock')?.toggleAttribute('hidden', !x.disabled);
      });
      break;
    }
    case 'solution':
      if (!r.solutionOpen) await api.op('view_solution', { card: c.id });
      r.solutionOpen = !r.solutionOpen;
      $('#solution').outerHTML = solutionHTML(current());
      $('#solution button').focus({ preventScroll: true });
      break;
    case 'missing': {
      if (b.dataset.phase && b.dataset.phase !== r.phase) await switchPhase(b.dataset.phase);
      let anchor = b.dataset.anchor;
      if (anchor?.startsWith('q')) anchor = 'question-' + anchor;
      if (anchor?.startsWith('c') && /^c\d+$/.test(anchor)) anchor = 'check-' + anchor;
      if (anchor)
        anchorTo(
          anchor === 'summary' ? 'summary-input' : anchor === 'writeup' ? 'writeup-input' : anchor,
        );
      break;
    }
    case 'complete':
      await complete();
      break;
    case 'dismiss-tip':
      await dismissTip(true);
      break;
    case 'toggle-takeaway': {
      const panel = $('#takeaway-panel'),
        open = b.getAttribute('aria-expanded') !== 'true';
      b.setAttribute('aria-expanded', String(open));
      panel.classList.toggle('open', open);
      panel.inert = !open;
      break;
    }
    case 'use-takeaway': {
      const input = $('#summary-input');
      if (input.value.trim() && input.value.trim() !== c.takeaway) {
        $('.takeaway-confirm').hidden = false;
        $('.takeaway-confirm .quiet').focus();
        break;
      }
      useTakeaway(c);
      break;
    }
    case 'confirm-takeaway':
      useTakeaway(c);
      break;
    case 'cancel-takeaway':
      $('.takeaway-confirm').hidden = true;
      $('[data-action="use-takeaway"]').focus();
      break;
    case 'write-summary': {
      const id = b.dataset.id;
      {
        const target = rec(id);
        target.phase = 'wrap';
        target.anchor = 'summary';
        target.scroll = 0;
      }
      await openCard(id);
      setTimeout(
        () => $('#summary-input')?.focus({ preventScroll: true }),
        duration('--dur-base') + 50,
      );
      break;
    }
    case 'review-item': {
      const id = b.dataset.id;
      {
        const target = rec(id);
        target.phase = 'practice';
        target.anchor = b.dataset.anchor;
        target.scroll = 0;
      }
      await openCard(id);
      break;
    }
    case 'contact':
      state.contact = b.dataset.person;
      state.conversationOpen = true;
      app.innerHTML = lab();
      renderNotices();
      decorate();
      $('.conversation-title h2')?.setAttribute('tabindex', '-1');
      $('.conversation-title h2')?.focus({ preventScroll: true });
      break;
    case 'contacts-back':
      state.conversationOpen = false;
      render();
      $(`.contact[data-person="${state.contact}"]`).focus();
      break;
    case 'journal-tab':
      state.journal = b.dataset.tab;
      await navigate('journal');
      $(`[data-tab="${state.journal}"]`).focus();
      break;
    case 'notes':
      state.journal = 'notes';
      await navigate('journal');
      break;
    case 'command-run':
      await runCommand(Number(b.dataset.index));
      break;
    case 'copy-inline':
      await copyText(b.textContent);
      break;
    case 'copy-code':
      await copyText(b.closest('.code-wrap').querySelector('code').textContent, b);
      break;
    case 'copy-path':
      await copyText(`data/tasks/${c.id}/${b.dataset.file}`);
      break;
    case 'reveal-file':
      await api.post('reveal', { card: c.id, file: b.dataset.file });
      toast('已在' + fileManager() + '中显示');
      break;
    case 'video':
      playVideo(b);
      break;
    case 'copy-question':
      await exportText('question', { card: c.id, q: b.dataset.qid });
      break;
    case 'copy-writeup':
      await exportText('writeup', { card: c.id });
      break;
    case 'copy-help':
      await exportText('help', {
        card: c.id,
        problem: $('#help-problem').value,
        code: $('#help-code').value,
      });
      break;
    case 'copy-notes':
      await copyText(pageData.journal.copy_text);
      break;
    case 'export':
      await exportText('record');
      break;

    case 'acknowledge-conflict':
      api.conflicted = false;
      editBases.clear();
      connectionRecovered = false;
      renderNotices();
      toast('已继续使用最新记录；旧草稿仍保留，可逐张核对恢复。');
      break;
    case 'review-drafts':
      modal.dataset.from = b.dataset.from || '';
      reviewDrafts(b.dataset.card);
      break;
    case 'restore-draft':
      await restoreDraft(Number(b.dataset.index));
      break;
    case 'discard-draft':
      b.textContent = '确认丢弃这份草稿';
      b.dataset.action = 'confirm-discard-draft';
      break;
    case 'confirm-discard-draft':
      await discardDraft(Number(b.dataset.index));
      break;
    case 'check-update':
      await updates.check();
      break;
    case 'apply-update':
      await updates.apply();
      break;
  }
}
// Anything that takes longer than 150ms shows a busy state on the control that started it.
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-action]'),
    timer =
      b &&
      setTimeout(() => {
        if (b.isConnected) b.setAttribute('aria-busy', 'true');
      }, 150);
  handleClick(e)
    .catch(reportError)
    .finally(() => {
      clearTimeout(timer);
      b?.removeAttribute('aria-busy');
    });
});
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el.id === 'command-input') {
    commandIndex = 0;
    renderCommands(el.value);
    return;
  }
  if (state.view !== 'card' || !current()) return;
  const c = current(),
    r = rec(c);
  let field;
  if (el.dataset.answer) {
    r.answers[el.dataset.answer] = el.value;
    field = 'answer:' + el.dataset.answer;
  }
  if (el.dataset.check) {
    (r.checks[el.dataset.check] ||= {}).draft = el.value;
    drafts.put(c.id, 'check:' + el.dataset.check, el.value);
    updateSaveNotice();
  }
  if (el.id === 'summary-input') {
    r.summary = el.value;
    field = 'summary';
  }
  if (el.id === 'writeup-input') {
    r.writeup = el.value;
    field = 'writeup';
  }
  if (field) {
    trackEditBase(c.id, field, el);
    saveMessage = '正在保存…';
    if (e.isComposing) {
      drafts.put(c.id, field, el.value);
      return;
    }
    drafts.schedule(c.id, field, el.value, (text) => writeField(c.id, field, text));
    updateSaveNotice();
    updateComplete();
  }
});
document.addEventListener('compositionstart', (e) => {
  const el = e.target,
    field = el.dataset.answer
      ? 'answer:' + el.dataset.answer
      : el.id === 'summary-input'
        ? 'summary'
        : el.id === 'writeup-input'
          ? 'writeup'
          : null;
  if (field && current()) drafts.pause(current().id, field);
});
document.addEventListener('compositionend', (e) =>
  e.target.dispatchEvent(new Event('input', { bubbles: true })),
);
document.addEventListener('change', (e) => {
  const el = e.target;
  const task = async () => {
    if (el.id === 'journal-track') {
      state.journalTrack = el.value;
      await navigate('journal');
      return;
    }
    if (state.view !== 'card' || !current()) return;
    const c = current(),
      r = rec(c);
    if (el.id === 'all-questions') {
      r.allQuestions = el.checked;
      syncQuestions();
    }
    if (el.dataset.resource !== undefined) {
      try {
        await api.op('toggle_resource', {
          card: c.id,
          index: Number(el.dataset.resource),
          done: el.checked,
        });
      } catch (error) {
        el.checked = r.resources.includes(Number(el.dataset.resource));
        throw error;
      }
    }
    if (el.dataset.rubric !== undefined) {
      await drafts.flush();
      try {
        await api.op('toggle_rubric', {
          card: c.id,
          index: Number(el.dataset.rubric),
          done: el.checked,
        });
      } catch (error) {
        el.checked = r.rubric.includes(Number(el.dataset.rubric));
        throw error;
      }
      updateComplete();
    }
    if (el.id === 'toc-select' && el.value) anchorTo(el.value);
  };
  task().catch(reportError);
});

document.addEventListener('keydown', (e) => {
  const input = e.target.matches('input,textarea,select,[contenteditable="true"]'),
    cmd = e.metaKey || e.ctrlKey;
  if (cmd && e.key === ',') {
    e.preventDefault();
    openModal('settings');
    return;
  }
  if (cmd && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    modal.open && modal.dataset.kind === 'command' ? closeModal() : openModal('command');
    return;
  }
  if (e.key === 'Escape' && modal.open) {
    e.preventDefault();
    closeModal();
    return;
  }
  if (modal.open) {
    if (modal.dataset.kind === 'command') {
      if (['ArrowDown', 'ArrowUp'].includes(e.key)) {
        e.preventDefault();
        commandIndex =
          (commandIndex + (e.key === 'ArrowDown' ? 1 : -1) + commandItems.length) %
          Math.max(1, commandItems.length);
        renderCommands($('#command-input').value);
        $(`#command-${commandIndex}`)?.scrollIntoView({ block: 'nearest' });
      }
      if (e.key === 'Enter' && commandItems.length) {
        e.preventDefault();
        runCommand(
          e.target.closest('[data-action="command-run"]')
            ? Number(e.target.closest('[data-action="command-run"]').dataset.index)
            : commandIndex,
        ).catch(reportError);
      }
    }
    return;
  }
  if (cmd && e.key === '\\') {
    e.preventDefault();
    state.collapsed = !state.collapsed;
    changeSetting('sidebar_collapsed', state.collapsed).catch(reportError);
    document.body.classList.toggle('focus-mode', state.view === 'card' && !state.collapsed);
    document.body.classList.toggle('sidebar-collapsed', state.collapsed && state.view !== 'card');
    return;
  }
  if (cmd && e.key === 'Enter' && state.view === 'card' && rec(current()).phase === 'practice') {
    e.preventDefault();
    const c = current(),
      r = rec(c);
    if (c.type === 'lesson') {
      const q =
        c.questions.find((q) => q.id === e.target.closest('.question')?.dataset.qid) ||
        c.questions[r.activeQ];
      if (q) revealAnswer(q.id);
    } else {
      const qid = e.target.dataset.check || c.checks.find((q) => !r.checks[q.id]?.correct)?.id;
      if (qid) checkAnswer(qid).catch(reportError);
    }
    return;
  }
  if (input || cmd || e.altKey || e.isComposing) return;
  if (
    e.key === 'Enter' &&
    !e.repeat &&
    ['today', 'welcome'].includes(state.view) &&
    !e.target.closest('button,a,input,textarea,select,summary,[role=button],[contenteditable]')
  ) {
    const primary = $(state.view === 'today' ? '.hero-card .primary' : '.welcome .primary');
    if (primary) {
      e.preventDefault();
      primary.click();
    }
    return;
  }
  if (
    e.target.closest('.track-tabs,.journal-tabs') &&
    ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)
  ) {
    e.preventDefault();
    const list = e.target.closest('[role=tablist]'),
      buttons = $$('button', list);
    let index = buttons.indexOf(e.target);
    index =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? buttons.length - 1
          : (index + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[index].click();
    return;
  }
  if (e.key === '?') {
    e.preventDefault();
    openModal('shortcuts');
    return;
  }
  if (e.key.toLowerCase() === 'g') {
    gPressedAt = performance.now();
    return;
  }
  if (performance.now() - gPressedAt < 1200) {
    const v = { t: 'today', r: 'route', l: 'lab', j: 'journal' }[e.key.toLowerCase()];
    if (v) {
      e.preventDefault();
      gPressedAt = 0;
      navigate(v).catch(reportError);
      return;
    }
  }
  if (state.view === 'card') {
    const c = current(),
      r = rec(c);
    if (['ArrowLeft', 'ArrowRight'].includes(e.key)) {
      e.preventDefault();
      switchPhase(
        phases[
          Math.max(0, Math.min(2, phases.indexOf(r.phase) + (e.key === 'ArrowRight' ? 1 : -1)))
        ],
        { instant: true },
      ).catch(reportError);
    }
    if (r.phase === 'practice' && c.type === 'lesson' && ['y', 'n'].includes(e.key.toLowerCase())) {
      const q =
        c.questions.find((q) => q.id === e.target.closest('.question')?.dataset.qid) ||
        c.questions[r.activeQ];
      if (q && r.reveals[q.id]) {
        e.preventDefault();
        markQuestion(q.id, e.key.toLowerCase() === 'y' ? 'correct' : 'wrong').catch(reportError);
      }
    }
  }
});
modal.addEventListener('click', (e) => {
  if (e.target === modal) {
    const r = modal.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
      closeModal();
  }
});
modal.addEventListener('cancel', (e) => {
  e.preventDefault();
  closeModal();
});
let scrollQueued = false;
window.addEventListener(
  'scroll',
  () => {
    if (!scrollQueued) {
      scrollQueued = true;
      requestAnimationFrame(() => {
        updateScroll();
        scheduleUI();
        scrollQueued = false;
      });
    }
  },
  { passive: true },
);
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', applyPrefs);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyPrefs);
window.addEventListener('learning-system-appearance', (event) => {
  if (typeof event.detail?.dark === 'boolean') {
    window.__LEARNING_SYSTEM_DARK__ = event.detail.dark;
    applyPrefs();
  }
});

window.addEventListener('beforeunload', (e) => {
  if (!drafts.protected()) {
    e.preventDefault();
    e.returnValue = '';
  }
});
window.addEventListener('pagehide', () => {
  api.leaving = true;
  flushPending();
});
window.addEventListener('pageshow', () => {
  api.leaving = false;
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    drafts.flush();
    savePosition();
  }
});
window.addEventListener('unhandledrejection', (e) => {
  e.preventDefault();
  reportError(e.reason instanceof Error ? e.reason : new Error(String(e.reason)));
});
async function restoreRoute() {
  if (!booted || location.hash === lastHandledHash) return;
  const target = location.hash;
  lastHandledHash = target;
  restoringHistory = true;
  try {
    const [view, id, phase] = target.slice(1).split('/');
    if (view === 'card' && cards.some((c) => c.id === id)) {
      if (phases.includes(phase) && rec(id).phase !== phase) {
        rec(id).phase = phase;
        rec(id).anchor = '';
        rec(id).scroll = 0;
      }
      await openCard(id);
    } else {
      if (view === 'journal') state.journal = ['notes', 'review'].includes(id) ? id : 'timeline';
      await navigate(
        ['welcome', 'today', 'route', 'lab', 'journal'].includes(view) ? view : 'today',
      );
    }
  } catch (error) {
    reportError(error);
  } finally {
    restoringHistory = false;
  }
}
window.addEventListener('popstate', restoreRoute);
window.addEventListener('hashchange', restoreRoute);
// ─── Recovery: flushing before leaving, reconnecting, content updates ────────────────────────────
function protectionState() {
  return {
    saved:
      !api.pending &&
      !uiWrites.size &&
      !settingsPending &&
      !settingsFailed &&
      !drafts.entries().length,
    protected: drafts.protected(),
    updating: updates.updating,
  };
}
async function flushPending() {
  if (updates.updating) {
    await drafts.protect();
    return protectionState();
  }
  savePosition();
  const protection = drafts.protect();
  await Promise.all([drafts.flush(), flushPositions(), settingsQueue, ...uiWrites]);
  await api.queue;
  await protection;
  await drafts.protect();
  return protectionState();
}
// Text a field holds in the confirmed record (what a draft would be compared against).
function recordValue(c, field) {
  const v = c?.record || {};
  if (field === 'summary') return v.summary || '';
  if (field === 'writeup') return v.writeup?.text || '';
  if (field.startsWith('answer:')) return v.questions?.[field.slice(7)]?.my_answer || '';
  return null;
}
function knownValues() {
  const known = new Map();
  for (const d of drafts.entries())
    known.set(
      d.card + '|' + d.field,
      recordValue(
        cards.find((c) => c.id === d.card),
        d.field,
      ),
    );
  return known;
}
// After the service comes back, save this window's unconfirmed text again, field by field.
// A field another window changed meanwhile keeps its draft for the user to choose (CODEX_BRIEF 18.7 #4).
let recovering = null;
function recoverAfterReconnect(before) {
  recovering ||= (async () => {
    try {
      const known = before || knownValues();
      if (!before) await api.bootstrap();
      if (api.conflicted || snapshot.read_only) return;
      let kept = 0;
      for (const d of drafts.entries()) {
        if (d.client_id !== drafts.client) continue;
        const c = cards.find((x) => x.id === d.card),
          now = recordValue(c, d.field);
        if (now === null || !c?.unlocked) continue;
        if (now === d.text) {
          await drafts.confirm(d);
          continue;
        }
        if (known.has(d.card + '|' + d.field) && now !== known.get(d.card + '|' + d.field)) {
          kept++;
          continue;
        }
        try {
          // `now` was just read and still matches what this window knew before the break.
          await writeField(d.card, d.field, d.text, now);
          await drafts.confirm(d);
        } catch {
          kept++;
        }
      }
      saveMessage = kept
        ? '重新连接后，有 ' + kept + ' 处输入在别处也被改过，草稿已保留，请核对后选择。'
        : '';
    } finally {
      recovering = null;
      updateSaveNotice();
      updateComplete();
      if (booted) renderNotices();
    }
  })();
  return recovering;
}
async function reconnect() {
  // Called by the Mac shell after it restarted the service (new port token). Keep every DOM
  // input in place, re-protect drafts with the new service, then save them again where safe.
  drafts.cancelTimers();
  const before = knownValues();
  await api.bootstrap();
  drafts.protection.clear();
  await drafts.protect();
  await updates.refresh();
  await recoverAfterReconnect(before);
  const result = await flushPending();
  if (!result.protected)
    saveMessage = '后台已重新连接，但还有输入没有写入磁盘。请保持窗口打开，稍后会再试。';
  renderNotices();
  updateSaveNotice();
  return result;
}
function updateUpdateUI(status) {
  app.inert = status.state === 'updating';
  sidebar.inert = status.state === 'updating';
  if (modal.open && modal.dataset.kind === 'settings') {
    const panel = $('#updates-panel');
    if (panel) {
      const key = updates.renderKey(course.content_version);
      if (panel.dataset.renderKey !== key) {
        const appeared = status.state === 'available' && !panel.querySelector('.update-candidate');
        panel.innerHTML = updates.html({ esc, btn }, course.content_version);
        panel.dataset.renderKey = key;
        if (appeared)
          panel
            .querySelector('.update-candidate')
            ?.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'instant' : 'smooth' });
      }
    }
    $$('[data-action="setting"]', modal).forEach(
      (button) => (button.disabled = status.state === 'updating'),
    );
  }
  if (status.state === 'updating') saveMessage = '课程正在更新，输入已暂停。';
  else if (saveMessage === '课程正在更新，输入已暂停。') saveMessage = '';
}
async function afterUpdate(status) {
  saveMessage = '';
  await api.bootstrap();
  if (!snapshot.read_only) await api.op('visit');
  await loadPage(state.view === 'card' ? 'today' : state.view);
  closeModal();
  if (state.view === 'card' && !cards.some((c) => c.id === state.cardId && c.unlocked))
    state.view = 'today';
  render();
  toast(status.message || `更新完成，新增 ${status.result?.new_cards?.length || 0} 张卡。`);
}
// ─── Start ───────────────────────────────────────────────────────────────────────────────────────
async function start() {
  await api.bootstrap();
  await drafts.load();
  dropLegacyPositions();
  installDesktop({
    flushPending,
    protectionState,
    reconnect,
    openSettings: () => openModal('settings'),
    setReconnecting,
    api,
  });
  updates.start();
  sessionKey = 'learning-hub-today:' + snapshot.session_id;
  try {
    state.firstToday = sessionStorage.getItem(sessionKey) !== 'seen';
  } catch {}
  if (!snapshot.read_only) await api.op('visit');
  const hash = location.hash.slice(1).split('/');
  booted = true;
  if (snapshot.needs_welcome) {
    state.view = 'welcome';
    render();
  } else if (hash[0] === 'card' && cards.some((c) => c.id === hash[1])) {
    state.view = 'today';
    await loadPage('today');
    if (phases.includes(hash[2]) && rec(hash[1]).phase !== hash[2]) {
      rec(hash[1]).phase = hash[2];
      rec(hash[1]).anchor = '';
    }
    await openCard(hash[1]);
  } else {
    if (hash[0] === 'journal' && ['notes', 'review'].includes(hash[1])) state.journal = hash[1];
    await navigate(['today', 'route', 'lab', 'journal'].includes(hash[0]) ? hash[0] : 'today');
  }
  if (state.view === 'today') {
    try {
      sessionStorage.setItem(sessionKey, 'seen');
    } catch {}
  }
  lastHandledHash = location.hash;
  restoringHistory = false;
}
applyPrefs();
start().catch((error) => {
  app.innerHTML = html`
    <section class="shell">
      <header class="page-header">
        <h1 class="display">学习台暂时没有打开。</h1>
        <p class="lead">${esc(error.message)}</p>
      </header>
      <p>请重新打开学习台，然后重试。你的学习记录不会因此被清空。</p>
    </section>
  `;
});
