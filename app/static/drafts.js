// Unconfirmed drafts never expire. Only a verified save or an explicit discard
// resolves one version; a newer version and other clients' drafts remain intact.
export class Drafts {
  constructor(onChange) {
    this.prefix = 'learning-hub-pending-v2:';
    this.onChange = onChange;
    this.client = crypto.randomUUID().replaceAll('-', '');
    this.sequence = 0;
    this.memory = new Map();
    this.timers = new Map();
    this.inflight = new Set();
    this.protection = new Map();
    this.diskWrites = new Map();
    this.chains = new Map();
    this.storageFailed = false;
  }
  configure(api, project) {
    this.api = api;
    this.prefix = 'learning-hub-pending-v2:' + project + ':';
  }
  key(card, field, client = this.client) {
    return this.prefix + client + ':' + encodeURIComponent(card) + ':' + encodeURIComponent(field);
  }
  identifier(entry) {
    return this.key(entry.card, entry.field, entry.client_id);
  }
  get(card, field) {
    return this.memory.get(this.key(card, field)) || null;
  }
  notify(message) {
    this.onChange?.(message);
  }
  keep(entry) {
    const key = this.identifier(entry);
    this.memory.set(key, entry);
    try {
      localStorage.setItem(key, JSON.stringify(entry));
    } catch {
      this.storageFailed = true;
    }
    return entry;
  }
  put(card, field, text, baseline) {
    const existing = this.get(card, field);
    if (existing?.text === text) return existing;
    const entry = {
      client_id: this.client,
      card,
      field,
      text,
      sequence: ++this.sequence,
      at: new Date().toISOString(),
      baseline: baseline ?? null,
    };
    this.keep(entry);
    this.persist(entry);
    return entry;
  }
  persist(entry) {
    if (!this.api) return Promise.resolve(false);
    const key = this.identifier(entry),
      version = key + ':' + entry.sequence;
    if (this.protection.get(key) === entry.sequence) return Promise.resolve(true);
    if (this.diskWrites.has(version)) return this.diskWrites.get(version);
    // One write at a time per field: an older version never overtakes a newer one, and a
    // version already superseded before its turn is not sent (the newer one carries the text).
    const prior = this.chains.get(key) || Promise.resolve();
    const pending = prior
      .then(() =>
        (this.memory.get(key)?.sequence || 0) > entry.sequence
          ? false
          : this.api
              .post('drafts', entry)
              .then((data) => {
                const saved = data.draft || data;
                if (saved.sequence !== entry.sequence || saved.text !== entry.text)
                  throw new Error('草稿尚未得到磁盘确认。');
                entry.id = saved.id;
                if (saved.resolved) {
                  this.forget(entry);
                  return true;
                }
                this.protection.set(key, Math.max(this.protection.get(key) || 0, entry.sequence));
                if (this.memory.get(key)?.sequence === entry.sequence) this.keep(entry);
                this.notify();
                return true;
              })
              .catch(() => {
                // A request overtaken by a newer version of the same field is rejected by the server;
                // that newer version reports its own result, so only the latest failure is shown.
                if (!((this.memory.get(key)?.sequence || 0) > entry.sequence))
                  this.notify('还有输入尚未写入磁盘，请保持窗口打开。');
                return false;
              }),
      )
      .finally(() => this.diskWrites.delete(version));
    this.chains.set(key, pending);
    this.diskWrites.set(version, pending);
    return pending;
  }
  async load() {
    // Read only this project's keys, including earlier browser-only drafts.
    try {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      for (const key of keys) {
        if (!key?.startsWith(this.prefix)) continue;
        try {
          const row = JSON.parse(localStorage.getItem(key));
          if (row?.field === 'position') {
            localStorage.removeItem(key);
            continue;
          }
          if (
            row &&
            typeof row.card === 'string' &&
            typeof row.field === 'string' &&
            typeof row.text === 'string'
          ) {
            row.client_id ||= crypto.randomUUID().replaceAll('-', '');
            row.sequence ||= 1;
            this.keep(row);
            if (key !== this.identifier(row)) localStorage.removeItem(key);
          }
        } catch {}
      }
    } catch {}
    const data = await this.api.get('drafts');
    for (const row of data.drafts || []) {
      if (row.field === 'position') continue;
      const key = this.identifier(row),
        local = this.memory.get(key);
      if (!local || local.sequence <= row.sequence) {
        this.keep(row);
        this.protection.set(key, row.sequence);
      }
    }
    for (const row of this.memory.values()) await this.persist(row);
    await this.reconcile();
    setTimeout(() => this.reconcile(), 3500);
    this.notices = data.notices || [];
    if (data.notices?.length) this.notify(data.notices.join(' '));
    else this.notify();
  }
  // Earlier windows may have saved a field but closed before confirming its draft.
  // The server clears only drafts that match the saved record; anything else stays.
  // Drafts younger than 3s may still belong to a live window that is confirming them.
  async reconcile() {
    for (const row of [...this.memory.values()])
      if (row.client_id !== this.client && Date.now() - Date.parse(row.at) > 3000)
        await this.confirm(row);
  }
  entries(card) {
    return [...this.memory.values()].filter((row) => !card || row.card === card);
  }
  forget(entry) {
    const key = this.identifier(entry);
    if (this.memory.get(key)?.sequence !== entry.sequence) return;
    this.memory.delete(key);
    this.protection.delete(key);
    try {
      localStorage.removeItem(key);
    } catch {}
    this.notify();
  }
  async confirm(entry) {
    if (!entry) return true;
    if (!(await this.persist(entry))) return false;
    if (!this.memory.has(this.identifier(entry))) return true;
    try {
      const result = await this.api.post('drafts/confirm', {
        id: entry.id,
        sequence: entry.sequence,
      });
      if (result.resolved === false || result.confirmed === false || result === false) return false;
      this.forget(entry);
      return true;
    } catch {
      return false;
    }
  }
  async remove(card, field, text) {
    const row = this.get(card, field);
    if (row?.text === text) return this.confirm(row);
    return false;
  }
  async discard(entry) {
    if (!(await this.persist(entry))) throw new Error('草稿尚未写入磁盘，请保持窗口打开后重试。');
    if (!this.memory.has(this.identifier(entry))) return;
    const result = await this.api.post('drafts/discard', {
      id: entry.id,
      sequence: entry.sequence,
    });
    if (result.resolved === false || result.discarded === false || result === false)
      throw new Error('草稿已在别处更新，请重新打开页面后核对。');
    this.forget(entry);
  }
  schedule(card, field, text, save) {
    const draft = this.put(card, field, text);
    const key = this.key(card, field);
    clearTimeout(this.timers.get(key)?.timer);
    const entry = { card, field, text, save, draft };
    entry.timer = setTimeout(() => this.commit(key, entry), 500);
    this.timers.set(key, entry);
  }
  async commit(key, entry) {
    if (this.timers.get(key) !== entry) return;
    clearTimeout(entry.timer);
    this.timers.delete(key);
    const pending = (async () => {
      try {
        await entry.save(entry.text);
        await this.confirm(entry.draft);
        this.notify();
        return true;
      } catch {
        this.notify();
        return false;
      }
    })();
    this.inflight.add(pending);
    try {
      return await pending;
    } finally {
      this.inflight.delete(pending);
    }
  }
  async flush() {
    const starting = [...this.timers.entries()].map(([key, entry]) => this.commit(key, entry));
    await Promise.all([...starting, ...this.inflight]);
    return !this.entries().some(
      (row) => row.client_id === this.client && !row.field.startsWith('check:'),
    );
  }
  async protect() {
    await Promise.all(this.entries().map((row) => this.persist(row)));
    return this.protected();
  }
  protected() {
    return this.entries().every(
      (row) => this.protection.get(this.identifier(row)) === row.sequence,
    );
  }
  pause(card, field) {
    const key = this.key(card, field);
    clearTimeout(this.timers.get(key)?.timer);
    this.timers.delete(key);
  }
  cancelTimers() {
    for (const entry of this.timers.values()) clearTimeout(entry.timer);
    this.timers.clear();
  }
}
