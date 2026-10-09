'use strict';

class BuildingEditor {
  constructor({state, base, api, report, history, clearHistory, transform, undo, redo}) {
    Object.assign(this, {state, api, report, history});
    this.records = new Map(); this.templates = [];
    this.provinces = new Set(); this.locked = new Set();
    this.selected = null; this.preview = null; this.dirty = false; this.busy = false;
    this.panel = document.querySelector('#building-panel');
    this.popup = document.querySelector('#building-popup');
    this.filter = document.querySelector('#building-filter');
    this.canvas = document.querySelector('#building-canvas');
    this.view = new BuildingView({canvas: this.canvas, base, state,
      records: () => this.records, selected: () => this.selected,
      provinces: () => this.provinces, filter: () => this.filter.value,
      preview: () => this.preview, click: (...args) => this.onMapClick(...args), transform,
      loadModel: name => this.api().get_building_model(name),
      modelStatus: message => { document.querySelector('#building-model-status').textContent = message; }});
    this.filter.addEventListener('change', () => { this.renderList(); this.view.schedule(); });
    document.querySelector('#building-clear-selection').addEventListener('click', () => {
      this.provinces.clear(); this.view.updateSelection(); this.renderList();
    });
    document.querySelector('#building-fill').addEventListener('click', () => this.generate(false));
    document.querySelector('#building-randomize').addEventListener('click', () => this.generate(true));
    document.querySelector('#building-save').addEventListener('click', () => this.save());
    document.querySelector('#building-undo').addEventListener('click', undo);
    document.querySelector('#building-redo').addEventListener('click', redo);
    document.querySelector('#building-game-folder').addEventListener('click', async () => {
      if (this.busy) return;
      await this.run(async () => {
        const result = await api().pick_building_game_root();
        if (result.cancelled) return;
        this.acceptLoad(result);
      });
    });
    document.querySelector('#building-reload').addEventListener('click', async () => {
      await this.run(async () => {
        const result = await api().reload_buildings();
        if (!result.ok) throw new Error(result.error);
        this.reset(); clearHistory(); this.acceptLoad(result);
        this.message('건물 배치를 디스크에서 다시 불러왔습니다.');
      });
    });
    document.querySelector('#building-popup-close').addEventListener('click', () => this.closePopup());
    document.querySelector('#building-lock').addEventListener('change', event => {
      if (!this.selected) return;
      if (event.target.checked) this.locked.add(this.selected); else this.locked.delete(this.selected);
      this.renderList();
    });
    document.querySelector('#building-auto-height').addEventListener('change', () => this.syncPopup());
    document.querySelector('#building-target').addEventListener('change', event => this.edit({target: event.target.value}));
    this.nudges = [];
    for (const field of ['x', 'z', 'y', 'rotation']) {
      const row = document.querySelector(`[data-building-axis="${field}"]`);
      const convert = value => field === 'rotation' ? value * Math.PI / 180 : value;
      const nudge = new BuildingNudge({range: row.querySelector('[type=range]'), number: row.querySelector('[type=number]'),
        minus: row.querySelector('[data-step=minus]'), plus: row.querySelector('[data-step=plus]'),
        read: () => {
          const value = this.records.get(this.selected)?.[field] || 0;
          return field === 'rotation' ? value * 180 / Math.PI : value;
        },
        scale: () => Number(document.querySelector('#building-step').value) * (field === 'rotation' ? 10 : 1),
        preview: value => {
          const original = this.records.get(this.selected);
          if (original) {
            this.preview = {...original, [field]: convert(value)};
            this.validatePreview();
          }
          this.view.schedule();
        },
        commit: value => this.edit({[field]: convert(value)}),
        cancel: () => { this.preview = null; this.view.schedule(); },
      });
      this.nudges.push(nudge);
    }
  }

  reset() {
    this.view.models.reset();
    this.records.clear(); this.provinces.clear(); this.locked.clear();
    this.dirty = false; this.selected = null; this.preview = null;
    this.popup.hidden = true; this.view.mask.width = 0;
    this.renderList();
  }

  async setActive(active) {
    this.nudges.forEach(nudge => nudge.abort());
    this.active = active;
    this.panel.hidden = !active; this.canvas.hidden = !active;
    document.querySelector('#canvas-area').classList.toggle('building-mode', active);
    this.popup.hidden = true;
    if (!active || !this.state().loaded) return;
    document.querySelector('#building-message').textContent = '건물 정의와 배치 좌표를 불러오는 중…';
    await this.run(async () => this.acceptLoad(await this.api().load_buildings()));
    this.view.schedule();
  }

