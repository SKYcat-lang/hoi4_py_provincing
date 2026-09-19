const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../web/country-overlay-data.js'), 'utf8');

function makeMap() {
  return {
    loaded: true, width: 4, height: 2, zoom: 1, panX: 0, panY: 0,
    pixelBuf: new Uint8ClampedArray([
      1, 0, 0, 255, 2, 0, 0, 255, 3, 0, 0, 255, 4, 0, 0, 255,
      1, 0, 0, 255, 2, 0, 0, 255, 3, 0, 0, 255, 4, 0, 0, 255,
    ]),
    rgbToProvinceId: new Map([['1,0,0', 1], ['2,0,0', 2], ['3,0,0', 3], ['4,0,0', 4]]),
    assignments: new Map([[1, 10], [2, 20], [3, 30], [4, 40]]),
    stateById: new Map([[10, {owner: 'AAA'}], [20, {owner: 'AAA'}], [30, {owner: 'BBB'}], [40, {}]]),
    countryColors: new Map([['AAA', [20, 80, 240]], ['BBB', [20, 80, 240]]]),
    seaRgbSet: new Set(), lakeRgbSet: new Set(),
  };
}

function build(mapState) {
  const environment = vm.createContext({ mapState });
  vm.runInContext(source, environment);
  return vm.runInContext('buildCountryOverlayData(mapState)', environment);
}

function segments(data) {
  const result = [];
  for (let index = 0; index < data.borderSegments.length; index += 4) {
    result.push(Array.from(data.borderSegments.slice(index, index + 4)));
  }
  return result;
}

test('opaque country colors preserve same-owner interiors and same-color country boundaries', () => {
  const mapState = makeMap();
  const originalPixels = mapState.pixelBuf.slice();
  const data = build(mapState);
  assert.deepEqual(Array.from(data.fillPixels.slice(0, 16)), [
    20, 80, 240, 255, 20, 80, 240, 255, 20, 80, 240, 255, 0, 0, 0, 0,
  ]);
  const borders = segments(data);
  assert.ok(borders.some(segment => segment.join() === '2,0,2,2'));
  assert.ok(!borders.some(([x1, , x2]) => x1 === 1 && x2 === 1));
  assert.deepEqual(mapState.pixelBuf, originalPixels);
});

test('water and unassigned provinces are transparent; missing colors keep borders', () => {
  const mapState = makeMap();
  mapState.seaRgbSet.add('1,0,0');
  mapState.lakeRgbSet.add('2,0,0');
  mapState.countryColors.delete('BBB');
  const data = build(mapState);
  assert.ok(data.fillPixels.every(channel => channel === 0));
  assert.deepEqual(Array.from(data.missingColors), ['BBB']);
  assert.ok(segments(data).some(segment => segment.join() === '2,0,2,2'));
});

test('country edits and reassignment recompute borders; world seam joins identical owners', () => {
  const mapState = makeMap();
  mapState.assignments.set(3, 20);
  mapState.stateById.get(40).owner = 'AAA';
  let data = build(mapState);
  assert.ok(segments(data).every(([, y1, , y2]) => y1 === y2));
  mapState.stateById.get(20).owner = 'BBB';
  data = build(mapState);
  assert.ok(segments(data).some(segment => segment.join() === '1,0,1,2'));
});


test('border-only calculation skips the full color bitmap, color-only calculation skips geometry', () => {
  const environment = vm.createContext({map: makeMap()});
  vm.runInContext(source, environment);
  const borders = vm.runInContext('buildCountryOverlayData(map, {includeColors: false})', environment);
  assert.equal(borders.fillPixels, null);
  assert.ok(borders.borderSegments.length > 0);
  const colors = vm.runInContext('buildCountryOverlayData(map, {includeBorders: false})', environment);
  assert.equal(colors.borderSegments, null);
  assert.ok(colors.fillPixels.length > 0);
});

test('country worker requests are coalesced, stale results ignored, and pan reuses GPU geometry', () => {
  const elements = new Map([
    ['#layer-country-border-toggle', {checked: true}],
    ['#layer-country-colors-toggle', {checked: false}],
    ['#layer-country-border-width', {value: '3.5'}],
    ['#country-colors-note', {}],
  ]);
  const map = makeMap();
  const canvas = {style: {}, addEventListener() {}}, fillCanvas = {style: {}};
  let worker, uploads = 0, draws = 0;
  const requests = [];
  const environment = vm.createContext({
    map, canvas, fillCanvas,
    document: {querySelector: selector => elements.get(selector)},
    window: {requestAnimationFrame() {return 1;}},
    Worker: class {constructor() {worker = this;} postMessage(request) {requests.push(request);}},
    CountryBorderRenderer: class {
      upload() {uploads++;}
      render(state, width) {assert.equal(width, 3.5); draws++;}
    },
  });
  vm.runInContext(readFileSync(path.join(__dirname, '../web/country-overlay.js'), 'utf8'), environment);
  const overlay = vm.runInContext('new CountryOverlay(canvas, fillCanvas, () => map)', environment);
  overlay.render();
  overlay.render();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].includeColors, false);
  overlay.invalidate();
  worker.onmessage({data: {revision: 0, borderSegments: new Float32Array(4)}});
  assert.equal(uploads, 0);
  overlay.render();
  assert.equal(requests.length, 2);
  worker.onmessage({data: {revision: 1, borderSegments: new Float32Array(4)}});
  assert.equal(uploads, 1);
  for (let index = 0; index < 10; index++) {
    map.panX = index * 5;
    overlay.render();
  }
  assert.equal(requests.length, 2);
  assert.equal(uploads, 1);
  assert.equal(draws, 10);
  elements.get('#layer-country-border-toggle').checked = false;
  overlay.render();
  assert.equal(canvas.style.display, 'none');
});
