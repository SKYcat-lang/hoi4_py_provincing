const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(path.join(__dirname, '../web/province-id-colors.js'), 'utf8');

function fixture() {
  return {
    width: 5, height: 1, loaded: true, zoom: 2, panX: 3, panY: 4, mode: 'state',
    pixelBuf: new Uint8ClampedArray([
      1,0,0,255, 2,0,0,255, 3,0,0,255, 4,0,0,255, 0,0,0,255,
    ]),
    rgbToProvinceId: new Map([['0,0,0',0], ['1,0,0',10], ['2,0,0',11], ['3,0,0',110]]),
  };
}

function pixels(map) {
  const environment = vm.createContext({map});
  vm.runInContext(source, environment);
  return Array.from(vm.runInContext('buildProvinceIdPixels(map)', environment));
}

test('gradient uses province ID distances; unregistered and reserved pixels stay transparent', () => {
  const map = fixture();
  const original = map.pixelBuf.slice();
  assert.deepEqual(pixels(map), [255,0,0,255, 252,0,3,255, 0,0,255,255, 0,0,0,0, 0,0,0,0]);
  assert.deepEqual(map.pixelBuf, original);
});

test('newly registered province IDs rescale the gradient', () => {
  const map = fixture();
  map.rgbToProvinceId.set('4,0,0', 210);
  assert.deepEqual(pixels(map).slice(8, 16), [128,0,128,255, 0,0,255,255]);
});

test('empty definitions and a single province have defined output', () => {
  const map = fixture();
  map.rgbToProvinceId.clear();
  assert.ok(pixels(map).every(value => value === 0));
  map.rgbToProvinceId.set('1,0,0', 42);
  assert.deepEqual(pixels(map).slice(0, 4), [255,0,0,255]);
});

test('async overlay keeps the editor mode, reuses pixels on pan, and rejects stale results', () => {
  const map = fixture(), toggle = {checked: true};
  let worker, requests = 0, uploads = 0;
  const canvas = {style: {}, getContext: () => ({putImageData() { uploads++; }})};
  const environment = vm.createContext({
    document: {querySelector: () => toggle},
    Worker: class { constructor() {worker = this;} postMessage() {requests++;} },
    ImageData: class {}, map, canvas,
  });
  vm.runInContext(source, environment);
  const overlay = vm.runInContext('new ProvinceIdOverlay(canvas, () => map)', environment);
  overlay.schedule();
  assert.equal(requests, 1);
  overlay.invalidate();
  worker.onmessage({data: {revision: 0, fillPixels: new Uint8ClampedArray(20)}});
  assert.equal(uploads, 0);
  assert.equal(requests, 2);
  worker.onmessage({data: {revision: 1, fillPixels: new Uint8ClampedArray(20)}});
  assert.equal(uploads, 1);
  map.panX = 30;
  overlay.schedule();
  assert.equal(canvas.style.transform, 'translate(30px, 4px) scale(2)');
  assert.equal(requests, 2);
  assert.equal(map.mode, 'state');
  toggle.checked = false;
  overlay.schedule();
  assert.equal(canvas.style.display, 'none');
});