  acceptLoad(result) {
    if (!result.ok) throw new Error(result.error);
    this.view.models.reset();
    if (this.view.models.available) document.querySelector('#building-model-status').textContent =
      this.state().heightmapValueBuf ? '높이맵 지형 · 원근 보기 · 모델 정지 자세' : '높이맵 없음: 평탄한 지형 · 원근 보기';
    this.records = new Map(result.records.map(row => [row.id, row]));
    this.templates = result.templates; this.dirty = result.dirty;
    const previous = this.filter.value;
    this.filter.replaceChildren(new Option('전체 건물', ''));
    const names = [...new Set([...this.templates.map(entry => entry.name), ...result.records.map(row => row.type)])].sort();
    for (const name of names) this.filter.add(new Option(name, name));
    if (names.includes(previous)) this.filter.value = previous;
    document.querySelector('#building-warnings').textContent = result.warnings.join('\n');
    document.querySelector('#building-game-folder').title = result.gameRoot || '게임 루트 폴더 선택';
    document.querySelector('#building-message').textContent = `${this.records.size.toLocaleString()}개 배치 · 지도를 확대하거나 영역을 선택하세요.`;
    this.renderList(); this.view.updateSelection();
  }

  async run(action) {
    if (this.busy || this.state().historyBusy) return false;
    this.busy = true;
    this.setDisabled(true);
    try { await action(); return true; }
    catch (error) { this.message(error.message || String(error), true); return false; }
    finally { this.busy = false; this.setDisabled(false); this.syncPopup(); }
  }

  setDisabled(disabled) {
    for (const element of document.querySelectorAll('#building-panel button, #building-panel select, #building-popup input, #building-popup button, #building-popup select')) {
      element.disabled = disabled;
    }
  }

  message(text, error = false) {
    document.querySelector('#building-message').textContent = text;
    document.querySelector('#building-popup-error').textContent = error ? text : '';
    this.report(text);
  }

  onMapClick(identifier, point, additive, screen) {
    if (this.busy) return;
    this.nudges.forEach(nudge => nudge.abort());
    if (identifier) return this.select(identifier, screen);
    const state = this.state(), x = Math.floor(point[0]), y = Math.floor(point[1]);
    if (x < 0 || y < 0 || x >= state.width || y >= state.height) return;
    const offset = (y * state.width + x) * 4;
    const key = `${state.pixelBuf[offset]},${state.pixelBuf[offset + 1]},${state.pixelBuf[offset + 2]}`;
    const province = state.rgbToProvinceId.get(key);
    if (!province) {
      this.message('아직 ID가 없는 새 색상입니다. 지도 저장으로 프로빈스 ID를 발급하세요.'); return;
    }
    if (state.seaRgbSet.has(key) || state.lakeRgbSet.has(key)) {
      this.message('바다나 호수가 아닌 육지 영역을 선택하세요.'); return;
    }
    let selection = [province];
    if (document.querySelector('#building-selection-mode').value === 'state') {
      const stateId = state.assignments.get(province);
      if (stateId === undefined) {
        this.message(`프로빈스 ${province}에는 아직 주가 지정되지 않았습니다.`); return;
      }
      selection = [...state.assignments].filter(([id, owner]) => {
        const color = state.provinceRgbById.get(id)?.join(',');
        return owner === stateId && color && !state.seaRgbSet.has(color) && !state.lakeRgbSet.has(color);
      }).map(([id]) => id);
    }
    const remove = additive && selection.every(id => this.provinces.has(id));
    if (!additive) this.provinces.clear();
    for (const id of selection) {
      if (remove) this.provinces.delete(id); else this.provinces.add(id);
    }
    this.closePopup(); this.view.updateSelection(); this.renderList();
  }

  select(identifier, screen) {
    if (this.busy || !this.records.has(identifier)) return;
    this.nudges.forEach(nudge => nudge.abort());
    this.selected = identifier; this.preview = null;
    const record = this.records.get(identifier);
    this.popup.hidden = false;
    document.querySelector('#building-popup-error').textContent = '';
    this.syncPopup();
    const point = screen || this.view.project(record.x, this.state().height - record.z, record.y);
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    this.popup.style.left = `${Math.max(8, Math.min(width - this.popup.offsetWidth - 8, point[0] + 18))}px`;
    this.popup.style.top = `${Math.max(8, Math.min(height - this.popup.offsetHeight - 8, point[1] + 12))}px`;
    this.view.schedule();
  }

  closePopup() {
    this.nudges.forEach(nudge => nudge.abort());
    this.selected = null; this.preview = null; this.popup.hidden = true; this.view.schedule();
  }

