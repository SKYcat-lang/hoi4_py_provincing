const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const path = require('node:path');

function fixture() {
  const state = {width: 256, height: 128, zoom: 4, panX: -112, panY: 44,
    heightmapValueBuf: new Uint8Array(256 * 128).fill(110)};
  const context = vm.createContext({state});
  vm.runInContext(readFileSync(path.join(__dirname, '../web/building-camera.js'), 'utf8'), context);
  const camera = vm.runInContext('new BuildingCamera(() => state)', context);
  camera.update(800, 600);
  return {state, camera};
}

test('perspective makes the near edge wider than the far edge', () => {
  const {camera} = fixture();
  const widthAt = z => camera.project(150, 11, z)[0] - camera.project(100, 11, z)[0];
  assert.ok(widthAt(20) > widthAt(110));
});

test('terrain picking inverts projection, including slope and viewport edges', () => {
  const {state, camera} = fixture();
  for (let row = 0; row < state.height; row++) for (let column = 0; column < state.width; column++) {
    state.heightmapValueBuf[row * state.width + column] = 100 + Math.round(column / 6 + row / 8);
  }
  camera.update(800, 600);
  for (const [x, z] of [[25, 10], [100, 64], [200, 100], [255.5, .5]]) {
    const projected = camera.project(x, camera.terrainHeight(x, z), z);
    const point = camera.unproject(projected[0], projected[1]);
    assert.ok(Math.abs(point[0] - x) < .0001);
    assert.ok(Math.abs(state.height - point[1] - z) < .0001);
  }
});

test('height sampling uses bottom origin, interpolation and sea minimum', () => {
  const {state, camera} = fixture();
  state.heightmapValueBuf.fill(0);
  assert.equal(camera.terrainHeight(30, 30), 9.5);
  const lastRow = (state.height - 1) * state.width;
  state.heightmapValueBuf[lastRow] = 100;
  state.heightmapValueBuf[lastRow + 1] = 120;
  assert.equal(camera.terrainHeight(.5, .5), 10);
  assert.equal(camera.terrainHeight(1, .5), 11);
  state.heightmapValueBuf = null;
  assert.equal(camera.terrainHeight(1, 1), 9.5);
});

test('height moves model top upward and changes perspective scale', () => {
  const {camera} = fixture();
  const ground = camera.project(140, 11, 64), roof = camera.project(140, 21, 64);
  assert.ok(roof[1] < ground[1]);
  assert.ok(roof[2] < ground[2]);
});
