const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const source = readFileSync(path.join(__dirname, '../web/building-nudge.js'), 'utf8');

function fixture(fail = false) {
  const element = () => ({value: '0', events: {}, addEventListener(name, callback) { this.events[name] = callback; }, setAttribute() {}});
  let value = 100, shown = value, commits = 0;
  const options = {range: element(), number: element(), minus: element(), plus: element(), read: () => value,
    scale: () => .01, preview: next => { shown = next; }, cancel: () => { shown = value; },
    commit: async next => { commits++; if (!fail) value = next; }};
  const environment = vm.createContext({options});
  vm.runInContext(source, environment);
  const nudge = vm.runInContext('new BuildingNudge(options)', environment);
  return {options, nudge, value: () => value, shown: () => shown, commits: () => commits};
}

test('release recenters without reverting position; repeated gestures accumulate', async () => {
  const {options, nudge, value, shown, commits} = fixture();
  options.range.value = '25'; options.range.events.input();
  assert.equal(shown(), 100.25); assert.equal(value(), 100);
  await nudge.finish();
  assert.equal(value(), 100.25); assert.equal(options.range.value, '0');
  options.range.value = '25'; options.range.events.input(); await nudge.finish();
  assert.equal(value(), 100.5); assert.equal(commits(), 2);
});

test('change and pointerup do not commit the same gesture twice', async () => {
  const {options, nudge, commits} = fixture();
  options.range.value = '10'; options.range.events.input();
  const pending = nudge.finish(); nudge.finish(); await pending;
  assert.equal(commits(), 1);
});

test('cancel restores preview and records no undo operation', () => {
  const {options, shown, commits} = fixture();
  options.range.value = '80'; options.range.events.input(); options.range.events.pointercancel();
  assert.equal(shown(), 100); assert.equal(options.range.value, '0'); assert.equal(commits(), 0);
});

test('rejected movement recenters on the accepted value', async () => {
  const {options, nudge, value} = fixture(true);
  options.range.value = '-100'; options.range.events.input(); await nudge.finish();
  assert.equal(value(), 100); assert.equal(options.number.value, '100.00'); assert.equal(options.range.value, '0');
});

test('direct coordinate input changes the next slider origin', async () => {
  const {options, nudge, value} = fixture();
  await nudge.submit(200);
  options.range.value = '-30'; options.range.events.input(); await nudge.finish();
  assert.equal(value(), 199.7);
});
