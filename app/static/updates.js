import { html } from './html.js';
// Checking only prepares a candidate. Applying always requires a separate click.
export class Updates {
  constructor(api, callbacks) {
    this.api = api;
    this.callbacks = callbacks;
    this.status = { state: 'idle', busy: false };
    this.applying = false;
    this.blocked = '';
  }
  async refresh() {
    try {
      const previous = this.status.state;
      this.status = await this.api.get('updates/status');
      this.callbacks.changed(this.status);
      if (this.status.state === 'success' && (previous === 'updating' || this.awaitingResult)) {
        this.awaitingResult = false;
        await this.callbacks.completed(this.status);
      }
    } catch (error) {
      if (this.status.busy) this.callbacks.error(error);
    }
  }
  start() {
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 1000);
  }
  async check() {
    this.status = await this.api.post('updates/check', {});
    this.callbacks.changed(this.status);
  }
  async apply() {
    if (this.applying || this.status.state !== 'available') return;
    this.applying = true;
    // Unconfirmed drafts still block an update (they are protected first); say so here,
    // inside the settings dialog, instead of in a toast the dialog would cover.
    try {
      this.blocked = '';
      this.callbacks.changed(this.status);
      const protection = await this.callbacks.flush();
      if (!protection.saved) {
        this.blocked = this.blockReason() ? '' : '还有输入正在保存。请稍等几秒，再点“更新”。';
        return;
      }
      this.awaitingResult = true;
      this.status = await this.api.post('updates/apply', {
        candidate_id: this.status.candidate.id,
      });
      this.callbacks.changed(this.status);
    } finally {
      this.applying = false;
      this.callbacks.changed(this.status);
    }
  }
  get updating() {
    return this.status.state === 'updating';
  }
  blockReason() {
    const n = this.callbacks.pendingDrafts?.() || 0;
    return n ? `还有 ${n} 份草稿等待你确认。草稿会一直保留；逐条恢复或丢弃后，才能更新课程。` : '';
  }
  renderKey(version) {
    return JSON.stringify([
      this.status,
      version,
      this.applying,
      this.blocked,
      this.callbacks.pendingDrafts?.() || 0,
    ]);
  }
  html({ esc, btn }, version) {
    const value = this.status,
      candidate = value.candidate;
    const step =
      value.step === 'draining'
        ? '正在保存输入…'
        : value.step === 'prepared'
          ? '备份已完成…'
          : value.step?.includes('replace')
            ? '正在更新课程文件…'
            : value.step?.includes('validate')
              ? '正在校验课程…'
              : value.step?.includes('restore')
                ? '正在恢复原有内容…'
                : value.step === 'committed'
                  ? '正在完成更新…'
                  : value.step
                    ? '正在准备更新…'
                    : '';
    return html`
      <section class="content-updates" aria-labelledby="content-update-title">
        <h3 id="content-update-title">课程内容</h3>
        <p class="meta">当前版本 ${esc(version || '')}</p>
        <div role="status" aria-live="polite">${value.message ? `<p>${esc(value.message)}</p>` : ''}${step ? `<p class="meta">${esc(step)}</p>` : ''}${value.issues?.length ? `<ul class="update-issues">${value.issues.map((issue) => `<li>${esc(issue)}</li>`).join('')}</ul>` : ''}</div>${
          value.state === 'available' && candidate
            ? html`
              <div class="update-candidate">
                <p>
                  <strong>${esc(candidate.filename)}</strong>
                </p>
                <p>${esc(candidate.old_version)} → ${esc(candidate.new_version)}</p>${candidate.new_cards?.length ? `<ul>${candidate.new_cards.map((c) => `<li>${esc(c.code)} ${esc(c.title)}</li>`).join('')}</ul>` : '<p class="meta">本次没有新增卡片。</p>'}${
                  this.blockReason() || this.blocked
                    ? html`
                      <div class="update-blocked" role="alert">
                        <p>${esc(this.blockReason() || this.blocked)}</p>${this.blockReason() ? btn('查看草稿', 'review-drafts', 'data-from="settings"', 'secondary') : ''}</div>
                    `
                    : ''
                }${btn(this.applying ? '正在保存输入…' : '更新', 'apply-update', this.applying || this.blockReason() ? 'disabled' : '', 'primary')}</div>
            `
            : ''
        }${!value.busy ? btn('检查更新', 'check-update', '', 'secondary') : '<p class="meta">请稍等，完成后会自动更新页面。</p>'}</section>
    `;
  }
}
