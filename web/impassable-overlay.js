'use strict';

function buildImpassablePixels({ width, height, pixelBuf, rgbToProvinceId,
  assignments, stateById, seaRgbSet, lakeRgbSet }) {
  const blockedColors = new Set();
  for (const [rgb, provinceId] of rgbToProvinceId) {
    if (provinceId <= 0 || rgb === '0,0,0' || seaRgbSet.has(rgb) || lakeRgbSet.has(rgb)) continue;
    if (!stateById.get(assignments.get(provinceId))?.impassable) continue;
    const [red, green, blue] = rgb.split(',').map(Number);
    blockedColors.add((red << 16) | (green << 8) | blue);
  }
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      // Paired dark/light lines remain legible on both terrain and state colors.
      const stripe = (x + y) % 12;
      if (stripe >= 4) continue;
      const offset = (y * width + x) * 4;
      const color = (pixelBuf[offset] << 16) | (pixelBuf[offset + 1] << 8) | pixelBuf[offset + 2];
      if (!blockedColors.has(color)) continue;
      const shade = stripe < 2 ? 30 : 255;
      pixels[offset] = pixels[offset + 1] = pixels[offset + 2] = shade;
      pixels[offset + 3] = stripe < 2 ? 190 : 160;
    }
  }
  return pixels;
}

class ImpassableOverlay {
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
    this.canvas.style.display = 'none';
    if (!document.querySelector('#layer-impassable-toggle').checked || !map.loaded || !map.pixelBuf) return;
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
          try {
            const current = this.getMapState();
            this.canvas.width = current.width;
            this.canvas.height = current.height;
            this.canvas.getContext('2d').putImageData(
              new ImageData(data.fillPixels, current.width, current.height), 0, 0,
            );
            this.readyRevision = data.revision;
            this.schedule();
          } catch (error) { this.fail(error.message); }
        };
        this.worker.onerror = event => this.fail(event.message);
      }
      this.pending = true;
      this.worker.postMessage({ kind: 'impassable', revision: this.revision, map: {
        width: map.width, height: map.height, pixelBuf: map.pixelBuf,
        rgbToProvinceId: map.rgbToProvinceId, assignments: map.assignments,
        stateById: map.stateById, seaRgbSet: map.seaRgbSet, lakeRgbSet: map.lakeRgbSet,
      } }); // Keep the editable source buffer attached to the main thread.
    } catch (error) { this.fail(error.message); }
  }

  fail(message) {
    this.pending = false;
    this.worker?.terminate();
    this.worker = null;
    document.querySelector('#layer-impassable-toggle').checked = false;
    this.canvas.style.display = 'none';
    setStatus(`통행 불가 사선 표시 실패: ${message}`);
  }
}
