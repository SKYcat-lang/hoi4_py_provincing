const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const source = readFileSync(path.join(__dirname, '../web/impassable-overlay.js'), 'utf8');

function fixture() {
  const width = 24, height = 6;
  const pixelBuf = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) pixelBuf.set([y + 1, 0, 0, 255], (y * width + x) * 4);
  }
  return {
    width, height, pixelBuf, loaded: true, zoom: 2, panX: 3, panY: 4,
    rgbToProvinceId: new Map(Array.from({length: 5}, (_, i) => [`${i + 1},0,0`, i + 1])),
    assignments: new Map([[1, 12], [2, 13], [3, 12], [4, 12]]),
    stateById: new Map([[12, {impassable: true}], [13, {impassable: false}]]),
    seaRgbSet: new Set(['3,0,0']), lakeRgbSet: new Set(['4,0,0']),
  };
}

function build(map) {
  const environment = vm.createContext({map});
  vm.runInContext(source, environment);
  return vm.runInContext('buildImpassablePixels(map)', environment);
}

test('hatching marks only blocked land; normal, water, unassigned and unknown pixels stay transparent', () => {
  const map = fixture(), original = map.pixelBuf.slice();
  const output = build(map);
  assert.equal(output[3], 190);
  assert.equal(output[2 * 4 + 3], 160);
  assert.equal(output[4 * 4 + 3], 0);
  assert.ok(output.slice(map.width * 4).every(value => value === 0));
  assert.deepEqual(map.pixelBuf, original);
});

test('reassignment, disabling and painting change the marked area', () => {
  const map = fixture();
  map.assignments.set(2, 12);
  assert.equal(build(map)[map.width * 4 + 3], 190);
  map.pixelBuf.set([2, 0, 0, 255], 0);
  map.assignments.delete(2);
  assert.equal(build(map)[3], 0);
  map.stateById.get(12).impassable = false;
  assert.ok(build(map).every(value => value === 0));
});

test('async layer rejects stale data, caches pan and zoom, and supports hide/show without editing pixels', () => {
  const map = fixture(), toggle = {checked: true};
  let worker, uploads = 0, requests = 0;
  const canvas = {style: {}, getContext: () => ({putImageData() {uploads++;}})};
  const environment = vm.createContext({
    map, canvas, document: {querySelector: () => toggle}, ImageData: class {},
    Worker: class {constructor() {worker = this;} postMessage() {requests++;}},
  });
  vm.runInContext(source, environment);
  const overlay = vm.runInContext('new ImpassableOverlay(canvas, () => map)', environment);
  overlay.schedule();
  overlay.invalidate();
  worker.onmessage({data: {revision: 0}});
  assert.equal(uploads, 0);
  assert.equal(requests, 2);
  worker.onmessage({data: {revision: 1, fillPixels: build(map)}});
  assert.equal(uploads, 1);
  map.panX = 10; map.zoom = 3;
  overlay.schedule();
  assert.equal(canvas.style.transform, 'translate(10px, 4px) scale(3)');
  assert.equal(requests, 2);
  toggle.checked = false;
  overlay.schedule();
  assert.equal(canvas.style.display, 'none');
  toggle.checked = true;
  overlay.schedule();
  assert.equal(canvas.style.display, 'block');
  assert.equal(requests, 2);
});

test('worker routes impassable requests and transfers the generated buffer', () => {
  const scripts = path.join(__dirname, '../web');
  let response, transferred;
  const environment = vm.createContext({
    self: {postMessage(data, transfers) {response = data; transferred = transfers;}},
    importScripts(...names) {
      for (const name of names) vm.runInContext(readFileSync(path.join(scripts, name), 'utf8'), environment);
    },
  });
  vm.runInContext(readFileSync(path.join(scripts, 'map-overlay-worker.js'), 'utf8'), environment);
  environment.self.onmessage({data: {kind: 'impassable', revision: 4, map: fixture()}});
  assert.equal(response.revision, 4);
  assert.equal(response.fillPixels[3], 190);
  assert.equal(transferred[0], response.fillPixels.buffer);
});
