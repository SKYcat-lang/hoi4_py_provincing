'use strict';

function buildProvinceIdPixels({ width, height, pixelBuf, rgbToProvinceId }) {
  let minimumId = Infinity, maximumId = -Infinity;
  for (const [rgb, id] of rgbToProvinceId) {
    if (id <= 0 || rgb === '0,0,0') continue;
    minimumId = Math.min(minimumId, id);
    maximumId = Math.max(maximumId, id);
  }
  const colorByRgb = new Map();
  for (const [rgb, id] of rgbToProvinceId) {
    if (id <= 0 || rgb === '0,0,0') continue;
    const [red, green, blue] = rgb.split(',').map(Number);
    const ratio = maximumId > minimumId ? (id - minimumId) / (maximumId - minimumId) : 0;
    colorByRgb.set((red << 16) | (green << 8) | blue,
      [Math.round(255 * (1 - ratio)), 0, Math.round(255 * ratio)]);
  }
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    const color = colorByRgb.get((pixelBuf[offset] << 16) | (pixelBuf[offset + 1] << 8) | pixelBuf[offset + 2]);
    // Unregistered colors have no ID yet; leave the underlying map visible.
    if (!color) continue;
    pixels[offset] = color[0];
    pixels[offset + 2] = color[2];
    pixels[offset + 3] = 255;
  }
  return pixels;
}

class ProvinceIdOverlay {
  constructor(canvas, getMapState) {
    this.canvas = canvas;
    this.getMapState = getMapState;
    this.revision = 0;
    this.readyRevision = -1;
    this.pending = false;
    this.worker = null;
  }

  invalidate() { this.revision++; }

  schedule() {
    const map = this.getMapState();
    const toggle = document.querySelector('#layer-province-id-colors-toggle');
    this.canvas.style.display = 'none';
    if (!toggle.checked || !map.loaded || !map.pixelBuf) return;
    if (this.readyRevision === this.revision) {
      this.canvas.style.transform = `translate(${map.panX}px, ${map.panY}px) scale(${map.zoom})`;
      this.canvas.style.display = 'block';
      return;
    }
    if (this.pending) return;
    try {
      if (!this.worker) {
        this.worker = new Worker('map-overlay-worker.js');
        this.worker.onmessage = ({ data }) => {
          this.pending = false;
          if (data.revision !== this.revision) { this.schedule(); return; }
          if (data.error) { this.fail(data.error); return; }
          const current = this.getMapState();
          this.canvas.width = current.width;
          this.canvas.height = current.height;
          this.canvas.getContext('2d').putImageData(new ImageData(data.fillPixels, current.width, current.height), 0, 0);
          this.readyRevision = data.revision;
          this.schedule();
        };
        this.worker.onerror = event => this.fail(event.message);
      }
      this.pending = true;
      this.worker.postMessage({ kind: 'province', revision: this.revision, map: {
        width: map.width, height: map.height, pixelBuf: map.pixelBuf, rgbToProvinceId: map.rgbToProvinceId,
      } }); // Do not transfer the editable source buffer: it must stay attached.
    } catch (error) { this.fail(error.message); }
  }

  fail(message) {
    this.pending = false;
    this.worker?.terminate();
    this.worker = null;
    document.querySelector('#layer-province-id-colors-toggle').checked = false;
    this.canvas.style.display = 'none';
    setStatus(`프로빈스 번호 색상 표시 실패: ${message}`);
  }
}
