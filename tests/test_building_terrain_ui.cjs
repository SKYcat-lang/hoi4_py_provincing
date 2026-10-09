// Uses the disposable building_ui_server fixture. Optional native model JSON is read-only.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {readFileSync} = require('node:fs');
const os = require('node:os');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.pywebview = {api: new Proxy({}, {get: (_, method) => (...args) =>
      fetch(`/api/${method}`, {method: 'POST', body: JSON.stringify(args)}).then(response => response.json())})};
  });
  try {
    await page.goto('http://127.0.0.1:8768/index.html');
    await page.evaluate(() => window.dispatchEvent(new Event('pywebviewready')));
    await page.locator('#btn-open').click();
    await page.waitForFunction(() => state.loaded);
    await page.locator('#tab-buildings').click();
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.size);
    await page.evaluate(() => {
      // Test only in-memory terrain. Do not save this synthetic scene.
      ctx.fillStyle = '#506e50'; ctx.fillRect(0, 0, state.width, state.height);
      state.heightmapValueBuf.fill(110);
      for (let row = 0; row < state.height; row++) for (let column = 0; column < state.width; column++) {
        const distance = Math.hypot((column - 145) / 35, (row - 50) / 25);
        state.heightmapValueBuf[row * state.width + column] = 110 + Math.round(100 * Math.max(0, 1 - distance));
      }
      state.zoom = 10; state.panX = 720 - 140 * 10; state.panY = 436 - 70 * 10;
      buildingEditor.closePopup(); buildingEditor.provinces.clear();
      buildingEditor.view.models.reset();
      buildingEditor.records = new Map([['probe', {id: 'probe', type: 'arms_factory', state: 10,
        province: 2, x: 145, z: 78, y: 9.5, rotation: 0, target: 0}]]);
      buildingEditor.view.updateSelection(); applyTransform();
    });
    await page.waitForFunction(() => buildingEditor.view.models.models.get('arms_factory'));
    const pixels = () => page.evaluate(() => {
      const view = buildingEditor.view; view.draw();
      const layer = view.models, gl = layer.gl;
      const bytes = new Uint8Array(layer.canvas.width * layer.canvas.height * 4);
      gl.readPixels(0, 0, layer.canvas.width, layer.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
      let red = 0;
      for (let index = 0; index < bytes.length; index += 4)
        if (bytes[index] > bytes[index + 1] * 1.5 && bytes[index] > 120) red++;
      return {red, error: gl.getError()};
    });
    assert.deepEqual(await pixels(), {red: 0, error: 0}, 'terrain must hide the underground model');
    await page.evaluate(() => { buildingEditor.records.get('probe').y = 21; buildingEditor.view.schedule(); });
    assert.ok((await pixels()).red > 50, 'model above terrain must be visible');
    const before = await page.evaluate(() => buildingEditor.view.unproject(900, 450));
    const canvasBounds = await page.locator('#building-canvas').boundingBox();
    await page.mouse.move(canvasBounds.x + 900, canvasBounds.y + 450);
    await page.mouse.wheel(0, -100);
    await page.waitForFunction(() => state.zoom > 10);
    const after = await page.evaluate(() => buildingEditor.view.unproject(900, 450));
    assert.ok(Math.hypot(before[0] - after[0], before[1] - after[1]) < .05, 'zoom must stay at the pointed terrain');
    if (process.env.BUILDING_NATIVE_MODELS) {
      const models = JSON.parse(readFileSync(process.env.BUILDING_NATIVE_MODELS, 'utf8'));
      await page.evaluate(models => {
        const view = buildingEditor.view;
        view.models.reset(); view.models.load = async name => models[name];
        buildingEditor.records = new Map(Object.keys(models).map((type, index) => {
          const x = 128 + index * 12, z = 67;
          return [type, {id: type, type, state: 10, province: 2, x, z,
            y: view.camera.terrainHeight(x, z), rotation: -.3, target: 0}];
        }));
        state.zoom = 18; state.panX = 860 - 140 * 18; state.panY = 436 - 61 * 18;
        buildingEditor.renderList(); view.schedule();
      }, models);
      await page.waitForFunction(() => ['arms_factory', 'naval_base_spawn', 'rocket_site'].every(name => buildingEditor.view.models.models.get(name)));
    }
    await page.screenshot({path: process.env.BUILDING_TERRAIN_SCREENSHOT || `${os.tmpdir()}/hoi4-building-terrain.png`});
    assert.equal((await pixels()).error, 0);
    assert.deepEqual(errors, []);
    console.log('PASS: perspective heightfield, terrain occlusion, shared model depth, zoom anchoring, optional native models');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
