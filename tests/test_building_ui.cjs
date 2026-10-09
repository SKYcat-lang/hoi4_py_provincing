// Requires the local fixture server and Playwright (NODE_PATH may point to a bundled runtime).
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

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
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.size > 0);
    assert.equal(await page.locator('#tab-buildings').evaluate(element => element.previousElementSibling.className), 'sep');
    await page.locator('#building-filter').selectOption('arms_factory');
    await page.waitForFunction(() => buildingEditor.view.models.models.get('arms_factory')?.meshes.length > 0);
    assert.equal(await page.evaluate(() => buildingEditor.view.models.gl.getError()), 0);
    assert.equal(await page.evaluate(() => buildingEditor.view.models.models.get('arms_factory').scale), 1.5);
    // A model must actually produce opaque pixels, not merely allocate GPU buffers.
    assert.ok(await page.evaluate(() => {
      buildingEditor.view.draw();
      const layer = buildingEditor.view.models, gl = layer.gl;
      const pixels = new Uint8Array(layer.canvas.width * layer.canvas.height * 4);
      gl.readPixels(0, 0, layer.canvas.width, layer.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      let modelPixels = 0;
      for (let index = 0; index < pixels.length; index += 4)
        if (pixels[index] > pixels[index + 1] * 1.5 && pixels[index] > 120 && pixels[index + 3]) modelPixels++;
      return modelPixels;
    }) > 10);
    // Select a province through the map's actual pointer handler.
    const point = await page.evaluate(() => {
      const rect = buildingEditor.canvas.getBoundingClientRect();
      const [x, y] = buildingEditor.view.project(150, 90);
      return {x: rect.left + x, y: rect.top + y};
    });
    await page.mouse.click(point.x, point.y);
    await page.locator('#building-list button').first().click();
    await page.locator('#building-popup').waitFor({state: 'visible'});
    const xInput = page.locator('[data-building-axis=x] input[type=number]');
    await xInput.fill('141.25'); await xInput.press('Tab');
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.get(buildingEditor.selected).x === 141.25);
    const slider = page.locator('[data-building-axis=x] input[type=range]');
    // Native keyboard input exercises input/change and spring-centering.
    await slider.focus(); await slider.press('ArrowRight');
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.get(buildingEditor.selected).x === 141.26);
    assert.equal(await slider.inputValue(), '0');
    await slider.press('ArrowRight');
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.get(buildingEditor.selected).x === 141.27);
    await page.locator('#building-undo').click();
    await page.waitForFunction(() => !state.historyBusy && buildingEditor.records.get(buildingEditor.selected).x === 141.26);
    await page.locator('#building-redo').click();
    await page.waitForFunction(() => !state.historyBusy && buildingEditor.records.get(buildingEditor.selected).x === 141.27);
    await xInput.fill('500'); await xInput.press('Tab');
    await page.waitForFunction(() => !buildingEditor.busy && document.querySelector('#building-popup-error').textContent.length > 0);
    assert.equal(await xInput.inputValue(), '141.27');
    await page.locator('#building-popup-close').click();
    await page.locator('#building-fill').click();
    await page.waitForFunction(() => !buildingEditor.busy && buildingEditor.records.size === 3);
    await page.locator('#building-fill').click();
    await page.waitForFunction(() => !buildingEditor.busy);
    assert.equal(await page.evaluate(() => buildingEditor.records.size), 3);
    assert.equal(await page.locator('#building-tilt').count(), 0);
    await page.locator('#building-list button').first().click();
    await page.screenshot({path: process.env.BUILDING_UI_SCREENSHOT || `${require('node:os').tmpdir()}/hoi4-building-editor.png`});
    await page.locator('#building-save').click();
    await page.waitForFunction(() => !buildingEditor.busy && !buildingEditor.dirty);
    const saved = await page.evaluate(() => window.pywebview.api.saved_building_text());
    assert.match(saved, /141\.27/); assert.match(saved, /^# test placement/);
    assert.equal(saved.split('\n').filter(row => row.includes(';arms_factory;')).length, 3);
    await page.locator('#tab-province').click();
    assert.equal(await page.locator('#building-canvas').isVisible(), false);
    await page.locator('#tab-buildings').click();
    await page.waitForFunction(() => !buildingEditor.busy);
    assert.equal(await page.evaluate(() => buildingEditor.records.size), 3);
    await page.locator('#building-list button').first().click();
    const sliderBounds = await slider.boundingBox();
    const prior = Number(await xInput.inputValue());
    for (let gesture = 0; gesture < 2; gesture++) {
      await page.mouse.move(sliderBounds.x + sliderBounds.width / 2, sliderBounds.y + sliderBounds.height / 2);
      await page.mouse.down();
      await page.mouse.move(sliderBounds.x + sliderBounds.width * .7, sliderBounds.y + sliderBounds.height / 2, {steps: 5});
      await page.mouse.up();
      await page.waitForFunction(() => !buildingEditor.busy);
      assert.equal(await slider.inputValue(), '0');
    }
    assert.ok(Number(await xInput.inputValue()) > prior);
    await page.locator('#btn-save').click();
    await page.locator('#save-dialog').waitFor({state: 'visible'});
    assert.match(await page.locator('#save-summary').textContent(), /buildings.txt/);
    await page.locator('#btn-save-confirm').click();
    await page.waitForFunction(() => !buildingEditor.busy && !buildingEditor.dirty && buildingEditor.records.size === 3);
    await page.locator('#building-selection-mode').selectOption('state');
    if (await page.locator('#building-popup-close').isVisible()) await page.locator('#building-popup-close').click();
    await page.locator('#building-clear-selection').click();
    // Shift selects terrain even when a randomly generated model covers the point.
    await page.keyboard.down('Shift');
    await page.mouse.click(point.x, point.y);
    await page.keyboard.up('Shift');
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces].sort()), [1, 2]);
    await page.keyboard.down('Shift');
    await page.mouse.click(point.x, point.y);
    await page.keyboard.up('Shift');
    assert.equal(await page.evaluate(() => buildingEditor.provinces.size), 0);

    // Replace every pixel of province 1. Its removed parent must still supply state 10.
    await page.locator('#tab-province').click();
    assert.equal(await page.evaluate(async () => {
      const pixels = [];
      for (let y = 0; y < state.height; y++) for (let x = 0; x < 100; x++) {
        pixels.push([x, y]);
        const offset = (y * state.width + x) * 4;
        state.pixelBuf.set([190, 90, 70], offset);
      }
      ctx.putImageData(state.imageData, 0, 0);
      return (await window.pywebview.api.apply_stroke(pixels, [190, 90, 70], false, false)).ok;
    }), true);
    await page.locator('#tab-buildings').click();
    await page.waitForFunction(() => !buildingEditor.busy);
    assert.doesNotMatch(await page.locator('#building-message').textContent(), /먼저 저장/);
    await page.locator('#btn-save').click();
    await page.locator('#save-dialog').waitFor({state: 'visible'});
    await page.locator('#btn-save-confirm').click();
    await page.waitForFunction(() => !buildingEditor.busy && state.rgbToProvinceId.has('190,90,70'));
    assert.equal(await page.evaluate(() => state.assignments.get(state.rgbToProvinceId.get('190,90,70'))), 10);
    assert.equal(await page.evaluate(() => state.provinceRgbById.has(1)), false);
    await page.locator('#building-selection-mode').selectOption('province');
    const newPoint = await page.evaluate(() => {
      // Put the new province clear of the tools panel.
      state.panX = 350; state.panY = 60; state.zoom = 3; applyTransform();
      const rect = buildingEditor.canvas.getBoundingClientRect();
      const [x, y] = buildingEditor.view.project(50, 60);
      return {x: rect.left + x, y: rect.top + y};
    });
    await page.mouse.click(newPoint.x, newPoint.y);
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces]), [4]);
    await page.locator('#building-filter').selectOption('bunker');
    await page.locator('#building-fill').click();
    await page.waitForFunction(() => !buildingEditor.busy);
    assert.equal(await page.evaluate(() => [...buildingEditor.records.values()].filter(row => row.type === 'bunker' && row.province === 4).length), 1);
    await page.locator('#building-selection-mode').selectOption('state');
    await page.keyboard.down('Shift');
    await page.mouse.click(newPoint.x, newPoint.y);
    await page.keyboard.up('Shift');
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces].sort()), [2, 4]);
    assert.equal(await page.evaluate(async () => {
      const result = await window.pywebview.api.create_state('Second test state', 10);
      if (!result.ok) return false;
      const assigned = await window.pywebview.api.assign_province_to_state(2, result.state.id);
      state.assignments.set(2, result.state.id);
      return assigned.ok;
    }), true);
    await page.mouse.click(newPoint.x, newPoint.y);
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces]), [4]);
    const otherStatePoint = await page.evaluate(() => {
      const rect = buildingEditor.canvas.getBoundingClientRect();
      const [x, y] = buildingEditor.view.project(150, 100);
      return {x: rect.left + x, y: rect.top + y};
    });
    await page.keyboard.down('Shift');
    await page.mouse.click(otherStatePoint.x, otherStatePoint.y);
    await page.keyboard.up('Shift');
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces].sort()), [2, 4]);
    await page.keyboard.down('Shift');
    await page.mouse.click(otherStatePoint.x, otherStatePoint.y);
    await page.keyboard.up('Shift');
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces]), [4]);
    // Unassigned land remains selectable; only generation needs a state owner.
    await page.evaluate(() => { state.assignments.delete(4); });
    await page.locator('#building-selection-mode').selectOption('province');
    await page.mouse.click(newPoint.x, newPoint.y);
    assert.deepEqual(await page.evaluate(() => [...buildingEditor.provinces]), [4]);
    assert.deepEqual(errors, []);
    console.log('PASS: perspective view, model pixels, coordinate editing, undo/redo, save, unsaved entry, repainted province inheritance/selection/generation, state group selection');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
