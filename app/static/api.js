// All record mutations are serial. Program state applies to the latest record on the server;
// typed text carries `base`, the text its edit started from, and conflicts only when that one
// field was changed somewhere else in the meantime.
export class APIError extends Error {
  constructor(status, data) {
    super(data.message || '请求没有完成，请稍后重试。');
    this.status = status;
    this.data = data;
  }
}
export class API {
  static retryLimit = 15;
  waiting = 0;
  connection(delta) {
    const before = this.waiting;
    this.waiting = Math.max(0, this.waiting + delta);
    if (!before !== !this.waiting) this.onConnection?.(!this.waiting);
  }
  revision = '';
  queue = Promise.resolve();
  conflicted = false;
  pending = 0;
  leaving = false;
  constructor(onSnapshot, onError) {
    this.onSnapshot = onSnapshot;
    this.onError = onError;
  }
  async request(path, options = {}) {
    let response;
    // While the page is being hidden, small writes use keepalive so they can land after unload.
    const keepalive =
      this.leaving && typeof options.body === 'string' && options.body.length < 32768;
    // A service that is restarting is retried quietly (0.5s, 1s, 2s, 4s, then every 5s, about a
    // minute in all) while the page shows "正在重新连接……"; only then is it reported as an error.
    for (let attempt = 0; ; attempt++) {
      try {
        response = await fetch(path, {
          cache: 'no-store',
          ...(keepalive ? { keepalive } : {}),
          ...options,
          headers: {
            'Content-Type': 'application/json',
            ...(this.controlToken &&
            /^\/api\/(drafts|updates|heartbeat|shutdown)(?:\/|$)/.test(path)
              ? { 'X-Learning-Control': this.controlToken }
              : {}),
            ...options.headers,
          },
        });
        if (attempt) this.connection(-1);
        break;
      } catch {
        if (this.leaving || attempt >= API.retryLimit) {
          if (attempt) this.connection(-1);
          throw new Error('没有连接到学习台。请确认学习台仍然运行；尚未保存的输入会保留为草稿。');
        }
        if (!attempt) this.connection(1);
        await new Promise((resolve) => setTimeout(resolve, Math.min(5000, 500 * 2 ** attempt)));
      }
    }
    const data = await response.json();
    // A restarted service issues a new control token: refresh it once and retry.
    if (
      response.status === 403 &&
      !options.retriedToken &&
      /^\/api\/(drafts|updates|heartbeat|shutdown)(?:\/|$)/.test(path)
    ) {
      try {
        await this.bootstrap();
      } catch {}
      return this.request(path, { ...options, retriedToken: true });
    }
    if (!response.ok) throw new APIError(response.status, data);
    return data;
  }
  async bootstrap() {
    const data = await this.request('/api/bootstrap');
    this.accept(data);
    return data;
  }
  accept(data) {
    if (data.desktop?.control_token) this.controlToken = data.desktop.control_token;
    if (data.revision !== undefined) this.revision = data.revision;
    if (data.cards && data.course) this.onSnapshot(data);
  }
  get(path) {
    return this.request('/api/' + path);
  }
  post(path, data) {
    return this.request('/api/' + path, { method: 'POST', body: JSON.stringify(data) });
  }
  // quiet: program state (reading position) fails silently; it is written again next time.
  op(op, params = {}, { quiet = false } = {}) {
    const operation = async () => {
      if (this.conflicted)
        throw new APIError(409, { message: '记录已在别处更新。请先检查并恢复草稿，再继续保存。' });
      try {
        const data = await this.post('op', { op, ...params, revision: this.revision });
        this.accept(data);
        return data;
      } catch (error) {
        // Only typed text can conflict: the same field changed in another window.
        if (error.status === 409) {
          this.conflicted = true;
          try {
            await this.bootstrap();
          } catch {}
        }
        if (!quiet) this.onError(error);
        throw error;
      }
    };
    this.pending++;
    const result = this.queue.then(operation).finally(() => this.pending--);
    this.queue = result.catch(() => {});
    return result;
  }
  async settings(settings) {
    return this.request('/api/settings', { method: 'PUT', body: JSON.stringify(settings) });
  }
}
