'use strict';

// Perspective terrain/model view, with CPU projection for picking and popups.
class BuildingView {
  constructor({canvas, base, state, records, selected, provinces, filter, preview, click, transform, loadModel, modelStatus}) {
    Object.assign(this, {canvas, base, state, records, selected, provinces, filter, preview, click, transform});
    this.frame = 0;
    this.hits = [];
    this.mask = document.createElement('canvas');
    this.camera = new BuildingCamera(state);
    this.models = new BuildingModels(loadModel, () => this.schedule(), modelStatus);
    canvas.addEventListener('contextmenu', event => event.preventDefault());
    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 0) {
        const rect = canvas.getBoundingClientRect();
        this.pan = this.unproject(event.clientX - rect.left, event.clientY - rect.top);
        canvas.setPointerCapture(event.pointerId);
      } else {
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left, y = event.clientY - rect.top;
        const candidates = this.hits.filter(hit => Math.hypot(hit.x - x, hit.y - y) < 12 ||
          (hit.bounds && x >= hit.bounds[0] && y >= hit.bounds[1] && x <= hit.bounds[2] && y <= hit.bounds[3]));
        candidates.sort((a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y));
        const point = this.unproject(x, y);
        click(event.shiftKey ? null : candidates[0]?.id, point, event.shiftKey, [x, y]);
      }
    });
    canvas.addEventListener('pointermove', event => {
      if (!this.pan) return;
      const rect = canvas.getBoundingClientRect();
      this.anchorAt(this.pan, event.clientX - rect.left, event.clientY - rect.top);
      transform();
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      canvas.addEventListener(name, () => { this.pan = null; });
    }
    canvas.addEventListener('wheel', event => {
      event.preventDefault(); event.stopPropagation();
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const point = this.unproject(x, y);
      state().zoom = Math.max(.05, Math.min(64, state().zoom * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
      this.anchorAt(point, x, y);
      transform();
    }, {passive: false});
    new ResizeObserver(() => this.schedule()).observe(canvas.parentElement);
  }

  anchorAt(point, screenX, screenY) {
    for (let iteration = 0; iteration < 4; iteration++) {
      const current = this.unproject(screenX, screenY);
      this.state().panX += (current[0] - point[0]) * this.state().zoom;
      this.state().panY += (current[1] - point[1]) * this.state().zoom;
    }
  }

  project(x, y, altitude) {
    const state = this.state();
    if (this.models.available) {
      this.camera.update(this.canvas.clientWidth, this.canvas.clientHeight);
      return this.camera.project(x, altitude ?? this.camera.terrainHeight(x, state.height - y), state.height - y).slice(0, 2);
    }
    return [state.panX + state.zoom * x, state.panY + state.zoom * y];
  }

  unproject(x, y) {
    const state = this.state();
    if (this.models.available) {
      this.camera.update(this.canvas.clientWidth, this.canvas.clientHeight);
      return this.camera.unproject(x, y);
    }
    return [(x - state.panX) / state.zoom, (y - state.panY) / state.zoom];
  }

  updateSelection() {
    const state = this.state();
    this.mask.width = state.width; this.mask.height = state.height;
    if (this.models.terrain) this.models.terrain.selectionDirty = true;
    if (!state.loaded || !this.provinces().size) return this.schedule();
    const colors = new Set([...this.provinces()].map(id => state.provinceRgbById.get(id))
      .filter(Boolean).map(rgb => rgb[0] << 16 | rgb[1] << 8 | rgb[2]));
    const context = this.mask.getContext('2d'), image = context.createImageData(state.width, state.height);
    const pixels = state.pixelBuf;
    for (let offset = 0; offset < pixels.length; offset += 4) {
      if (!colors.has(pixels[offset] << 16 | pixels[offset + 1] << 8 | pixels[offset + 2])) continue;
      image.data[offset] = 255; image.data[offset + 1] = 220;
      image.data[offset + 2] = 115; image.data[offset + 3] = 110;
    }
    context.putImageData(image, 0, 0);
    this.schedule();
  }

  schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); });
  }

  draw() {
    if (this.canvas.hidden || !this.state().loaded) return;
    const state = this.state(), width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(width * ratio) || this.canvas.height !== Math.round(height * ratio)) {
      this.canvas.width = Math.round(width * ratio); this.canvas.height = Math.round(height * ratio);
    }
    const context = this.canvas.getContext('2d');
    this.camera.update(width, height);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = '#151a23'; context.fillRect(0, 0, width, height);
    if (!this.models.available) {
      context.save();
      context.transform(state.zoom, 0, 0, state.zoom, state.panX, state.panY);
      context.imageSmoothingEnabled = false;
      context.globalAlpha = .6; context.drawImage(this.base, 0, 0);
      context.globalAlpha = 1;
      if (this.mask.width) context.drawImage(this.mask, 0, 0);
      context.restore();
    }
    this.hits = [];
    const visible = [];
    for (const original of this.records().values()) {
      if (this.filter() && original.type !== this.filter() && original.id !== this.selected()) continue;
      const row = this.preview()?.id === original.id ? this.preview() : original;
      const [x, y, depth] = this.models.available ? this.camera.project(row.x, row.y, row.z)
        : [...this.project(row.x, state.height - row.z), 1];
      if (depth <= this.camera.near) continue;
      const bounds = this.models.available ? this.models.bounds(row, this.camera) : null;
      if (bounds ? bounds[2] < 0 || bounds[3] < 0 || bounds[0] > width || bounds[1] > height
        : x < -30 || y < -30 || x > width + 30 || y > height + 30) continue;
      if (state.zoom < 1.2 && !this.provinces().has(row.province) && row.id !== this.selected()) continue;
      visible.push({row, x, y, bounds});
    }
    visible.sort((a, b) => a.y - b.y);
    const rendered = this.models.draw(context, visible, this.camera, state, this.base, this.mask, ratio);
    for (const {row, x, y, bounds} of visible) {
      const active = row.id === this.selected();
      const radius = Math.max(3, Math.min(10, state.zoom * 1.3));
      const color = row.invalid ? '#ff817c' : active ? '#ffdc73' : row.target ? '#80d8ef' : '#e9edf5';
      context.save(); context.translate(x, y);
      if (!rendered.has(row.id)) {
        context.fillStyle = color; context.beginPath(); context.arc(0, 0, radius, 0, Math.PI * 2); context.fill();
      }
      context.strokeStyle = color; context.lineWidth = active ? 2 : 1;
      if (active || !rendered.has(row.id)) {
        context.beginPath(); context.moveTo(0, 0);
        const direction = this.project(row.x + Math.sin(row.rotation) * (radius + 9) / state.zoom,
          state.height - row.z - Math.cos(row.rotation) * (radius + 9) / state.zoom, row.y);
        context.lineTo(direction[0] - x, direction[1] - y); context.stroke();
      }
      if (active) { context.strokeRect(-radius - 4, -radius - 4, radius * 2 + 8, radius * 2 + 8); }
      context.restore();
      this.hits.push({id: row.id, x, y, bounds: rendered.has(row.id) ? bounds : null});
    }
  }
}
