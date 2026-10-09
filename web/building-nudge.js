'use strict';

// A spring-centred slider: every gesture starts from the last committed value.
class BuildingNudge {
  constructor({range, number, minus, plus, read, scale, preview, commit, cancel}) {
    Object.assign(this, {range, number, read, scale, preview, commit, cancel});
    this.origin = null;
    this.pending = false;
    range.addEventListener('input', () => {
      if (this.pending) return;
      if (this.origin === null) this.origin = read();
      this.value = this.origin + Number(range.value) * scale();
      number.value = this.value.toFixed(2);
      range.setAttribute('aria-valuetext', `${this.value.toFixed(2)} (변화 ${(this.value - this.origin).toFixed(2)})`);
      preview(this.value);
    });
    range.addEventListener('change', () => this.finish());
    range.addEventListener('pointerup', () => this.finish());
    range.addEventListener('blur', () => this.finish());
    range.addEventListener('pointercancel', () => this.abort());
    range.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); this.abort(); }
    });
    number.addEventListener('change', () => this.submit(Number(number.value)));
    minus.addEventListener('click', () => this.submit(read() - scale()));
    plus.addEventListener('click', () => this.submit(read() + scale()));
  }

  async submit(value) {
    if (this.pending) return;
    this.pending = true;
    try {
      if (!Number.isFinite(value) || this.number.value === '') { this.cancel(); return; }
      await this.commit(value);
    } finally {
      this.pending = false;
      this.number.value = this.read().toFixed(2);
      this.range.value = '0';
    }
  }

  finish() {
    if (this.origin === null) return;
    const value = this.value;
    this.origin = null;
    this.range.value = '0';
    return this.submit(value);
  }

  abort() {
    if (this.origin === null) return;
    this.origin = null;
    this.range.value = '0';
    this.number.value = this.read().toFixed(2);
    this.cancel();
  }
}
