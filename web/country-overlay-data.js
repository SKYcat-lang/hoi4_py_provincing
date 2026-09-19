'use strict';

// Worker-only map scans. Pan/zoom only reuse the resulting GPU buffer.
function buildCountryOverlayData(mapState, { includeBorders = true, includeColors = true } = {}) {
  const { width, height, pixelBuf } = mapState;
  const countryIds = includeBorders ? new Uint32Array(width * height) : null;
  const fillPixels = includeColors ? new Uint8ClampedArray(width * height * 4) : null;
  const countryIdByTag = new Map();
  const provinceLookup = new Map();
  const missingColors = new Set();
  for (const [rgbKey, provinceId] of mapState.rgbToProvinceId) {
    const owner = mapState.stateById.get(mapState.assignments.get(provinceId))?.owner;
    if (!owner || mapState.seaRgbSet.has(rgbKey) || mapState.lakeRgbSet.has(rgbKey)) continue;
    const [red, green, blue] = rgbKey.split(',').map(Number);
    const packedRgb = (red << 16) | (green << 8) | blue;
    if (packedRgb === 0) continue;
    if (!countryIdByTag.has(owner)) countryIdByTag.set(owner, countryIdByTag.size + 1);
    provinceLookup.set(packedRgb, {
      id: countryIdByTag.get(owner),
      owner,
      color: includeColors ? mapState.countryColors.get(owner) : null,
    });
  }
  for (let pixel = 0; pixel < width * height; pixel++) {
    const offset = pixel * 4;
    const packedRgb = (pixelBuf[offset] << 16) | (pixelBuf[offset + 1] << 8) | pixelBuf[offset + 2];
    const country = provinceLookup.get(packedRgb);
    if (!country) continue;
    if (countryIds) countryIds[pixel] = country.id;
    if (fillPixels) {
      if (!country.color) {
        missingColors.add(country.owner);
        continue;
      }
      fillPixels[offset] = country.color[0];
      fillPixels[offset + 1] = country.color[1];
      fillPixels[offset + 2] = country.color[2];
      fillPixels[offset + 3] = 255;
    }
  }
  let borderSegments = null;
  if (countryIds) {
    const segments = [];
    const verticalStarts = new Int32Array(width + 1).fill(-1);
    // Row-major access avoids the old strided column scan and per-pixel modulo.
    for (let y = 0; y <= height; y++) {
      let horizontalStart = -1;
      const row = y * width;
      for (let x = 0; x <= width; x++) {
        const above = y > 0 && x < width ? countryIds[row - width + x] : 0;
        const below = y < height && x < width ? countryIds[row + x] : 0;
        const horizontalEdge = x < width && above !== below;
        if (horizontalEdge && horizontalStart < 0) horizontalStart = x;
        if (!horizontalEdge && horizontalStart >= 0) {
          segments.push(horizontalStart, y, x, y);
          horizontalStart = -1;
        }
        const left = y < height ? countryIds[row + (x === 0 ? width - 1 : x - 1)] : 0;
        const right = y < height ? countryIds[row + (x === width ? 0 : x)] : 0;
        const verticalEdge = y < height && left !== right;
        if (verticalEdge && verticalStarts[x] < 0) verticalStarts[x] = y;
        if (!verticalEdge && verticalStarts[x] >= 0) {
          segments.push(x, verticalStarts[x], x, y);
          verticalStarts[x] = -1;
        }
      }
    }
    borderSegments = new Float32Array(segments);
  }
  return { fillPixels, borderSegments, missingColors: [...missingColors].sort() };
}
