// A request is frozen before transmission. Retrying a lost acknowledgement must
// use the same IDs, revision and body, even if more taps arrive in the meantime.
export function optimisticChoice(session, action) {
  if (!["like", "pass"].includes(action.kind)) return session;
  const index = session.cards.findIndex(card => card.face_id === action.face_id);
  if (index < 0 || !session.replacement_queue.length) throw Error("Wait for the next portraits to finish saving.");
  const cards = [...session.cards];
  cards[index] = session.replacement_queue[0];
  const likes = session.likes + Number(action.kind === "like");
  return { ...session, cards, replacement_queue: session.replacement_queue.slice(1), likes,
    likes_remaining: Math.max(0, session.required_likes - likes),
    current_crowd_choices: session.current_crowd_choices + 1 };
}

export class YourTypeQueue {
  constructor({ session, send, changed, save = () => {}, restored = null, id = () => crypto.randomUUID() }) {
    this.session = session; this.send = send; this.changed = changed; this.save = save; this.id = id;
    if (restored && (!Array.isArray(restored.pending) || restored.pending.length > 12 ||
        !Number.isSafeInteger(restored.base_revision) || restored.base_revision < 0 ||
        (restored.frozen && (!Number.isSafeInteger(restored.frozen.single?.expected_revision ?? restored.frozen.expected_revision)
          || (restored.frozen.single?.expected_revision ?? restored.frozen.expected_revision) < 0
          || !(restored.frozen.single || Array.isArray(restored.frozen.actions)))))) {
      throw Error("The saved recovery buffer could not be verified. Discard it to open the saved attempt.");
    }
    this.pending = restored ? JSON.parse(JSON.stringify(restored.pending)) : [];
    this.frozen = restored?.frozen ? JSON.parse(JSON.stringify(restored.frozen)) : null;
    this.error = restored && (this.pending.length || this.frozen) ? Error("Recovered choices need a save confirmation.") : null;
    this.conflict = Boolean(restored && !this.frozen && restored.base_revision !== session.revision && this.pending.length);
    this.running = null; this.timer = null; this.closed = false; this.controlling = false;
    this.emit();
  }
  snapshot() {
    let display = this.session;
    const frozen = this.frozen?.single ? [this.frozen.single] : this.frozen?.actions || [];
    const actions = [...frozen, ...this.pending];
    const expected = this.frozen?.single?.expected_revision ?? this.frozen?.expected_revision;
    const end = expected + frozen.length;
    // The recovery GET may already contain a committed frozen batch. Its exact
    // body is still replayed for confirmation, but its choices must not be
    // applied a second time while reconstructing the display.
    const displayActions = this.conflict ? [] : [
      ...(this.frozen && this.session.revision === expected ? frozen : []),
      ...(!this.frozen || this.session.revision === expected || this.session.revision === end ? this.pending : [])];
    try { for (const action of displayActions) display = optimisticChoice(display, action); }
    catch { /* A restored acknowledgement may already be reflected in the GET. */ }
    return { session: display, pending: actions.length, saving: Boolean(this.running),
      error: this.error, conflict: this.conflict, advancing: actions.some(a => a.kind === "next_crowd") };
  }
  emit() {
    if (!this.closed) {
      this.save(this.pending.length || this.frozen ? { base_revision: this.session.revision, frozen: this.frozen, pending: this.pending } : null);
      this.changed(this.snapshot());
    }
  }
  enqueue(action) {
    if (this.closed || this.error || this.conflict || this.controlling) return false;
    const state = this.snapshot();
    if (state.advancing || state.pending >= 12) return false;
    if (["like", "pass"].includes(action.kind)) optimisticChoice(state.session, action);
    this.pending.push(JSON.parse(JSON.stringify({ ...action, action_id: this.id() })));
    this.emit();
    if (!this.timer) this.timer = setTimeout(() => { this.timer = null; this.flush().catch(() => {}); }, 120);
    return true;
  }
  async flush() {
    clearTimeout(this.timer); this.timer = null;
    if (this.running) { await this.running; return this.flush(); }
    if (this.closed || this.conflict) throw Error("Reload the saved attempt to continue.");
    if (!this.frozen && !this.pending.length) return this.session;
    if (!this.frozen) {
      this.frozen = { batch_id: this.id(), expected_revision: this.session.revision, actions: this.pending.splice(0, 16) };
    }
    this.error = null;
    const frozen = this.frozen;
    this.running = (async () => {
      try {
        const accepted = await Promise.resolve().then(() => this.send(this.session.id, frozen));
        if (this.closed) return;
        this.session = accepted; this.frozen = null;
        const end = (frozen.single?.expected_revision ?? frozen.expected_revision) + (frozen.single ? 1 : frozen.actions.length);
        if (this.pending.length && accepted.revision !== end) {
          this.conflict = true;
          throw Error("The saved attempt advanced elsewhere. Reload before applying unsaved choices.");
        }
      } catch (error) {
        if (this.closed) throw error;
        this.error = error;
        this.conflict = this.conflict || [400, 404, 409, 410, 412, 422].includes(error?.response?.status);
        // A conflict needs a deliberate reload; never rebase selections onto
        // a different tab's portraits or turn them into different votes.
        throw error;
      } finally { this.running = null; this.emit(); }
    })();
    this.emit();
    await this.running;
    if (this.pending.length) return this.flush();
    return this.session;
  }
  replace(session) {
    if (this.pending.length || this.frozen || this.running) throw Error("Save your pending choices first.");
    this.session = session; this.emit();
  }
  async control(kind) {
    if (this.controlling) throw Error("Wait for the current control to finish saving.");
    this.controlling = true;
    try {
      await this.flush();
      this.frozen = { single: { action_id: this.id(), expected_revision: this.session.revision,
        kind, viewed_face_ids: [], dwell_ms: {} } };
      this.emit();
      return await this.flush();
    } finally { this.controlling = false; }
  }
  discardConflict(session) {
    this.pending = []; this.frozen = null; this.error = null; this.conflict = false;
    this.save(null); this.session = session; this.emit();
  }
  close() { this.closed = true; clearTimeout(this.timer); this.changed = () => {}; }
}
