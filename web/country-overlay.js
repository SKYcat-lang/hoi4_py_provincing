'use strict';

class CountryOverlay {
  constructor(canvas, fillCanvas, getMapState) {
    this.canvas = canvas;
    this.fillCanvas = fillCanvas;
    this.getMapState = getMapState;
    this.revision = 0;
    this.borderRevision = -1;
    this.fillRevision = -1;
    this.frame = 0;
    this.pending = false;
    this.worker = null;
    this.renderer = null;
    this.contextLost = false;
    this.missingColors = [];
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.contextLost = true;
      this.renderer = null;
      this.borderRevision = -1;
      this.canvas.style.display = 'none';
    });
    canvas.addEventListener('webglcontextrestored', () => {
      this.contextLost = false;
      this.schedule();
    });
  }

  invalidate() { this.revision++; }

  schedule() {
    const map = this.getMapState();
    const borders = document.querySelector('#layer-country-border-toggle').checked;
    const colors = document.querySelector('#layer-country-colors-toggle').checked;
    if (!map.loaded || (!borders && !colors)) {
      this.canvas.style.display = 'none';
      this.fillCanvas.style.display = 'none';
      return;
    }
    if (this.frame) return;
    this.frame = window.requestAnimationFrame(() => {
      this.frame = 0;
      this.render();
    });
  }

  requestData(map, includeBorders, includeColors) {
    if (this.pending) return;
    if (!this.worker) {
      this.worker = new Worker('map-overlay-worker.js');
      this.worker.onmessage = ({ data }) => {
        this.pending = false;
        if (data.revision !== this.revision) { this.schedule(); return; }
        if (data.error) { this.fail(data.error); return; }
        try {
          const current = this.getMapState();
          if (data.borderSegments && !this.contextLost) {
            this.renderer ??= new CountryBorderRenderer(this.canvas);
            this.renderer.upload(data.borderSegments);
            this.borderRevision = data.revision;
          }
          if (data.fillPixels) {
            this.fillCanvas.width = current.width;
            this.fillCanvas.height = current.height;
            this.fillCanvas.getContext('2d').putImageData(
              new ImageData(data.fillPixels, current.width, current.height), 0, 0,
            );
            this.missingColors = data.missingColors;
            this.fillRevision = data.revision;
          }
          this.schedule();
        } catch (error) { this.fail(error.message); }
      };
      this.worker.onerror = event => this.fail(event.message);
    }
    this.pending = true;
    this.worker.postMessage({ kind: 'country', revision: this.revision, includeBorders, includeColors, map: {
      width: map.width, height: map.height, pixelBuf: map.pixelBuf,
      rgbToProvinceId: map.rgbToProvinceId, assignments: map.assignments,
      stateById: map.stateById, seaRgbSet: map.seaRgbSet, lakeRgbSet: map.lakeRgbSet,
      countryColors: includeColors ? map.countryColors : new Map(),
    } }); // Preserve source pixels; transfer only computed results back.
  }

  render() {
    const map = this.getMapState();
    const borders = document.querySelector('#layer-country-border-toggle').checked;
    const colors = document.querySelector('#layer-country-colors-toggle').checked;
    this.canvas.style.display = 'none';
    this.fillCanvas.style.display = 'none';
    if ((!borders && !colors) || !map.loaded || !map.pixelBuf) return;
    const note = document.querySelector('#country-colors-note');
    try {
      const needBorders = borders && this.borderRevision !== this.revision;
      const needColors = colors && this.fillRevision !== this.revision;
      if ((needBorders && !this.contextLost) || needColors) {
        this.requestData(map, needBorders && !this.contextLost, needColors);
      }
      note.hidden = !this.pending && (!colors || this.missingColors.length === 0);
      note.textContent = this.pending ? '국가 레이어 계산 중…' :
        `색상 미정의 ${this.missingColors.length}개 국가: 원래 지도 유지`;
      note.title = this.pending ? '' : `common/countries/colors.txt: ${this.missingColors.join(', ')}`;
      if (colors && !needColors) {
        this.fillCanvas.style.display = 'block';
        this.fillCanvas.style.transform = `translate(${map.panX}px, ${map.panY}px) scale(${map.zoom})`;
      }
      if (borders && !needBorders) {
        this.canvas.style.display = 'block';
        this.renderer.render(map, Number(document.querySelector('#layer-country-border-width').value) || 3.5);
      }
    } catch (error) { this.fail(error.message); }
  }

  fail(message) {
    this.pending = false;
    this.worker?.terminate();
    this.worker = null;
    document.querySelector('#layer-country-border-toggle').checked = false;
    document.querySelector('#layer-country-colors-toggle').checked = false;
    this.canvas.style.display = 'none';
    this.fillCanvas.style.display = 'none';
    const note = document.querySelector('#country-colors-note');
    note.hidden = false;
    note.textContent = `국가 레이어 표시 실패: ${message}`;
  }
}