  syncPopup() {
    const record = this.records.get(this.selected);
    if (!record) { this.popup.hidden = true; return; }
    document.querySelector('#building-popup-title').textContent = record.type;
    document.querySelector('#building-popup-subtitle').textContent = `주 ${record.state} · 프로빈스 ${record.province}`;
    for (const field of ['x', 'z', 'y', 'rotation']) {
      const row = document.querySelector(`[data-building-axis="${field}"]`);
      row.querySelector('[type=number]').value = (field === 'rotation' ? record[field] * 180 / Math.PI : record[field]).toFixed(2);
      const automatic = field === 'y' && document.querySelector('#building-auto-height').checked;
      for (const control of row.querySelectorAll('input, button')) control.disabled = this.busy || automatic;
    }
    document.querySelector('#building-target').value = record.target;
    const template = this.templates.find(entry => entry.name === record.type || entry.buildings.includes(record.type));
    document.querySelector('#building-target-row').hidden = !template?.port && !['naval_base', 'naval_base_spawn', 'floating_harbor'].includes(record.type);
    document.querySelector('#building-target-label').textContent = record.type === 'floating_harbor' ? '대상 육지 ID' : '연결 바다 ID';
    document.querySelector('#building-lock').checked = this.locked.has(record.id);
  }

  applyChange(result, remember = true) {
    if (!result.ok) throw new Error(result.error);
    for (const identifier of result.removed) { this.records.delete(identifier); this.locked.delete(identifier); }
    for (const record of result.records) this.records.set(record.id, record);
    if (remember && JSON.stringify(result.before) !== JSON.stringify(result.after)) {
      this.history({kind: 'buildings', before: result.before, after: result.after});
    }
    this.dirty = result.dirty; this.preview = null;
    this.renderList(); this.syncPopup(); this.view.schedule();
  }

  validatePreview() {
    const row = this.preview, state = this.state();
    if (!row) return;
    const x = Math.floor(row.x), y = state.height - 1 - Math.floor(row.z);
    const inside = x >= 0 && y >= 0 && x < state.width && y < state.height;
    const offset = (y * state.width + x) * 4;
    const key = inside ? `${state.pixelBuf[offset]},${state.pixelBuf[offset + 1]},${state.pixelBuf[offset + 2]}` : '';
    row.invalid = !inside || (row.type === 'floating_harbor'
      ? !state.seaRgbSet.has(key) : state.rgbToProvinceId.get(key) !== row.province);
    document.querySelector('#building-popup-error').textContent = row.invalid ? '배치 가능한 영역을 벗어났습니다. 놓으면 변경이 검증됩니다.' : '';
  }

  async edit(values) {
    if (!this.selected) return;
    const identifier = this.selected;
    await this.run(async () => {
      const result = await this.api().update_building(identifier, values, document.querySelector('#building-auto-height').checked);
      this.applyChange(result); this.message('건물 좌표를 변경했습니다. 저장 전까지 실행 취소할 수 있습니다.');
    });
    this.preview = null; this.view.schedule();
  }

  async generate(randomize) {
    const names = this.filter.value ? [this.filter.value] : this.templates.filter(entry => !entry.disabled && entry.max > 0).map(entry => entry.name);
    if (!names.length) return this.message('생성할 건물 정의가 없습니다. 게임 폴더를 확인하세요.', true);
    await this.run(async () => {
      const result = await this.api().generate_buildings([...this.provinces], names, [...this.locked], randomize);
      this.applyChange(result);
      const count = result.records.length;
      document.querySelector('#building-warnings').textContent = (result.warnings || []).join('\n');
      this.message(`${randomize ? '재배치' : '누락 생성'} ${count}개 · 저장 전 미리보기`);
    });
  }

  async restore(replacements) {
    const result = await this.api().restore_buildings(replacements);
    if (result.ok) this.applyChange(result, false);
    return result;
  }

  async save() {
    if (this.busy) { this.message('좌표 조정이 끝난 뒤 저장하세요.'); return false; }
    return this.run(async () => {
      const result = await this.api().save_buildings();
      if (!result.ok) throw new Error(result.error);
      this.dirty = false; this.renderList(); this.message('건물 배치를 저장했습니다.');
    });
  }

  renderList() {
    const stateCount = new Set([...this.provinces].map(id => this.state().assignments.get(id))
      .filter(id => id !== undefined)).size;
    document.querySelector('#building-selection').textContent = this.provinces.size
      ? `선택 ${stateCount}개 주 · ${this.provinces.size}개 프로빈스` : '지도 클릭으로 영역 선택 · Shift로 추가/해제';
    document.querySelector('#building-dirty').textContent = this.dirty ? '저장하지 않은 변경' : '저장됨';
    const list = document.querySelector('#building-list'); list.replaceChildren();
    const rows = [...this.records.values()].filter(row => (!this.filter.value || row.type === this.filter.value)
      && this.provinces.has(row.province));
    document.querySelector('#building-count').textContent = `선택 영역 배치 ${rows.length}개`;
    for (const row of rows.slice(0, 200)) {
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = `${this.locked.has(row.id) ? '🔒 ' : ''}${row.type} · ${row.province}`;
      button.addEventListener('click', () => this.select(row.id)); list.append(button);
    }
    if (rows.length > 200) list.append(document.createTextNode('처음 200개 표시 · 건물 종류로 범위를 좁히세요.'));
  }
}
