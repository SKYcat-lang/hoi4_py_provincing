const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../web/province-search.js'), 'utf8');

function fixture(locate = async () => result) {
  const element = () => ({value: '5', hidden: true, disabled: true, style: {}, handlers: {},
    addEventListener(name, handler) { this.handlers[name] = handler; }, blur() { this.blurred = true; }});
  const map = {loaded: true, zoom: 0.5, panX: 0, panY: 0, mode: 'state'};
  const options = {form: element(), input: element(), clearButton: element(), marker: element(),
    feedback: element(),
    viewport: {clientWidth: 1000, clientHeight: 800}, getMapState: () => map, locate,
    transform() {}, report(message) { options.message = message; }};
  const environment = vm.createContext({options});
  vm.runInContext(source, environment);
  const search = vm.runInContext('new ProvinceSearch(options)', environment);
  return {search, options, map};
}
const result = {ok: true, provinceId: 5, x: 50, y: 60, bounds: [45, 55, 55, 65], pixelCount: 40};

test('search centers a province, retains editing mode, and marker follows transforms', async () => {
  const {search, options, map} = fixture();
  await search.search();
  assert.equal(map.mode, 'state');
  assert.equal(map.zoom, 4);
  assert.equal(options.marker.style.left, '400px');
  assert.equal(options.marker.style.top, '400px');
  assert.equal(options.marker.textContent, 'ID 5');
  assert.match(options.feedback.textContent, /프로빈스 ID 5/);
  assert.equal(options.marker.hidden, false);
  map.panX += 20;
  search.render();
  assert.equal(options.marker.style.left, '420px');
  options.clearButton.handlers.click();
  assert.equal(options.marker.hidden, true);
  assert.equal(options.feedback.textContent, '');
});

test('invalid and unloaded requests never call the bridge', async () => {
  const {search, options, map} = fixture(() => {throw new Error('bridge should not be called');});
  for (const value of ['', '0', '-1', '5.1', '1e2', '9007199254740992']) {
    options.input.value = value;
    await search.search();
    assert.match(options.message, /양의 정수/);
  }
  map.loaded = false;
  await search.search();
  assert.match(options.message, /먼저 맵/);
});

test('missing provinces and bridge failures report an error without moving the viewport', async () => {
  for (const locate of [async () => ({ok: false, error: 'ID 없음'}), async () => {throw Error('연결 실패');}]) {
    const {search, options, map} = fixture(locate);
    await search.search();
    assert.match(options.message, /없음|연결 실패/);
    assert.equal(map.panX, 0);
    assert.equal(options.marker.hidden, true);
    assert.equal(options.clearButton.disabled, true);
  }
});

test('clear or a newer search discards stale asynchronous responses', async () => {
  const requests = [];
  const {search, options, map} = fixture(() => new Promise(resolve => requests.push(resolve)));
  const first = search.search();
  const second = search.search();
  requests[1]({...result, provinceId: 8});
  await second;
  requests[0](result);
  await first;
  assert.equal(options.marker.textContent, 'ID 8');
  const third = search.search();
  search.clear(); // Used by geometry edits and map reloads as well as the clear button.
  const previousPan = map.panX;
  requests[2]({...result, x: 1000});
  await third;
  assert.equal(options.marker.hidden, true);
  assert.equal(map.panX, previousPan);
});

test('Enter submits the form and Escape clears the marker without map shortcuts', async () => {
  const {search, options} = fixture();
  let prevented = false, stopped = false;
  options.form.handlers.submit({preventDefault() {prevented = true;}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, true);
  assert.equal(options.marker.hidden, false);
  options.input.handlers.keydown({key: 'Escape', stopPropagation() {stopped = true;}});
  assert.equal(stopped, true);
  assert.equal(options.input.blurred, true);
  assert.equal(search.result, null);
});
