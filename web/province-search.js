'use strict';

class ProvinceSearch {
  constructor({ form, input, clearButton, marker, feedback, viewport, getMapState, locate, transform, report }) {
    Object.assign(this, { input, clearButton, marker, feedback, viewport, getMapState, locate, transform });
    this.report = message => {
      feedback.textContent = message;
      feedback.title = message;
      report(message);
    };
    this.revision = 0;
    this.result = null;
    form.addEventListener('submit', event => {
      event.preventDefault();
      this.search();
    });
    clearButton.addEventListener('click', () => this.clear());
    // Keep typing/undo shortcuts inside the search field away from map editing.
    input.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.key === 'Escape') { this.clear(); input.blur(); }
    });
  }

  clear() {
    this.revision++;
    this.result = null;
    this.marker.hidden = true;
    this.clearButton.disabled = true;
    this.feedback.textContent = '';
    this.feedback.title = '';
  }

  async search() {
    this.clear();
    const revision = this.revision;
    const map = this.getMapState();
    if (!map.loaded) { this.report('먼저 맵을 열어주세요.'); return; }
    const identifier = this.input.value.trim();
    if (!/^[0-9]+$/.test(identifier) || !Number.isSafeInteger(Number(identifier)) || Number(identifier) <= 0) {
      this.report('프로빈스 ID는 양의 정수로 입력해주세요.');
      return;
    }
    this.report(`프로빈스 ID ${Number(identifier)} 검색 중…`);
    this.clearButton.disabled = false;
    try {
      const result = await this.locate(Number(identifier));
      if (revision !== this.revision) return;
      if (!result.ok) { this.clear(); this.report(result.error); return; }
      this.result = result;
      const [left, top, right, bottom] = result.bounds;
      // Keep tiny provinces visible without making broad/disconnected ones microscopic.
      map.zoom = Math.max(1, Math.min(4,
        this.viewport.clientWidth * 0.55 / (right - left + 1),
        this.viewport.clientHeight * 0.55 / (bottom - top + 1)));
      map.panX = this.viewport.clientWidth * 0.4 - (result.x + 0.5) * map.zoom;
      map.panY = this.viewport.clientHeight * 0.5 - (result.y + 0.5) * map.zoom;
      this.transform();
      this.render();
      this.report(`프로빈스 ID ${result.provinceId} · ${result.pixelCount.toLocaleString()} px · 위치 (${result.x}, ${result.y})`);
    } catch (error) {
      if (revision !== this.revision) return;
      this.clear();
      this.report(`프로빈스 검색 실패: ${error.message || error}`);
    }
  }

  render() {
    if (!this.result) return;
    const map = this.getMapState();
    this.marker.hidden = false;
    this.marker.style.left = `${map.panX + (this.result.x + 0.5) * map.zoom}px`;
    this.marker.style.top = `${map.panY + (this.result.y + 0.5) * map.zoom}px`;
    this.marker.textContent = `ID ${this.result.provinceId}`;
  }
}
