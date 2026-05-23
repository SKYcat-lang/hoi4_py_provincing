'use strict';

/* ============================================================
   HOI4 Province Painter - Frontend
   ------------------------------------------------------------
   설계 메모:
   - 메인 캔버스(#map-canvas)는 항상 BMP의 원본 픽셀 크기로 유지한다.
     (왜? 안티앨리어싱과 부동소수점 좌표 오차를 원천 차단하기 위해.)
   - 화면에 보이는 줌/팬은 CSS transform: scale + translate로 처리.
     -> 캔버스 픽셀 데이터는 절대 1:1을 유지하고, 화면 표현만 변형한다.
   - 1×1 픽셀 채색은 ImageData를 직접 수정한다(fillRect는 하위 픽셀로 새는 경우가 있다).
   - 마우스 이동 사이는 Bresenham 라인 알고리즘으로 픽셀 단위 보간.
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

// ---------- 상태 ----------
const state = {
  loaded: false,
  width: 0,
  height: 0,
  imageData: null,         // 프로빈스 편집용 ImageData (편집 대상)
  pixelBuf: null,          // imageData.data (Uint8ClampedArray)
  stateImageData: null,    // 스테이트 맵용 ImageData (캐시, 매핑 변경 시 갱신)
  stateImageDirty: true,   // 다음 탭 진입 시 재구성해야 하는가
  mode: 'province',        // 'province' | 'state' | 'split' (탭)
  splitClickedXY: null,    // 분할 모드에서 마지막으로 클릭한 픽셀
  zoom: 1,
  panX: 0,
  panY: 0,
  currentRgb: [255, 0, 0],
  tool: 'brush',           // 'brush' | 'fill'
  brushDown: false,
  rightDown: false,
  rightDragMoved: false,
  lastPaintX: -1,
  lastPaintY: -1,
  strokeChanges: [],
  strokePixels: [],
  // 한 번의 브러시 스트로크 동안 "잠긴 원본 색".
  // mouseDown 시점의 픽셀 색으로 설정되며, 드래그 중에는 이 색을 가진 픽셀만 칠함.
  // mouseUp에서 null로 리셋.
  strokeLockRgb: null,
  undoStack: [],
  redoStack: [],
  protectLakes: true,
  protectSea: true,
  lakeRgbSet: new Set(),
  seaRgbSet: new Set(),
  states: [],
  stateById: new Map(),       // id -> {id, name, color}
  rgbToProvinceId: new Map(), // "r,g,b" -> province_id
  assignments: new Map(),     // province_id -> state_id
  selectedStateId: null,      // 현재 선택된 스테이트 ID
  regions: [],
  continents: [],
  newProvincesPreview: [],
};

const canvas = $('#map-canvas');
const ctx = canvas.getContext('2d', { willReadFrequently: true });
ctx.imageSmoothingEnabled = false;

const overlayCanvas = $('#overlay-canvas');
const overlayCtx = overlayCanvas.getContext('2d');

const protectCanvas = $('#protect-canvas');
const protectCtx = protectCanvas.getContext('2d', { willReadFrequently: true });
protectCtx.imageSmoothingEnabled = false;

// 레이어 캔버스 (rivers, terrain)
const riversLayerCanvas = $('#rivers-layer-canvas');
const riversLayerCtx = riversLayerCanvas.getContext('2d');
riversLayerCtx.imageSmoothingEnabled = false;

const terrainLayerCanvas = $('#terrain-layer-canvas');
const terrainLayerCtx = terrainLayerCanvas.getContext('2d');
terrainLayerCtx.imageSmoothingEnabled = false;

// X-crossing 좌표 [(x,y), ...]
state.xcrossings = [];

// 보호 오버레이 캐시 무효 플래그
state.protectOverlayDirty = true;

// ---------- 유틸 ----------
function rgbKey(r, g, b) { return `${r},${g},${b}`; }
function rgbToHex([r, g, b]) {
  const h = (n) => n.toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}
function setStatus(msg) { $('#status').textContent = msg; }

function updateZoomLabel() {
  $('#zoom-info').textContent = `${Math.round(state.zoom * 100)}%`;
}
function updateCurrentColorLabel() {
  $('#current-swatch').style.background = rgbToHex(state.currentRgb);
  $('#current-rgb').textContent = rgbToHex(state.currentRgb).toUpperCase();
}

// ---------- 변환 ----------
// ---------- 레이어 (rivers / terrain) ----------
function loadOverlayLayer(layerCanvas, layerCtx, dataUrl) {
  if (!dataUrl) {
    // 데이터 없음 — 캔버스 비움
    layerCtx.clearRect(0, 0, layerCanvas.width, layerCanvas.height);
    return;
  }
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => {
    layerCtx.clearRect(0, 0, layerCanvas.width, layerCanvas.height);
    layerCtx.drawImage(img, 0, 0);
  };
  img.onerror = () => {
    console.warn('레이어 이미지 로드 실패');
  };
  img.src = dataUrl;
}

function applyLayerVisibility(layerCanvas, enabled, opacityPercent) {
  // CSS opacity로 투명도 적용. enabled=false면 0
  const op = enabled ? Math.max(0, Math.min(1, opacityPercent / 100)) : 0;
  layerCanvas.style.opacity = String(op);
  // pointer-events는 항상 none이라 클릭 통과는 보장
}

function applyTransform() {
  const t = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoom})`;
  canvas.style.transformOrigin = '0 0';
  canvas.style.transform = t;
  overlayCanvas.style.transformOrigin = '0 0';
  overlayCanvas.style.transform = t;
  protectCanvas.style.transformOrigin = '0 0';
  protectCanvas.style.transform = t;
  riversLayerCanvas.style.transformOrigin = '0 0';
  riversLayerCanvas.style.transform = t;
  terrainLayerCanvas.style.transformOrigin = '0 0';
  terrainLayerCanvas.style.transform = t;
  updateZoomLabel();
  // SVG 마커는 transform과 별개로 화면 좌표로 다시 계산
  renderMarkers();
}

function screenToPixel(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = (clientX - rect.left) / state.zoom;
  const y = (clientY - rect.top) / state.zoom;
  return [Math.floor(x), Math.floor(y)];
}

// ---------- 픽셀 조작 ----------
function getPixel(x, y) {
  const i = (y * state.width + x) * 4;
  const buf = state.pixelBuf;
  return [buf[i], buf[i + 1], buf[i + 2]];
}

function setPixelRaw(x, y, r, g, b) {
  const i = (y * state.width + x) * 4;
  const buf = state.pixelBuf;
  buf[i] = r;
  buf[i + 1] = g;
  buf[i + 2] = b;
  buf[i + 3] = 255;
}

function paintPixel(x, y) {
  if (x < 0 || y < 0 || x >= state.width || y >= state.height) return;

  const cur = getPixel(x, y);

  // 스트로크 잠금: 시작 시점 색과 같은 픽셀만 허용 (실수로 다른 프로빈스 침범 방지)
  const lock = state.strokeLockRgb;
  if (lock !== null) {
    if (cur[0] !== lock[0] || cur[1] !== lock[1] || cur[2] !== lock[2]) return;
  }

  // 보호 토글 검사
  const key = rgbKey(cur[0], cur[1], cur[2]);
  if (state.protectLakes && state.lakeRgbSet.has(key)) return;
  if (state.protectSea && state.seaRgbSet.has(key)) return;

  const [r, g, b] = state.currentRgb;
  if (cur[0] === r && cur[1] === g && cur[2] === b) return; // 변화 없음

  // 스트로크 변경분 기록 (Undo용에는 첫 변경만 기록)
  state.strokeChanges.push([x, y, cur[0], cur[1], cur[2]]);
  state.strokePixels.push([x, y]);

  setPixelRaw(x, y, r, g, b);
  state.stateImageDirty = true;  // 스테이트 맵 캐시 무효화
}

function paintLine(x0, y0, x1, y1) {
  // Bresenham
  let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  let dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  let x = x0, y = y0;
  // 안전 상한
  let safety = (Math.abs(x1 - x0) + Math.abs(y1 - y0)) * 4 + 8;
  while (safety-- > 0) {
    paintPixel(x, y);
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x += sx; }
    if (e2 <= dx) { err += dx; y += sy; }
  }
}

function flushCanvas() {
  // 변경된 영역만 다시 그릴 수도 있지만, 5632×2048은 transform과 함께 전체
  // putImageData도 빠른 편이라(약 30ms) 일단 단순화한다.
  ctx.putImageData(state.imageData, 0, 0);
}

// ---------- 도구 전환 ----------
function setTool(name) {
  if (name !== 'brush' && name !== 'fill') return;
  state.tool = name;
  $('#btn-tool-brush').classList.toggle('active', name === 'brush');
  $('#btn-tool-fill').classList.toggle('active', name === 'fill');
  canvas.style.cursor = name === 'fill' ? 'cell' : 'crosshair';
  setStatus(name === 'fill' ? '도구: 페인트통 (G)' : '도구: 브러시 (B)');
}

// ---------- 탭/모드 전환 ----------
function setMode(name) {
  if (name !== 'province' && name !== 'state' && name !== 'split' && name !== 'adjacency') return;
  state.mode = name;
  $('#tab-province').classList.toggle('active', name === 'province');
  $('#tab-state').classList.toggle('active', name === 'state');
  $('#tab-split').classList.toggle('active', name === 'split');
  const tabAdj = $('#tab-adjacency');
  if (tabAdj) tabAdj.classList.toggle('active', name === 'adjacency');

  // 모드별 UI 가시성 (hidden 속성으로 통일)
  document.querySelectorAll('.brush-only').forEach(el => {
    el.hidden = (name !== 'province');
  });
  document.querySelectorAll('.state-only').forEach(el => {
    el.hidden = (name !== 'state');
  });
  document.querySelectorAll('.split-only').forEach(el => {
    el.hidden = (name !== 'split');
  });

  // 인접 모드: 하단 입력바 + 목록 패널 노출/숨김
  const adjBar = document.getElementById('adjacency-bar');
  const adjListPanel = document.getElementById('adjacency-list-panel');
  if (adjBar) {
    adjBar.classList.toggle('hidden', name !== 'adjacency');
    adjBar.setAttribute('aria-hidden', name !== 'adjacency' ? 'true' : 'false');
  }
  if (adjListPanel) {
    adjListPanel.classList.toggle('hidden', name !== 'adjacency');
  }
  // 인접 모드 진입 시 영구 선/목록 자동 로드
  if (name === 'adjacency' && window.AdjMode && typeof window.AdjMode.enter === 'function') {
    window.AdjMode.enter();
  }
  // 인접 모드 이탈 시 임시 마커/선 + 오버레이 정리
  if (name !== 'adjacency' && window.AdjMode && typeof window.AdjMode.cancel === 'function') {
    window.AdjMode.cancel();
  }

  if (state.loaded) {
    if (name === 'state') {
      ensureStateImageData();
      ctx.putImageData(state.stateImageData, 0, 0);
      canvas.style.cursor = 'pointer';
      updateSelectedStateLabel();
    } else if (name === 'split') {
      // 분할 모드는 프로빈스 BMP를 그대로 보여줌 (편집은 브러시 안 함)
      ctx.putImageData(state.imageData, 0, 0);
      canvas.style.cursor = 'crosshair';
    } else if (name === 'adjacency') {
      ctx.putImageData(state.imageData, 0, 0);
      canvas.style.cursor = 'crosshair';
    } else {
      ctx.putImageData(state.imageData, 0, 0);
      canvas.style.cursor = state.tool === 'fill' ? 'cell' : 'crosshair';
    }
  }
  const labels = { province: '프로빈스 편집', state: '스테이트 할당', split: '자동 분할', adjacency: '인접 연결' };
  setStatus(`모드: ${labels[name]}`);
}

// ---------- X-crossing 마커 ----------
// 머티리얼 핀 SVG 마커 (줌과 무관한 화면 픽셀 크기)
// 핀 사이즈 32×42 (헤드 직경 22, 끝점이 마커 위치)
const PIN_VIEWBOX_W = 32;
const PIN_VIEWBOX_H = 42;
// SVG path: 위가 둥근 핀, 끝이 뾰족하게 (0, 42)에서 만남
// (16, 42)가 핀의 끝점 (마커 위치)
const PIN_BODY_PATH =
  'M16 0 C 7 0, 0 7, 0 16 C 0 23, 7 30, 16 42 C 25 30, 32 23, 32 16 C 32 7, 25 0, 16 0 Z';

const PIN_GLYPHS = {
  // X-crossing: 큰 X 모양
  xcross: '<path class="pin-glyph" d="M11 11 L21 21 M21 11 L11 21" stroke="#fff" stroke-width="2.4" stroke-linecap="round" fill="none"/>',
  // One-pixel: 느낌표
  onepx: '<path class="pin-glyph" d="M16 8 L16 18 M16 22 L16 23.6" stroke="#fff" stroke-width="2.6" stroke-linecap="round" fill="none"/>',
  // Exclave: 분리된 두 점 (월경지 아이콘)
  exclave: '<path class="pin-glyph" d="M11 13 a3 3 0 1 1 0 0.01 Z M21 19 a3 3 0 1 1 0 0.01 Z" fill="#fff"/>',
};

function _pinSvg(kind) {
  const glyph = PIN_GLYPHS[kind] || '';
  return `<g class="marker-pin kind-${kind}">
    <path class="pin-body" d="${PIN_BODY_PATH}"/>
    ${glyph}
  </g>`;
}

const markerSvg = $('#marker-svg');

function renderMarkers() {
  // 모든 마커를 SVG로 다시 그림. 화면 좌표 = (이미지 좌표 × zoom + pan).
  if (!markerSvg) return;

  const z = state.zoom;
  const px0 = state.panX;
  const py0 = state.panY;

  // 핀 끝점이 마커 위치에 오도록 translate 보정
  // 핀 사이즈는 화면 픽셀 28×38 (가독성 + 클러터 균형)
  const PIN_W = 28;
  const PIN_H = 38;
  const offsetX = -PIN_W / 2;        // 핀 중앙 가로 보정
  const offsetY = -PIN_H;            // 핀 끝점이 마커 좌표에 오도록 상단 띄움

  const parts = [];

  function pushPin(imgX, imgY, kind) {
    const sx = imgX * z + px0;
    const sy = imgY * z + py0;
    // 화면 밖이면 그리지 않음 (성능 + 클러터)
    if (sx < -50 || sy < -50 || sx > markerSvg.clientWidth + 50 || sy > markerSvg.clientHeight + 50) return;
    parts.push(
      `<svg x="${(sx + offsetX).toFixed(1)}" y="${(sy + offsetY).toFixed(1)}" ` +
      `width="${PIN_W}" height="${PIN_H}" viewBox="0 0 ${PIN_VIEWBOX_W} ${PIN_VIEWBOX_H}" ` +
      `style="overflow:visible">${_pinSvg(kind)}</svg>`
    );
  }

  // X-crossing: 좌표는 2x2 좌상단 → 마커 위치는 (x+1, y+1)
  if (state.xcrossings) {
    for (const [x, y] of state.xcrossings) {
      pushPin(x + 1, y + 1, 'xcross');
    }
  }
  // One-pixel
  if (state.onePxCoords) {
    for (const [x, y] of state.onePxCoords) {
      pushPin(x + 0.5, y + 0.5, 'onepx');
    }
  }
  // Exclave: 픽셀 수가 많을 수 있으므로 컴포넌트별 대표 1픽셀만 핀으로 표시.
  // (각 exclave 마다 첫 픽셀)
  if (state.exclaveGroups && state.exclaveGroups.length > 0) {
    for (const grp of state.exclaveGroups) {
      if (grp.pixels && grp.pixels.length > 0) {
        const [px, py] = grp.pixels[0];
        pushPin(px + 0.5, py + 0.5, 'exclave');
      }
    }
  }

  markerSvg.innerHTML = parts.join('');

  // 인접 모드 마커도 같이 동기화 (zoom/pan 변경 시)
  if (window.AdjMode && typeof window.AdjMode.render === 'function') {
    window.AdjMode.render();
  }
}

// 호환성: 기존 호출처가 drawXcrossingOverlay 사용 중이면 SVG 렌더로 위임
function drawXcrossingOverlay() {
  // overlay-canvas 비우기 (이전 잔상 제거)
  if (overlayCanvas && overlayCanvas.width > 0) {
    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  }
  renderMarkers();
}

// ---------- 호수/바다 보호 검정 오버레이 ----------
function refreshProtectOverlay() {
  // 보호 토글 OFF면 오버레이 비우기
  protectCtx.clearRect(0, 0, protectCanvas.width, protectCanvas.height);
  if (!state.loaded) return;
  if (!state.protectLakes && !state.protectSea) return;

  // ImageData 한 번에 만들어 putImageData
  const w = state.width, h = state.height;
  const out = protectCtx.createImageData(w, h);
  const dst = out.data;
  const src = state.pixelBuf;

  // 어떤 RGB 키를 검정으로 표시할지 결정
  const protectKeys = new Set();
  if (state.protectLakes) {
    for (const k of state.lakeRgbSet) protectKeys.add(k);
  }
  if (state.protectSea) {
    for (const k of state.seaRgbSet) protectKeys.add(k);
  }

  if (protectKeys.size === 0) {
    // 결과적으로 보호 RGB가 없으면 transparent 그대로
    return;
  }

  // 캐시: packed RGB int → 검정 여부
  const cache = new Map();
  function isProtected(r, g, b) {
    const key = (r << 16) | (g << 8) | b;
    let v = cache.get(key);
    if (v !== undefined) return v;
    v = protectKeys.has(`${r},${g},${b}`);
    cache.set(key, v);
    return v;
  }

  const total = w * h;
  for (let i = 0; i < total; i++) {
    const si = i * 4;
    if (isProtected(src[si], src[si + 1], src[si + 2])) {
      dst[si] = 0;
      dst[si + 1] = 0;
      dst[si + 2] = 0;
      dst[si + 3] = 255;  // 완전 불투명 검정
    }
    // 그 외는 dst 전부 0 (투명) — createImageData가 이미 0 초기화함
  }
  protectCtx.putImageData(out, 0, 0);
  state.protectOverlayDirty = false;
}

function setXcrossings(coords) {
  state.xcrossings = coords || [];
  const badge = $('#xcount-badge');
  const clearBtn = $('#btn-check-xcross-clear');
  if (badge) {
    if (state.xcrossings.length > 0) {
      badge.hidden = false;
      badge.textContent = state.xcrossings.length.toString();
      badge.classList.add('warn');
      badge.classList.remove('ok');
    } else {
      badge.hidden = true;
    }
  }
  if (clearBtn) clearBtn.hidden = state.xcrossings.length === 0;
  drawXcrossingOverlay();
}

function clearXcrossings() {
  setXcrossings([]);
  setStatus('X-crossing 마커 제거');
}

// ---------- One-pixel province 검사 ----------
state.onePxCoords = [];

function setOnePxMarkers(coords) {
  state.onePxCoords = coords || [];
  const badge = $('#onepx-badge');
  const clearBtn = $('#btn-check-onepx-clear');
  if (badge) {
    if (state.onePxCoords.length > 0) {
      badge.hidden = false;
      badge.textContent = state.onePxCoords.length.toString();
      badge.classList.add('warn');
      badge.classList.remove('ok');
    } else {
      badge.hidden = true;
    }
  }
  if (clearBtn) clearBtn.hidden = state.onePxCoords.length === 0;
  drawXcrossingOverlay();  // 통합 오버레이로 X-crossing+OnePx 함께 갱신
}

function drawOnePxOverlay() {
  // 통합 drawXcrossingOverlay에서 함께 처리하므로 여기선 위임
  drawXcrossingOverlay();
}

async function scanOnePxProvinces() {
  if (!state.loaded) return;
  setStatus('One-pixel 프로빈스 스캔 중...');
  const r = await window.pywebview.api.scan_one_pixel_provinces(1000);
  if (!r || !r.ok) {
    setStatus(`스캔 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  setOnePxMarkers(r.coords);
  if (r.count === 0) {
    setStatus('One-pixel 프로빈스 0건. 깨끗합니다 ✓');
  } else {
    setStatus(`One-pixel 프로빈스 ${r.count}건 발견 (노란 마커). 첫 좌표: ${r.coords[0][0]},${r.coords[0][1]}`);
  }
}

function clearOnePxMarkers() {
  setOnePxMarkers([]);
  setStatus('One-pixel 마커 제거');
}

// ---------- Exclave (월경지) 검사 ----------
state.exclaveGroups = [];   // [{rgb, size, pixels: [[x,y],...]}, ...]

function setExclaveMarkers(exclaves) {
  // exclaves: [{rgb, size, pixels: [[x,y],...]}, ...]
  state.exclaveGroups = exclaves || [];
  const badge = $('#exclave-badge');
  if (badge) {
    if (state.exclaveGroups.length > 0) {
      badge.hidden = false;
      badge.textContent = String(state.exclaveGroups.length);
      badge.classList.add('warn');
      badge.classList.remove('ok');
    } else {
      badge.hidden = true;
    }
  }
  renderMarkers();
}

function clearExclaveMarkers() {
  state.exclaveGroups = [];
  const badge = $('#exclave-badge');
  if (badge) badge.hidden = true;
  renderMarkers();
}

async function scanExclaves() {
  if (!state.loaded) return;
  setStatus('월경지 스캔 중... (전체 BMP BFS, 5~15초 소요 가능)');
  const r = await window.pywebview.api.scan_exclaves(2000);
  if (!r || !r.ok) {
    setStatus(`월경지 스캔 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  setExclaveMarkers(r.exclaves);
  if (r.count === 0) {
    setStatus('월경지 0건. 깨끗합니다 ✓');
  } else {
    setStatus(`월경지 ${r.count}건 (총 ${r.totalPixelMarkers} 픽셀). 빨강 빗금 표시. 캔버스 클릭으로 제거.`);
  }
}

// ---------- rivers.bmp 팔레트 검증/교정 ----------
async function checkRiversPalette() {
  if (!state.loaded) return;
  setStatus('rivers.bmp 팔레트 검사 중...');
  const r = await window.pywebview.api.validate_rivers();
  if (!r || !r.ok) {
    setStatus(`rivers 검사 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  const badge = $('#rivers-status-badge');
  const fixBtn = $('#btn-fix-rivers');
  if (badge) {
    badge.hidden = false;
    if (r.paletteMatches && r.isPalettedBmp) {
      badge.textContent = 'OK';
      badge.classList.remove('warn');
      badge.classList.add('ok');
    } else {
      const issues = (r.invalidIndices ? r.invalidIndices.length : 0)
                   + (r.isPalettedBmp ? 0 : 1);
      badge.textContent = issues > 0 ? `!${issues}` : '!';
      badge.classList.remove('ok');
      badge.classList.add('warn');
    }
  }
  if (fixBtn) fixBtn.hidden = (r.paletteMatches && r.isPalettedBmp);

  // 진단 메시지: 가장 우선 순위 높은 문제부터
  const issues = [];
  if (!r.isPalettedBmp) {
    issues.push(`인덱스 BMP 아님 (mode=${r.mode})`);
  }
  if (r.sizeMatch === false) {
    issues.push(`provinces.bmp와 크기 불일치 (${r.size?.[0]}×${r.size?.[1]} vs ${r.provincesSize?.[0]}×${r.provincesSize?.[1]})`);
  }
  if (r.invalidIndices && r.invalidIndices.length > 0) {
    issues.push(`표준 외 인덱스 ${r.invalidIndices.length}개 (${r.invalidIndices.slice(0,5).join(',')}${r.invalidIndices.length>5?'...':''})`);
  }
  if (r.standardPaletteCheck && r.standardPaletteComplete === false) {
    const missing = r.standardPaletteCheck.filter(e => !e.ok).map(e => e.index);
    issues.push(`표준 팔레트 엔트리 ${missing.length}개 누락/불일치 (idx ${missing.slice(0,8).join(',')}${missing.length>8?'...':''})`);
  }

  if (issues.length === 0) {
    setStatus('rivers.bmp: 모든 검사 통과 ✓ (팔레트, 크기, 표준 엔트리)');
  } else {
    setStatus(`rivers.bmp 문제 ${issues.length}건: ${issues.join(' / ')}. '교정' 버튼으로 자동 수정`);
  }
}

async function fixRiversPalette() {
  if (!state.loaded) return;
  if (!confirm('rivers.bmp 팔레트를 HOI4 표준으로 교정합니다.\n원본은 rivers.bmp.bak로 백업됩니다.\n계속할까요?')) return;
  setStatus('rivers.bmp 교정 중...');
  const r = await window.pywebview.api.fix_rivers();
  if (!r || !r.ok) {
    setStatus(`교정 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  setStatus(`교정 완료: ${r.replacedPixels} 픽셀이 표준 팔레트로 매핑됨${r.backupPath ? ' (백업: ' + r.backupPath + ')' : ''}`);
  // 다시 검사해서 OK 배지로
  await checkRiversPalette();
}

async function scanXcrossingsAll() {
  if (!state.loaded) return;
  setStatus('X-crossing 전체 스캔 중...');
  const r = await window.pywebview.api.scan_xcrossings_all(2000);
  if (!r || !r.ok) {
    setStatus(`스캔 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  setXcrossings(r.coords);
  if (r.count === 0) {
    setStatus('X-crossing 0건. 깨끗합니다 ✓');
  } else if (r.truncated) {
    setStatus(`X-crossing ${r.count}건 (상한 도달 — 더 있을 수 있음). 첫 좌표: ${r.coords[0][0]},${r.coords[0][1]}`);
  } else {
    setStatus(`X-crossing ${r.count}건 발견. 첫 좌표: ${r.coords[0][0]},${r.coords[0][1]}`);
  }
}

async function scanXcrossingsNear(pixels) {
  if (!state.loaded || !pixels || pixels.length === 0) return;
  const r = await window.pywebview.api.scan_xcrossings_near(pixels);
  if (!r || !r.ok) return;
  // 새로 발견된 X-crossing을 기존 리스트에 누적 (중복 제거)
  const existing = new Set(state.xcrossings.map(([x, y]) => `${x},${y}`));
  const merged = state.xcrossings.slice();
  for (const [x, y] of r.coords) {
    const key = `${x},${y}`;
    if (!existing.has(key)) {
      merged.push([x, y]);
      existing.add(key);
    }
  }
  // 변경된 영역에서 사라진 것은 제거 (이전엔 X였는데 그 후 다른 색이 칠해져서 해소된 경우)
  // → 후보 픽셀 주변의 좌상단 좌표를 모아 재검사
  const candidateKeys = new Set();
  for (const p of pixels) {
    for (let dx = -1; dx <= 0; dx++) {
      for (let dy = -1; dy <= 0; dy++) {
        candidateKeys.add(`${p[0]+dx},${p[1]+dy}`);
      }
    }
  }
  const stillValid = new Set(r.coords.map(([x, y]) => `${x},${y}`));
  const filtered = merged.filter(([x, y]) => {
    const key = `${x},${y}`;
    if (candidateKeys.has(key)) {
      // 후보 영역 내라면 stillValid에 있어야만 유지
      return stillValid.has(key);
    }
    return true; // 후보 외 좌표는 그대로
  });
  setXcrossings(filtered);
  if (r.count > 0) {
    setStatus(`X-crossing ${state.xcrossings.length}건 (이번 스트로크에서 ${r.count}건 검출)`);
  }
}

// ---------- 스테이트 맵 렌더링 ----------
function ensureStateImageData() {
  // 픽셀별로 (provinceId → stateId → 색상) 룩업해서 별도 ImageData 구성
  if (!state.stateImageDirty && state.stateImageData) return;

  const w = state.width, h = state.height;
  const out = ctx.createImageData(w, h);
  const dst = out.data;
  const src = state.pixelBuf;
  const sel = state.selectedStateId;

  // 현재까지 본 RGB의 매핑을 캐시 (각 픽셀마다 Map 조회를 피하려고)
  const cache = new Map(); // packed RGB -> [r,g,b]
  const rgbToPid = state.rgbToProvinceId;
  const stateById = state.stateById;
  const assignments = state.assignments;

  // 비선택 스테이트의 어둡기(반투명 검정 오버레이 alpha와 동일 효과)
  // 0.0 = 그대로, 1.0 = 완전 검정. 0.65 정도면 "도드라짐"이 느껴지지만 본래 색은 알아볼 수 있음.
  const DIM_ALPHA = 0.65;

  // 색상 결정 함수 (캐시 키는 packed int)
  function colorFor(r, g, b) {
    const key = (r << 16) | (g << 8) | b;
    let cached = cache.get(key);
    if (cached) return cached;
    let res;
    if (r === 0 && g === 0 && b === 0) {
      // (0,0,0) = invalid 슬롯, 검정 그대로
      res = [0, 0, 0];
    } else {
      const pidKey = `${r},${g},${b}`;
      const pid = rgbToPid.get(pidKey);
      if (pid === undefined) {
        res = [0, 0, 0]; // 등록되지 않은 색 (새 프로빈스 = 미할당)
      } else {
        const sid = assignments.get(pid);
        if (sid === undefined) {
          res = [0, 0, 0]; // 미할당
        } else {
          const s = stateById.get(sid);
          const base = s ? s.color : [128, 128, 128];
          if (sel !== null && sid !== sel) {
            // 비선택 스테이트: 본래 색 위에 반투명 검정 오버레이
            // alpha-over: out = base * (1 - a) + 0 * a = base * (1 - a)
            const k = 1 - DIM_ALPHA;
            res = [
              Math.round(base[0] * k),
              Math.round(base[1] * k),
              Math.round(base[2] * k),
            ];
          } else {
            // 선택됐거나 선택 없음 → 본래 색 그대로
            res = base;
          }
        }
      }
    }
    cache.set(key, res);
    return res;
  }

  // 픽셀 루프
  const total = w * h;
  for (let i = 0; i < total; i++) {
    const si = i * 4;
    const c = colorFor(src[si], src[si + 1], src[si + 2]);
    dst[si] = c[0];
    dst[si + 1] = c[1];
    dst[si + 2] = c[2];
    dst[si + 3] = 255;
  }
  state.stateImageData = out;
  state.stateImageDirty = false;
}

function refreshStateImageIfActive() {
  state.stateImageDirty = true;
  if (state.mode === 'state') {
    ensureStateImageData();
    ctx.putImageData(state.stateImageData, 0, 0);
  }
}

function updateSelectedStateLabel() {
  const sid = state.selectedStateId;
  const swatch = $('#selected-state-swatch');
  const label = $('#selected-state-label');
  if (sid === null) {
    swatch.style.background = 'transparent';
    label.textContent = '없음 (클릭하여 선택)';
    return;
  }
  const s = state.stateById.get(sid);
  if (!s) {
    swatch.style.background = '#000';
    label.textContent = `(알 수 없는 ID ${sid})`;
    return;
  }
  swatch.style.background = rgbToHex(s.color);
  // 이 스테이트에 속한 프로빈스 수 카운트
  let count = 0;
  for (const v of state.assignments.values()) if (v === sid) count++;
  label.textContent = `${s.name} (id ${sid}, ${count}개 프로빈스)`;
}

// ---------- 스테이트 모드 클릭 ----------
async function handleStateModeClick(x, y, shiftKey) {
  // 픽셀에서 province ID와 현재 state ID 조회
  const r = await window.pywebview.api.get_province_id_at_pixel(x, y);
  if (!r || !r.ok) {
    setStatus(`조회 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  const pid = r.provinceId;
  const sid = r.stateId;

  if (!shiftKey) {
    // 일반 클릭: 그 위치의 스테이트를 선택
    if (sid === null || sid === undefined) {
      state.selectedStateId = null;
      setStatus(pid === null ? '클릭한 위치는 정의되지 않은 프로빈스입니다.' : '미할당 프로빈스입니다.');
    } else {
      state.selectedStateId = sid;
      setStatus(`스테이트 선택: id ${sid}`);
    }
    updateSelectedStateLabel();
    refreshStateImageIfActive();
    return;
  }

  // Shift+클릭: 현재 선택된 스테이트에 이 프로빈스 편입
  if (state.selectedStateId === null) {
    setStatus('먼저 스테이트를 선택해주세요 (좌클릭).');
    return;
  }
  if (pid === null || pid === undefined) {
    setStatus('아직 정의되지 않은 프로빈스입니다 (저장 시 새로 등록 후 다시 시도).');
    return;
  }
  if (sid === state.selectedStateId) {
    setStatus('이미 이 스테이트에 속해 있습니다.');
    return;
  }

  // 호수/바다 보호 토글 적용
  const rgbStr = (r.rgb || []).join(',');
  if (state.protectLakes && state.lakeRgbSet.has(rgbStr)) {
    setStatus('호수 보호 토글에 의해 차단됨 (해당 프로빈스는 호수)');
    return;
  }
  if (state.protectSea && state.seaRgbSet.has(rgbStr)) {
    setStatus('바다 보호 토글에 의해 차단됨 (해당 프로빈스는 바다)');
    return;
  }

  const res = await window.pywebview.api.assign_province_to_state(pid, state.selectedStateId);
  if (!res || !res.ok) {
    setStatus(`편입 실패: ${res ? res.error : 'unknown'}`);
    return;
  }
  state.assignments.set(pid, state.selectedStateId);
  setStatus(`프로빈스 ${pid} → 스테이트 ${state.selectedStateId} 편입${res.previousStateId !== null && res.previousStateId !== undefined ? ` (이전: ${res.previousStateId})` : ''}`);
  updateSelectedStateLabel();
  refreshStateImageIfActive();
}

// ---------- 페인트통 ----------
async function performFloodFill(x, y) {
  if (x < 0 || y < 0 || x >= state.width || y >= state.height) return;
  setStatus('페인트통 채우는 중...');
  const result = await window.pywebview.api.flood_fill(
    x, y, state.currentRgb,
    state.protectLakes, state.protectSea,
  );
  if (!result || !result.ok) {
    setStatus(`페인트통 실패: ${result ? result.error : 'unknown'}`);
    return;
  }
  if (result.blockedByProtection) {
    setStatus('보호 토글에 의해 차단됨 (시작 픽셀이 호수/바다)');
    return;
  }
  const changes = result.changedPixels || [];
  if (changes.length === 0) {
    setStatus('변경된 픽셀이 없습니다 (이미 같은 색)');
    return;
  }

  // 프론트엔드 캔버스에 동일 변경 적용
  // 백엔드는 이미 자기 ndarray를 갱신했으므로 픽셀 RGB 정보로 그대로 반영
  const [nr, ng, nb] = state.currentRgb;
  for (const [px, py /*, oldR, oldG, oldB*/] of changes) {
    setPixelRaw(px, py, nr, ng, nb);
  }
  flushCanvas();
  state.stateImageDirty = true;

  // Undo 스택 등록 (기존 브러시와 같은 포맷: [x, y, oldR, oldG, oldB])
  state.undoStack.push({ changes });
  state.redoStack = [];
  updateUndoButtons();

  setStatus(`페인트통 완료: ${changes.length} 픽셀 채움`);

  // 페인트통 결과로도 X-crossing이 생기거나 없어질 수 있음.
  // 변경된 좌표만 후보로 넘김 (changes 첫 두 요소가 x, y).
  const pixelsForScan = changes.map(c => [c[0], c[1]]);
  scanXcrossingsNear(pixelsForScan);
}

// ---------- 입력 처리 ----------
function onMouseDown(e) {
  if (!state.loaded) return;
  const [x, y] = screenToPixel(e.clientX, e.clientY);

  // 월경지 마커가 표시 중이면, 어떤 클릭이든 (좌/우 모두) 마커만 지우고 끝.
  // 일회성 시각 알림이라 "다시 작업하려면 한 번 클릭"이 자연스럽다.
  if (state.exclaveGroups && state.exclaveGroups.length > 0) {
    state.exclaveGroups = [];
    const badge = $('#exclave-badge');
    if (badge) badge.hidden = true;
    renderMarkers();
    setStatus('월경지 마커 제거');
    return;
  }

  if (e.button === 0) {
    // 스테이트 할당 모드 우선 분기
    if (state.mode === 'state') {
      handleStateModeClick(x, y, e.shiftKey);
      return;
    }
    // 자동 분할 모드: 클릭한 픽셀 좌표만 기록 (실제 분할은 [분할] 버튼)
    if (state.mode === 'split') {
      if (x < 0 || y < 0 || x >= state.width || y >= state.height) return;
      state.splitClickedXY = [x, y];
      const [r, g, b] = getPixel(x, y);
      setStatus(`분할 대상 선택: (${x}, ${y}) RGB(${r}, ${g}, ${b}) — '분할' 버튼을 누르세요`);
      return;
    }
    // 인접 연결 모드: 두 번 클릭으로 From/To 선택
    if (state.mode === 'adjacency') {
      if (window.AdjMode && typeof window.AdjMode.handleClick === 'function') {
        window.AdjMode.handleClick(x, y);
      }
      return;
    }

    // 프로빈스 편집 모드: 도구 분기
    const useFill = e.shiftKey || state.tool === 'fill';
    if (useFill) {
      performFloodFill(x, y);
      return;
    }
    state.brushDown = true;
    state.strokeChanges = [];
    state.strokePixels = [];
    state.lastPaintX = x;
    state.lastPaintY = y;

    // 시작 픽셀의 색을 스트로크 잠금색으로 설정.
    // 이후 드래그 동안 같은 색의 픽셀만 칠해진다 → 다른 프로빈스 침범 방지.
    if (x >= 0 && y >= 0 && x < state.width && y < state.height) {
      const start = getPixel(x, y);
      state.strokeLockRgb = [start[0], start[1], start[2]];
    } else {
      state.strokeLockRgb = null;
    }

    paintPixel(x, y);
    flushCanvas();
  } else if (e.button === 2) {
    // 우클릭: 시작 시점에는 아직 스포이드인지 팬인지 모른다.
    state.rightDown = true;
    state.rightDragMoved = false;
    state.rightStart = { x: e.clientX, y: e.clientY, panX: state.panX, panY: state.panY };
  }
}

function onMouseMove(e) {
  if (!state.loaded) return;

  if (state.rightDown) {
    // 일정 거리 이상 움직이면 팬 모드로 전환
    const dx = e.clientX - state.rightStart.x;
    const dy = e.clientY - state.rightStart.y;
    if (state.rightDragMoved || dx * dx + dy * dy > 16) {
      state.rightDragMoved = true;
      state.panX = state.rightStart.panX + dx;
      state.panY = state.rightStart.panY + dy;
      applyTransform();
    }
    return;
  }

  // 커서 정보 + Delete 키 동작용 좌표 추적
  const [px, py] = screenToPixel(e.clientX, e.clientY);
  state.lastCursorXY = [px, py];
  if (px >= 0 && py >= 0 && px < state.width && py < state.height) {
    const [r, g, b] = getPixel(px, py);
    $('#cursor-info').textContent = `(${px}, ${py})  RGB(${r}, ${g}, ${b})`;
  } else {
    $('#cursor-info').textContent = '-';
  }

  if (state.brushDown) {
    paintLine(state.lastPaintX, state.lastPaintY, px, py);
    state.lastPaintX = px;
    state.lastPaintY = py;
    flushCanvas();
  }
}

async function onMouseUp(e) {
  if (!state.loaded) return;

  if (e.button === 0 && state.brushDown) {
    state.brushDown = false;
    const wasLocked = state.strokeLockRgb;
    // 스트로크 종료 → 잠금 해제 (다음 마우스 다운에서 새로 설정됨)
    state.strokeLockRgb = null;

    if (state.strokeChanges.length > 0) {
      // Undo 스택에 한 스트로크로 push
      state.undoStack.push({ changes: state.strokeChanges });
      state.redoStack = [];
      // 백엔드에도 반영 (부모 추적용으로 옛 RGB도 같이 전달)
      try {
        const oldRgbs = state.strokeChanges.map(c => [c[2], c[3], c[4]]);
        await window.pywebview.api.apply_stroke(
          state.strokePixels,
          state.currentRgb,
          state.protectLakes,
          state.protectSea,
          oldRgbs,
        );
      } catch (err) {
        console.error('apply_stroke failed', err);
      }
      updateUndoButtons();
      // 스트로크 종료 직후 국소 X-crossing 검사 (변경된 픽셀 주변)
      scanXcrossingsNear(state.strokePixels);
    } else if (wasLocked) {
      // 변경된 픽셀이 0이면서 잠금이 있었다 = 시작 픽셀이 보호 대상이었거나
      // 새 색이 잠금색과 같았던 경우. 사용자에게 알려준다.
      const key = rgbKey(wasLocked[0], wasLocked[1], wasLocked[2]);
      if (state.protectLakes && state.lakeRgbSet.has(key)) {
        setStatus('호수 보호 토글에 의해 차단됨 (시작 픽셀이 호수)');
      } else if (state.protectSea && state.seaRgbSet.has(key)) {
        setStatus('바다 보호 토글에 의해 차단됨 (시작 픽셀이 바다)');
      }
    }
  } else if (e.button === 2 && state.rightDown) {
    state.rightDown = false;
    if (!state.rightDragMoved) {
      // 인접 모드: 짧은 우클릭 = 선택 취소
      if (state.mode === 'adjacency') {
        if (window.AdjMode && typeof window.AdjMode.cancel === 'function') {
          window.AdjMode.cancel();
        }
        return;
      }
      // 짧은 우클릭 → 스포이드 (다른 모드)
      const [x, y] = screenToPixel(e.clientX, e.clientY);
      const result = await window.pywebview.api.pick_color_at(x, y);
      if (result && result.ok) {
        state.currentRgb = result.rgb;
        updateCurrentColorLabel();
        if (result.province) {
          const p = result.province;
          setStatus(`스포이드: ID ${p.id} / ${p.type} / ${p.terrain} / 대륙 ${p.continent}`);
        } else {
          setStatus(`스포이드: 등록되지 않은 색상`);
        }
      }
    }
  }
}

function onContextMenu(e) {
  e.preventDefault(); // 우클릭 메뉴 차단
}

function onWheel(e) {
  if (!state.loaded) return;
  e.preventDefault();
  // 커서 위치 기준 줌
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const oldZoom = state.zoom;
  const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
  let newZoom = oldZoom * factor;
  newZoom = Math.max(0.05, Math.min(64, newZoom));
  // 줌 변경 후에도 커서 아래 픽셀이 같은 위치에 오게 panX/Y 조정
  state.panX -= (cx / oldZoom) * (newZoom - oldZoom);
  state.panY -= (cy / oldZoom) * (newZoom - oldZoom);
  state.zoom = newZoom;
  applyTransform();
}

function onKeyDown(e) {
  if (!state.loaded) return;
  if (e.ctrlKey && e.key.toLowerCase() === 'z') {
    e.preventDefault();
    undo();
  } else if (e.ctrlKey && (e.key.toLowerCase() === 'y' ||
            (e.shiftKey && e.key.toLowerCase() === 'z'))) {
    e.preventDefault();
    redo();
  } else if (e.ctrlKey && e.key.toLowerCase() === 's') {
    e.preventDefault();
    onSaveClick();
  } else if (!e.ctrlKey && !e.altKey && !e.metaKey) {
    // 입력 필드에 포커스 있을 때는 단축키 무시
    const t = e.target;
    const isTyping = t && (
      t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
      (t.isContentEditable === true)
    );
    if (isTyping) return;

    // 도구 단축키 (Ctrl/Alt/Meta 안 눌렸을 때만, 입력 중 아닐 때만)
    // 모드 전환은 툴바 탭 버튼만 사용 (숫자키는 입력 충돌 위험으로 제거).
    const k = e.key.toLowerCase();
    if (k === 'b' && state.mode === 'province') {
      e.preventDefault();
      setTool('brush');
    } else if (k === 'g' && state.mode === 'province') {
      e.preventDefault();
      setTool('fill');
    } else if (e.key === 'Delete') {
      // 커서 위치 프로빈스를 인접 흡수(=삭제). 19000개 한도 관리용.
      e.preventDefault();
      deleteProvinceUnderCursor();
    }
  }
}

// 커서 위치를 항상 추적해 두면 Delete 키 동작이 자연스럽다.
state.lastCursorXY = null;

async function deleteProvinceUnderCursor() {
  if (!state.loaded) return;
  const xy = state.lastCursorXY;
  if (!xy) {
    setStatus('마우스 커서를 맵 위에 둔 상태에서 Delete 키를 눌러주세요.');
    return;
  }
  const [x, y] = xy;
  if (x < 0 || y < 0 || x >= state.width || y >= state.height) {
    return;
  }
  setStatus('인접 프로빈스로 흡수 중...');
  let r;
  try {
    r = await window.pywebview.api.delete_province_at(
      x, y, state.protectLakes, state.protectSea,
    );
  } catch (err) {
    console.error('delete_province_at failed', err);
    setStatus('삭제 호출 실패');
    return;
  }
  if (!r || !r.ok) {
    setStatus(`삭제 실패: ${r ? r.error : 'unknown'}`);
    return;
  }
  const changes = r.changedPixels || [];
  if (changes.length === 0) {
    setStatus(r.message || '변경된 픽셀이 없습니다.');
    return;
  }
  // 프론트 ImageData에 적용
  for (const c of changes) {
    setPixelRaw(c[0], c[1], c[5], c[6], c[7]);
  }
  flushCanvas();
  // Undo 스택 등록 (changes = [x,y,oR,oG,oB,nR,nG,nB] → 표준 5-튜플로 압축)
  state.undoStack.push({
    changes: changes.map(c => [c[0], c[1], c[2], c[3], c[4]]),
  });
  state.redoStack = [];
  updateUndoButtons();
  // 국소 X-crossing 검사 (경계가 바뀌었으니)
  scanXcrossingsNear(changes.map(c => [c[0], c[1]]));
  // 카운터 갱신
  refreshProvinceCount();
  const into = r.absorbedIntoProvinceId ?? '미배정';
  setStatus(`프로빈스 ID ${r.deletedProvinceId ?? '?'} → ID ${into}에 흡수됨 (${r.pixelCount}px)`);
}

// ---------- 프로빈스 수 카운터 ----------
async function refreshProvinceCount() {
  if (!state.loaded) return;
  try {
    const r = await window.pywebview.api.get_live_province_count();
    if (r && r.ok) {
      updateProvinceCountLabel(r.liveCount);
    }
  } catch (err) {
    console.warn('province count refresh failed', err);
  }
}

function updateProvinceCountLabel(count) {
  const valEl = $('#province-count-value');
  const wrapEl = $('#province-count-label');
  if (!valEl || !wrapEl) return;
  valEl.textContent = String(count);
  wrapEl.classList.remove('warn', 'danger');
  if (count > 20000) {
    wrapEl.classList.add('danger');
  } else if (count > 19000) {
    wrapEl.classList.add('warn');
  }
}

// ---------- Undo/Redo ----------
function undo() {
  const stroke = state.undoStack.pop();
  if (!stroke) return;
  const redoChanges = [];
  for (const [x, y, r, g, b] of stroke.changes) {
    const cur = getPixel(x, y);
    redoChanges.push([x, y, cur[0], cur[1], cur[2]]);
    setPixelRaw(x, y, r, g, b);
  }
  state.redoStack.push({ changes: redoChanges });
  flushCanvas();
  syncPixelGroupToBackend(stroke.changes);
  updateUndoButtons();
  // 마커 갱신
  scanXcrossingsNear(stroke.changes.map(c => [c[0], c[1]]));
}

function redo() {
  const stroke = state.redoStack.pop();
  if (!stroke) return;
  const undoChanges = [];
  for (const [x, y, r, g, b] of stroke.changes) {
    const cur = getPixel(x, y);
    undoChanges.push([x, y, cur[0], cur[1], cur[2]]);
    setPixelRaw(x, y, r, g, b);
  }
  state.undoStack.push({ changes: undoChanges });
  flushCanvas();
  syncPixelGroupToBackend(stroke.changes);
  updateUndoButtons();
  scanXcrossingsNear(stroke.changes.map(c => [c[0], c[1]]));
}

async function syncPixelGroupToBackend(changes) {
  // changes 는 [[x, y, r, g, b], ...] (적용해야 할 RGB)
  // RGB별로 그룹핑해서 apply_stroke 여러 번 호출 (호수/바다 보호는 OFF로 보내야
  // 정확히 되돌릴 수 있다 — undo는 사용자 의도와 무관하게 정확 복원이 우선).
  const byColor = new Map();
  for (const [x, y, r, g, b] of changes) {
    const k = rgbKey(r, g, b);
    if (!byColor.has(k)) byColor.set(k, { rgb: [r, g, b], pixels: [] });
    byColor.get(k).pixels.push([x, y]);
  }
  for (const { rgb, pixels } of byColor.values()) {
    try {
      // Undo/Redo는 부모 카운터에 영향 주지 않도록 track_parents=false
      await window.pywebview.api.apply_stroke(pixels, rgb, false, false, null, false);
    } catch (err) {
      console.error('undo/redo backend sync failed', err);
    }
  }
}

function updateUndoButtons() {
  $('#btn-undo').disabled = state.undoStack.length === 0;
  $('#btn-redo').disabled = state.redoStack.length === 0;
}

// ---------- 초기 로드 ----------
async function onOpenClick() {
  setStatus('폴더 선택 중...');
  const result = await window.pywebview.api.pick_map_folder();
  if (!result || !result.ok) {
    if (result && result.cancelled) {
      setStatus('취소됨');
      return;
    }
    setStatus(`로드 실패: ${result ? result.error : 'unknown'}`);
    return;
  }
  await applyLoadedMap(result);
}

async function applyLoadedMap(result) {
  state.width = result.width;
  state.height = result.height;
  state.states = result.states || [];
  state.regions = result.regions || [];
  state.continents = result.continents || [];

  state.lakeRgbSet = new Set((result.lakeRgbs || []).map(([r, g, b]) => rgbKey(r, g, b)));
  state.seaRgbSet = new Set((result.seaRgbs || []).map(([r, g, b]) => rgbKey(r, g, b)));

  // 스테이트 색상/매핑 룩업 테이블
  state.stateById = new Map();
  for (const s of state.states) {
    state.stateById.set(s.id, s);
  }
  state.rgbToProvinceId = new Map();
  for (const [rgbArr, pid] of (result.provinceRgbToId || [])) {
    state.rgbToProvinceId.set(`${rgbArr[0]},${rgbArr[1]},${rgbArr[2]}`, pid);
  }
  state.assignments = new Map();
  for (const [pid, sid] of (result.assignments || [])) {
    state.assignments.set(pid, sid);
  }
  state.selectedStateId = null;
  state.stateImageDirty = true;

  canvas.width = state.width;
  canvas.height = state.height;
  overlayCanvas.width = state.width;
  overlayCanvas.height = state.height;
  protectCanvas.width = state.width;
  protectCanvas.height = state.height;
  riversLayerCanvas.width = state.width;
  riversLayerCanvas.height = state.height;
  terrainLayerCanvas.width = state.width;
  terrainLayerCanvas.height = state.height;

  // 레이어 BMP들을 비동기 로드 (실패해도 본체에 영향 없음)
  loadOverlayLayer(riversLayerCanvas, riversLayerCtx, result.riversImageDataUrl);
  loadOverlayLayer(terrainLayerCanvas, terrainLayerCtx, result.terrainImageDataUrl);

  // 이미지 로드 → ImageData로
  const img = new Image();
  img.decoding = 'sync';
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = result.imageDataUrl;
  });
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0);
  state.imageData = ctx.getImageData(0, 0, state.width, state.height);
  state.pixelBuf = state.imageData.data;

  // 초기 줌: 캔버스 영역에 맞게 fit
  const wrap = $('#canvas-area');
  const fitX = wrap.clientWidth / state.width;
  const fitY = wrap.clientHeight / state.height;
  state.zoom = Math.min(fitX, fitY) * 0.95;
  state.panX = (wrap.clientWidth - state.width * state.zoom) / 2;
  state.panY = (wrap.clientHeight - state.height * state.zoom) / 2;
  applyTransform();

  $('#welcome').classList.add('hidden');
  state.loaded = true;
  state.undoStack = [];
  state.redoStack = [];
  state.stateImageDirty = true;
  setXcrossings([]);  // 마커 초기화
  setOnePxMarkers([]);
  clearExclaveMarkers();
  // rivers/onepx 배지도 초기화
  const riversBadge = $('#rivers-status-badge');
  const fixBtn = $('#btn-fix-rivers');
  if (riversBadge) riversBadge.hidden = true;
  if (fixBtn) fixBtn.hidden = true;
  refreshProtectOverlay();
  updateUndoButtons();
  updateSelectedStateLabel();

  setStatus(`로드 완료: ${result.provinceCount}개 프로빈스 / ${result.width}×${result.height} / 스테이트 ${state.states.length}개`);
  // 카운터 초기 표시
  updateProvinceCountLabel(result.provinceCount);
  refreshProvinceCount();  // 더 정확한 라이브 카운트로 덮어쓰기
}

// ---------- 자동 분할 ----------
async function runAutoSplit() {
  if (!state.loaded) {
    setStatus('맵을 먼저 로드해주세요.');
    return;
  }
  if (!state.splitClickedXY) {
    setStatus('먼저 분할할 프로빈스를 클릭해주세요.');
    return;
  }
  const avgInput = $('#split-avg-input');
  const minInput = $('#split-min-input');
  const noiseInput = $('#split-noise-input');
  const avgPx = parseInt(avgInput.value, 10);
  if (!avgPx || avgPx < 1) {
    setStatus('평균 px는 1 이상이어야 합니다.');
    return;
  }
  let minPx = null;
  if (minInput.value && minInput.value.trim() !== '') {
    minPx = parseInt(minInput.value, 10);
    if (isNaN(minPx) || minPx < 0) minPx = null;
  }
  // 노이즈 강도: 슬라이더 값(0~100) → 0~1
  let noiseStrength = 0.5;
  if (noiseInput) {
    const v = parseInt(noiseInput.value, 10);
    if (!isNaN(v)) noiseStrength = Math.max(0, Math.min(1, v / 100));
  }

  const [x, y] = state.splitClickedXY;
  setStatus(`분할 중... (영역이 크면 1~3초 소요, 노이즈 ${Math.round(noiseStrength*100)}%)`);
  const r = await window.pywebview.api.split_province_at(
    x, y, avgPx, minPx, noiseStrength,
    state.protectLakes, state.protectSea,
  );
  if (!r || !r.ok) {
    setStatus(`분할 실패: ${r ? r.error : 'unknown'}`);
    if (r && r.blockedByProtection) {
      // 보호 차단은 alert 띄울 만큼은 아니지만 명확히 안내
      // (이미 setStatus로 충분)
    } else if (r && r.error) {
      alert(r.error);
    }
    return;
  }

  const changes = r.changedPixels || [];
  if (changes.length === 0) {
    setStatus(r.message || '변경된 픽셀이 없습니다.');
    return;
  }

  // 프론트엔드 ImageData에 적용
  for (const c of changes) {
    setPixelRaw(c[0], c[1], c[5], c[6], c[7]);
  }
  flushCanvas();
  state.stateImageDirty = true;

  // Undo 등록 (브러시 포맷)
  const undoChanges = changes.map(c => [c[0], c[1], c[2], c[3], c[4]]);
  state.undoStack.push({ changes: undoChanges });
  state.redoStack = [];
  updateUndoButtons();

  setStatus(
    `분할 완료: ${r.splitCount}개 조각 / 시드 ${r.seedCount} / ` +
    `병합 ${r.mergedCount} / 변경 ${changes.length} 픽셀 / 최소 ${r.minPixels}px`
  );

  // X-crossing 검사 (변경 영역만)
  const pixelsForScan = changes.map(c => [c[0], c[1]]);
  scanXcrossingsNear(pixelsForScan);
}

// ---------- BMP 덮어쓰기 ----------
async function onOverlayBmpClick() {
  if (!state.loaded) {
    setStatus('맵을 먼저 로드해주세요.');
    return;
  }
  const proceed = confirm(
    '외부 BMP를 불러와 현재 맵에 덮어씁니다.\n' +
    '이 작업은 "한 번의 큰 붓질"로 기록되어 Ctrl+Z로 되돌릴 수 있습니다.\n' +
    '계속할까요?'
  );
  if (!proceed) return;

  setStatus('BMP 선택 다이얼로그 열림...');
  const r = await window.pywebview.api.pick_overlay_bmp();
  if (!r) {
    setStatus('덮어쓰기 실패');
    return;
  }
  if (r.cancelled) {
    setStatus('취소됨');
    return;
  }
  if (!r.ok) {
    setStatus(`덮어쓰기 실패: ${r.error}`);
    alert(r.error);
    return;
  }
  if (r.changedCount === 0) {
    setStatus('차이 없음 — 동일한 BMP입니다.');
    return;
  }

  // changes: [[x, y, oR, oG, oB, nR, nG, nB], ...]
  // 프론트엔드 ImageData에 적용
  for (const c of r.changes) {
    setPixelRaw(c[0], c[1], c[5], c[6], c[7]);
  }
  flushCanvas();
  state.stateImageDirty = true;

  // Undo 스택에 한 번에 등록 (브러시와 같은 [x,y,oR,oG,oB] 포맷)
  const undoChanges = r.changes.map(c => [c[0], c[1], c[2], c[3], c[4]]);
  state.undoStack.push({ changes: undoChanges });
  state.redoStack = [];
  updateUndoButtons();

  // 새로 등장한 RGB의 set을 룩업에 추가 (저장하기 전엔 ID가 없으므로 미할당으로 표시됨)
  // (특별히 추가 작업 안 해도 다음 저장 시 새 프로빈스로 식별됨)

  // 보호 오버레이 갱신 (BMP 변경으로 보호 RGB 위치도 바뀜)
  refreshProtectOverlay();

  // X-crossing 전체 스캔 권장 (변경 영역이 매우 클 수 있음)
  setStatus(`BMP 덮어씀: ${r.changedCount} 픽셀 변경 / X 검사 진행 중...`);
  await scanXcrossingsAll();
}

// ---------- 새 색상 ----------
async function onPickColorClick() {
  const r = await window.pywebview.api.pick_new_color();
  if (r && r.ok) {
    state.currentRgb = r.rgb;
    updateCurrentColorLabel();
    setStatus(`새 색상 발급: ${rgbToHex(r.rgb).toUpperCase()}`);
  }
}

// ---------- 저장 ----------
async function onSaveClick() {
  if (!state.loaded) return;
  setStatus('저장 미리보기 분석 중...');
  const preview = await window.pywebview.api.preview_save();
  if (!preview || !preview.ok) {
    setStatus(`저장 실패: ${preview ? preview.error : 'unknown'}`);
    return;
  }

  state.newProvincesPreview = preview.newProvinces || [];

  // 다이얼로그 채우기
  const summary = `새 프로빈스 ${preview.newProvinces.length}개 추가, 사라진 프로빈스 ${preview.removedProvinces.length}개. 기존 프로빈스의 스테이트 매핑은 [스테이트 할당] 탭에서 미리 지정한 내용이 사용됩니다.`;
  $('#save-summary').textContent = summary;
  const tbody = $('#save-table tbody');
  tbody.innerHTML = '';

  const stateOptions = [`<option value="">(미할당)</option>`].concat(
    state.states.map(s => `<option value="${s.id}">${s.id} - ${s.name}</option>`)
  ).join('');

  for (const p of preview.newProvinces) {
    const tr = document.createElement('tr');
    const colorHex = rgbToHex(p.rgb);
    tr.innerHTML = `
      <td class="color-cell">
        <span class="swatch" style="background:${colorHex}"></span>
        <span class="rgb-text">${colorHex.toUpperCase()}</span>
      </td>
      <td>${p.id}</td>
      <td>
        <select data-rgb="${p.rgb.join(',')}" class="type-select">
          <option value="land" ${p.type==='land'?'selected':''}>land</option>
          <option value="sea" ${p.type==='sea'?'selected':''}>sea</option>
          <option value="lake" ${p.type==='lake'?'selected':''}>lake</option>
        </select>
      </td>
      <td>${p.terrain}</td>
      <td>${p.continent}</td>
      <td>${p.coastal ? '예' : '아니오'}</td>
      <td>
        <select data-rgb="${p.rgb.join(',')}" class="state-select">${stateOptions}</select>
      </td>
    `;
    tbody.appendChild(tr);
  }

  $('#save-dialog').classList.remove('hidden');
  setStatus(`저장 미리보기: ${summary}`);
}

async function onSaveConfirm() {
  const typeOverrides = {};
  document.querySelectorAll('.type-select').forEach(el => {
    typeOverrides[el.dataset.rgb] = el.value;
  });
  const stateAssignments = {};
  document.querySelectorAll('.state-select').forEach(el => {
    if (el.value) stateAssignments[el.dataset.rgb] = parseInt(el.value, 10);
  });

  setStatus('저장 중...');
  const r = await window.pywebview.api.commit_save(typeOverrides, stateAssignments);
  $('#save-dialog').classList.add('hidden');
  if (r && r.ok) {
    const extraExt = (r.modifiedExternalFiles && r.modifiedExternalFiles.length) || 0;
    let msg = `저장 완료: 새 ${r.newProvinceCount}개 / 삭제 ${r.removedProvinceCount}개 / state ${r.modifiedStateFiles.length} / region ${r.modifiedRegionFiles.length} / 외부 파일 ${extraExt}`;
    setStatus(msg);
    // 카운터 갱신 (백엔드가 정확한 라이브 카운트를 반환)
    if (typeof r.liveProvinceCount === 'number') {
      updateProvinceCountLabel(r.liveProvinceCount);
    }
    // 보호 색상/룩업/할당 갱신
    for (const p of state.newProvincesPreview) {
      const rgbStr = p.rgb.join(',');
      const t = (typeOverrides[rgbStr]) || p.type;
      const k = rgbKey(p.rgb[0], p.rgb[1], p.rgb[2]);
      if (t === 'lake') state.lakeRgbSet.add(k);
      if (t === 'sea') state.seaRgbSet.add(k);
      // 새 프로빈스의 RGB → ID 매핑 추가
      state.rgbToProvinceId.set(rgbStr, p.id);
      // 다이얼로그에서 지정한 스테이트가 있으면 매핑 등록
      const sidStr = stateAssignments[rgbStr];
      if (sidStr !== undefined && sidStr !== null && sidStr !== '') {
        state.assignments.set(p.id, parseInt(sidStr, 10));
      }
    }
    // 스테이트 맵 캐시 무효화 (탭 다시 들어가면 재구성)
    state.stateImageDirty = true;
    if (state.mode === 'state') {
      ensureStateImageData();
      ctx.putImageData(state.stateImageData, 0, 0);
    }
    updateSelectedStateLabel();
    // 새 lake/sea 추가 가능 → 보호 오버레이 갱신
    refreshProtectOverlay();

    // 저장 시 전체 X-crossing 스캔 결과 반영
    if (r.xcrossings) {
      setXcrossings(r.xcrossings);
      if (r.xcrossingCount > 0) {
        setStatus(`저장됨, 단 X-crossing ${r.xcrossingCount}건 발견 — 빨간 마커 위치를 수정해주세요`);
      }
    }
  } else {
    setStatus(`저장 실패: ${r ? r.error : 'unknown'}`);
  }
}

function onSaveCancel() {
  $('#save-dialog').classList.add('hidden');
  setStatus('저장 취소');
}

// ---------- 이벤트 등록 ----------
window.addEventListener('pywebviewready', () => {
  $('#btn-open').addEventListener('click', onOpenClick);
  $('#btn-overlay-bmp').addEventListener('click', onOverlayBmpClick);
  $('#btn-pick-color').addEventListener('click', onPickColorClick);
  $('#btn-undo').addEventListener('click', undo);
  $('#btn-redo').addEventListener('click', redo);
  $('#btn-save').addEventListener('click', onSaveClick);
  $('#btn-save-confirm').addEventListener('click', onSaveConfirm);
  $('#btn-save-cancel').addEventListener('click', onSaveCancel);

  $('#btn-tool-brush').addEventListener('click', () => setTool('brush'));
  $('#btn-tool-fill').addEventListener('click', () => setTool('fill'));

  $('#tab-province').addEventListener('click', () => setMode('province'));
  $('#tab-state').addEventListener('click', () => setMode('state'));
  $('#tab-split').addEventListener('click', () => setMode('split'));
  const tabAdjEl = document.getElementById('tab-adjacency');
  if (tabAdjEl) tabAdjEl.addEventListener('click', () => setMode('adjacency'));
  $('#btn-split-run').addEventListener('click', runAutoSplit);
  const noiseSlider = $('#split-noise-input');
  const noiseReadout = $('#split-noise-readout');
  if (noiseSlider && noiseReadout) {
    noiseSlider.addEventListener('input', () => {
      noiseReadout.textContent = `${noiseSlider.value}%`;
    });
  }

  // 검증 패널 버튼들
  $('#btn-check-xcross').addEventListener('click', scanXcrossingsAll);
  $('#btn-check-xcross-clear').addEventListener('click', clearXcrossings);
  $('#btn-check-onepx').addEventListener('click', scanOnePxProvinces);
  $('#btn-check-onepx-clear').addEventListener('click', clearOnePxMarkers);
  $('#btn-check-exclave').addEventListener('click', scanExclaves);
  $('#btn-check-rivers').addEventListener('click', checkRiversPalette);
  $('#btn-fix-rivers').addEventListener('click', fixRiversPalette);

  // 검증 패널 접기/펼치기
  const checkPanel = $('#check-panel');
  const checkPanelToggle = $('#check-panel-toggle');
  if (checkPanelToggle) {
    checkPanelToggle.addEventListener('click', () => {
      const collapsed = checkPanel.classList.toggle('collapsed');
      checkPanelToggle.textContent = collapsed ? '+' : '−';
      checkPanelToggle.title = collapsed ? '패널 펼치기' : '패널 접기';
    });
  }

  // 기능 패널 접기/펼치기
  const toolPanel = $('#tool-panel');
  const toolPanelToggle = $('#tool-panel-toggle');
  if (toolPanelToggle) {
    toolPanelToggle.addEventListener('click', () => {
      const collapsed = toolPanel.classList.toggle('collapsed');
      toolPanelToggle.textContent = collapsed ? '+' : '−';
      toolPanelToggle.title = collapsed ? '패널 펼치기' : '패널 접기';
    });
  }

  // 레이어 컨트롤
  const riversToggle = $('#layer-rivers-toggle');
  const riversOpacity = $('#layer-rivers-opacity');
  const riversReadout = $('#layer-rivers-opacity-readout');
  const terrainToggle = $('#layer-terrain-toggle');
  const terrainOpacity = $('#layer-terrain-opacity');
  const terrainReadout = $('#layer-terrain-opacity-readout');

  function refreshRivers() {
    applyLayerVisibility(riversLayerCanvas,
                         riversToggle.checked,
                         parseInt(riversOpacity.value, 10));
    riversReadout.textContent = `${riversOpacity.value}%`;
  }
  function refreshTerrain() {
    applyLayerVisibility(terrainLayerCanvas,
                         terrainToggle.checked,
                         parseInt(terrainOpacity.value, 10));
    terrainReadout.textContent = `${terrainOpacity.value}%`;
  }
  riversToggle.addEventListener('change', refreshRivers);
  riversOpacity.addEventListener('input', refreshRivers);
  terrainToggle.addEventListener('change', refreshTerrain);
  terrainOpacity.addEventListener('input', refreshTerrain);
  refreshRivers();
  refreshTerrain();

  // 레이어 패널 접기/펼치기
  const layerPanel = $('#layer-panel');
  const layerPanelToggle = $('#layer-panel-toggle');
  layerPanelToggle.addEventListener('click', () => {
    const collapsed = layerPanel.classList.toggle('collapsed');
    layerPanelToggle.textContent = collapsed ? '+' : '−';
    layerPanelToggle.title = collapsed ? '패널 펼치기' : '패널 접기';
  });

  $('#toggle-protect-lake').addEventListener('change', (e) => {
    state.protectLakes = e.target.checked;
    refreshProtectOverlay();
  });
  $('#toggle-protect-sea').addEventListener('change', (e) => {
    state.protectSea = e.target.checked;
    refreshProtectOverlay();
  });

  canvas.addEventListener('mousedown', onMouseDown);
  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
  canvas.addEventListener('contextmenu', onContextMenu);
  $('#canvas-area').addEventListener('wheel', onWheel, { passive: false });
  window.addEventListener('keydown', onKeyDown);

  updateCurrentColorLabel();
  updateUndoButtons();
});

// =====================================================================
// 최소침습 ID 병합 (감독자 모드)
// =====================================================================
// 흐름:
//   1) "ID 갭 병합" 버튼 → scan_placeholder_ids() → 매핑 미리보기
//   2) 매핑된 ID들로 search_id_usages() → 외부 파일 매치 리스트
//   3) 사용자가 각 매치별 Yes/No 결정 (기본 Yes)
//   4) "드라이런" 또는 "실행" 클릭 → apply_min_invasive_compaction()
//
// 자동화 금지 원칙: 매치별 결정은 반드시 사용자 손을 거친다.
(function setupCompactSupervisor() {
  document.addEventListener('DOMContentLoaded', () => {
    const $ = (sel) => document.querySelector(sel);
    const scanBtn = $('#btn-compact-scan');
    const dialog = $('#compact-dialog');
    const cancelBtn = $('#btn-compact-cancel');
    const dryRunBtn = $('#btn-compact-dry-run');
    const executeBtn = $('#btn-compact-execute');
    const yesCountLabel = $('#compact-yes-count');
    if (!scanBtn) return;  // 패널이 없으면 작동 안 함 (방어)

    // 현재 다이얼로그가 들고 있는 상태
    let currentPlan = null;       // { idMap, changedIdMap, removedIds, ... }
    let currentMatches = [];      // [{filePath, relPath, lineNo, lineText, matchedId, colStart, colEnd}, ...]
    let decisions = [];           // matches와 같은 길이의 Boolean (true=Yes)

    function showDialog() { dialog.classList.remove('hidden'); }
    function hideDialog() { dialog.classList.add('hidden'); }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;',
        '"': '&quot;', "'": '&#39;',
      }[c]));
    }

    function updateYesCount() {
      const yes = decisions.filter(Boolean).length;
      yesCountLabel.textContent = `선택: ${yes} / ${decisions.length}`;
    }

    function renderMapTable(plan) {
      const wrap = $('#compact-map-scroll');
      const changed = plan.changedIdMap || {};
      const keys = Object.keys(changed).map(k => parseInt(k, 10)).sort((a, b) => a - b);
      if (keys.length === 0) {
        wrap.innerHTML = '<p style="color: var(--muted); font-size: 12px; padding: 12px;">매핑할 ID가 없습니다 (placeholder만 잘려나갑니다).</p>';
        return;
      }
      const rows = keys.map(oldId => {
        const newId = changed[oldId];
        return `<tr><td>${oldId}</td><td style="color: var(--muted);">→</td><td><b style="color: var(--accent);">${newId}</b></td></tr>`;
      }).join('');
      wrap.innerHTML = `
        <table class="compact-map-table">
          <thead><tr><th>옛 ID</th><th></th><th>새 ID</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    function renderMatches() {
      const wrap = $('#compact-matches-wrap');
      if (!currentMatches.length) {
        wrap.innerHTML = '<p style="padding:12px; color: var(--muted); font-size:12px;">외부 파일에서 매칭된 ID가 없습니다. 그대로 실행해도 안전합니다.</p>';
        updateYesCount();
        return;
      }
      // 파일별로 그룹화 (백엔드가 이미 정렬해서 줌). 각 파일별로 인덱스 목록도 같이 저장.
      const changed = currentPlan?.changedIdMap || {};
      const groups = [];  // [{relPath, indices: [globalIdx, ...]}]
      let cur = null;
      currentMatches.forEach((m, idx) => {
        if (!cur || cur.relPath !== m.relPath) {
          cur = { relPath: m.relPath, indices: [] };
          groups.push(cur);
        }
        cur.indices.push(idx);
      });

      const html = [];
      groups.forEach((g) => {
        const total = g.indices.length;
        const yesInFile = g.indices.filter(i => decisions[i]).length;
        const allYes = yesInFile === total;
        const allNo = yesInFile === 0;
        html.push(`<div class="compact-file-group">`);
        html.push(`
          <div class="compact-match-file">
            <span>📄</span>
            <span class="file-name" title="${escapeHtml(g.relPath)}">${escapeHtml(g.relPath)}</span>
            <span class="file-count">${yesInFile} / ${total}</span>
            <span class="file-bulk">
              <button class="${allYes ? 'active yes' : ''}" data-file-action="yes" data-file="${escapeHtml(g.relPath)}">파일 Yes</button>
              <button class="${allNo ? 'active no' : ''}" data-file-action="no" data-file="${escapeHtml(g.relPath)}">파일 No</button>
            </span>
          </div>`);
        g.indices.forEach((idx) => {
          const m = currentMatches[idx];
          const before = m.lineText.slice(0, m.colStart);
          const hit = m.lineText.slice(m.colStart, m.colEnd);
          const after = m.lineText.slice(m.colEnd);
          const newId = changed[m.matchedId] ?? '?';
          const yes = decisions[idx];
          html.push(`
            <div class="compact-match-row${yes ? '' : ' no'}" data-idx="${idx}">
              <span class="compact-match-line-no">L${m.lineNo}</span>
              <span class="compact-match-text">${escapeHtml(before)}<span class="hit">${escapeHtml(hit)}</span>${escapeHtml(after)}</span>
              <span class="compact-match-arrow">→</span>
              <span class="compact-match-new-id">${newId}</span>
              <span class="compact-match-decision">
                <button class="${yes ? 'active yes' : ''}" data-action="yes" data-idx="${idx}">Yes</button>
                <button class="${yes ? '' : 'active no'}" data-action="no" data-idx="${idx}">No</button>
              </span>
            </div>`);
        });
        html.push(`</div>`);
      });
      wrap.innerHTML = html.join('');

      // 이벤트 위임: (1) 개별 매치 Yes/No, (2) 파일 단위 Yes/No
      wrap.onclick = (e) => {
        const fileBtn = e.target.closest('button[data-file-action]');
        if (fileBtn) {
          const filePath = fileBtn.dataset.file;
          const setYes = (fileBtn.dataset.fileAction === 'yes');
          currentMatches.forEach((m, idx) => {
            if (m.relPath === filePath) decisions[idx] = setYes;
          });
          renderMatches();
          return;
        }
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const idx = parseInt(btn.dataset.idx, 10);
        if (Number.isNaN(idx)) return;
        decisions[idx] = (btn.dataset.action === 'yes');
        renderMatches();
      };
      updateYesCount();
    }

    function renderSummary() {
      const sum = $('#compact-summary');
      if (!currentPlan) { sum.textContent = ''; return; }
      const changedCount = Object.keys(currentPlan.changedIdMap || {}).length;
      const removedCount = (currentPlan.removedIds || []).length;
      const newCount = currentPlan.newProvinceCount || 0;
      sum.innerHTML = `
        <b>매핑:</b> ${changedCount}개 ID 이동 ·
        <b>제거:</b> ${removedCount}개 ID 슬롯 사라짐 ·
        <b>최종 프로빈스 수:</b> ${newCount} ·
        <b>외부 파일 매치:</b> ${currentMatches.length}개
      `;
    }

    async function runScan() {
      scanBtn.disabled = true;
      try {
        const planRes = await window.pywebview.api.scan_placeholder_ids();
        if (!planRes.ok) {
          alert('계획 산출 실패: ' + (planRes.error || 'unknown'));
          return;
        }
        currentPlan = planRes.plan;
        const movePairs = planRes.movePairs || [];
        const idsToSearch = movePairs.map(p => p[0]);  // 옛 ID들

        if (idsToSearch.length === 0) {
          currentMatches = [];
          decisions = [];
        } else {
          const searchRes = await window.pywebview.api.search_id_usages(idsToSearch);
          if (!searchRes.ok) {
            alert('검색 실패: ' + (searchRes.error || 'unknown'));
            return;
          }
          currentMatches = searchRes.matches || [];
          decisions = currentMatches.map(() => true);  // 기본 모두 Yes
        }

        renderSummary();
        renderMapTable(currentPlan);
        renderMatches();
        showDialog();
      } catch (e) {
        alert('스캔 중 예외: ' + e.message);
      } finally {
        scanBtn.disabled = false;
      }
    }

    async function runApply(dryRun) {
      const approved = currentMatches.filter((_, i) => decisions[i]);
      const btn = dryRun ? dryRunBtn : executeBtn;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = dryRun ? '드라이런 중…' : '실행 중…';
      try {
        const res = await window.pywebview.api.apply_min_invasive_compaction(
          approved, dryRun,
        );
        if (!res.ok) {
          alert((dryRun ? '드라이런' : '실행') + ' 실패: ' + (res.error || 'unknown'));
          return;
        }
        const rep = res.report || {};
        const msg = [
          dryRun ? '✓ 드라이런 완료 (실제 파일은 변경되지 않았습니다)' : '✓ 실행 완료',
          `치환 적용된 위치: ${rep.totalReplacements || 0}`,
          `수정된 파일 수: ${(rep.modifiedFiles || []).length}`,
          `스킵된 파일 수: ${(rep.skippedFiles || []).length}`,
          `매핑 안 된 매치(unmapped): ${(rep.unmappedMatches || []).length}`,
        ].join('\n');
        alert(msg);
        if (!dryRun) {
          hideDialog();
          // 저장 후 메모리 상태도 최신으로. 간단히 페이지 reload는 너무 강하니
          // 사용자에게 안내만.
          // (실제 메모리 동기화는 self.provinces가 백엔드에서 갱신됨)
        }
      } catch (e) {
        alert((dryRun ? '드라이런' : '실행') + ' 중 예외: ' + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = origText;
      }
    }

    scanBtn.addEventListener('click', runScan);
    cancelBtn.addEventListener('click', hideDialog);
    dryRunBtn.addEventListener('click', () => runApply(true));
    executeBtn.addEventListener('click', () => {
      if (!confirm('실제 파일 변경을 적용합니다. 백업은 생성되지 않으며 되돌릴 수 없습니다. 계속할까요?')) return;
      runApply(false);
    });
  });
})();

// =====================================================================
// 인접 연결 모드 (adjacency)
// =====================================================================
// 사용 흐름:
//   1) 4번 탭 클릭 → state.mode = 'adjacency', 하단 입력바 노출
//   2) 캔버스 클릭 1회 → From 프로빈스 ID 채움, 마커 표시
//   3) 캔버스 클릭 2회 → To 프로빈스 ID 채움, From-To 잇는 점선 표시
//   4) 입력바에서 Type/Through/Rule/Comment 입력
//   5) [추가] 버튼 → api.add_adjacency 호출 → 성공 시 입력바 초기화 후 다시 1번
//
// 모드 이탈 또는 [↺] 버튼 시 마커/선/입력 모두 리셋.
window.AdjMode = (function () {
  const RULE_RE = /^[A-Z][A-Z0-9_]*$/;

  // 내부 상태
  let fromId = null;    // 첫 클릭 프로빈스 ID
  let toId = null;      // 두번째 클릭 프로빈스 ID
  let fromXY = null;    // 캔버스 이미지 좌표 (x, y)
  let toXY = null;

  // DOM 캐시 (lazy)
  let svg, hint, fromEl, toEl, fromDisplay, toDisplay;
  let typeSel, throughInput, throughRefreshBtn, ruleInput, ruleValidity, commentInput;
  let addBtn, saveBtn, deleteBtn, clearBtn, listPanel, listBody, listCount, listClose;

  // 데이터 캐시
  let centroidsById = null;            // {pid: [cx, cy]}
  let allAdjacencies = [];             // [{fromId, toId, type, through, ruleName, comment, index}]
  let fromRgb = null;                  // 현재 선택된 From 프로빈스의 RGB [r,g,b]
  let toRgb = null;
  // 편집 상태: null=신규 입력, int=해당 인덱스의 항목을 편집 중
  let editingIndex = null;
  // Through 사용자 수동 편집 여부. true면 자동 채움이 일어나지 않음.
  let throughManuallyEdited = false;

  function cache() {
    if (svg) return;
    svg = document.getElementById('adj-svg');
    hint = document.getElementById('adj-hint');
    fromEl = document.getElementById('adj-step-1');
    toEl = document.getElementById('adj-step-2');
    fromDisplay = document.getElementById('adj-from-display');
    toDisplay = document.getElementById('adj-to-display');
    typeSel = document.getElementById('adj-type');
    throughInput = document.getElementById('adj-through');
    throughRefreshBtn = document.getElementById('adj-through-refresh');
    ruleInput = document.getElementById('adj-rule');
    ruleValidity = document.getElementById('adj-rule-validity');
    commentInput = document.getElementById('adj-comment');
    addBtn = document.getElementById('adj-add-btn');
    saveBtn = document.getElementById('adj-save-btn');
    deleteBtn = document.getElementById('adj-delete-btn');
    clearBtn = document.getElementById('adj-clear-btn');
    listPanel = document.getElementById('adjacency-list-panel');
    listBody = document.getElementById('adj-list-body');
    listCount = document.getElementById('adj-list-count');
    listClose = document.getElementById('adj-list-close');
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function updateStepStyles() {
    if (!fromEl) return;
    // step 1
    fromEl.classList.toggle('active', fromId === null);
    fromEl.classList.toggle('filled', fromId !== null);
    fromDisplay.textContent = (fromId !== null) ? String(fromId) : '-';
    // step 2
    toEl.classList.toggle('active', fromId !== null && toId === null);
    toEl.classList.toggle('filled', toId !== null);
    toDisplay.textContent = (toId !== null) ? String(toId) : '-';
  }

  function updateHint() {
    if (!hint) return;
    hint.classList.remove('warn', 'ok');
    if (fromId === null) {
      hint.textContent = '캔버스에서 첫 프로빈스를 클릭하세요.';
    } else if (toId === null) {
      hint.textContent = '두번째 프로빈스를 클릭하세요. (같은 ID는 불가)';
    } else {
      hint.textContent = `${fromId} → ${toId} 준비됨. 옵션 입력 후 [추가]를 누르세요.`;
      hint.classList.add('ok');
    }
  }

  function updateAddBtn() {
    if (!addBtn) return;
    // Rule은 select라 항상 OK
    const ready = (fromId !== null && toId !== null && fromId !== toId);
    addBtn.disabled = !ready;
  }

  function validateRuleLive() {
    // Rule이 select로 바뀌어 옵션 외 값이 들어올 수 없음. 검증은 항상 성공.
    if (!ruleInput) return;
    ruleInput.classList.remove('invalid');
    if (ruleValidity) ruleValidity.textContent = '';
    updateAddBtn();
  }

  function render() {
    cache();
    if (!svg) return;
    if (typeof state === 'undefined' || !state.loaded) {
      svg.innerHTML = '';
      return;
    }
    // 인접 모드가 아니면 아무것도 그리지 않음 (다른 모드 영향 방지)
    if (state.mode !== 'adjacency') {
      svg.innerHTML = '';
      return;
    }
    const z = state.zoom;
    const px0 = state.panX;
    const py0 = state.panY;
    const parts = [];

    function toScreen(imgX, imgY) {
      return [(imgX + 0.5) * z + px0, (imgY + 0.5) * z + py0];
    }

    // ── (1) 영구 인접 선: 모든 기존 adjacency를 type 별 색으로 ──
    //    각 선마다 보이는 라인 + 투명 두꺼운 hit-line(클릭 영역 확보)을 같이 그림.
    if (centroidsById && allAdjacencies.length > 0) {
      for (const a of allAdjacencies) {
        const c1 = centroidsById[a.fromId];
        const c2 = centroidsById[a.toId];
        if (!c1 || !c2) continue;
        const [x1, y1] = toScreen(c1[0], c1[1]);
        const [x2, y2] = toScreen(c2[0], c2[1]);
        // 화면 클리핑 (성능)
        const W = svg.clientWidth, H = svg.clientHeight;
        const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
        const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
        if (maxX < -10 || maxY < -10 || minX > W + 10 || minY > H + 10) continue;

        let cls = 'adj-perm-line';
        if (a.type === 'sea') cls += ' kind-sea';
        else if (a.type === 'impassable') cls += ' kind-impassable';
        else if (a.type === 'river') cls += ' kind-river';
        else cls += ' kind-strait';
        const isSel = (editingIndex !== null && a.index === editingIndex);
        if (isSel) cls += ' selected';
        const dotCls = isSel ? 'adj-perm-dot selected' : 'adj-perm-dot';
        const idx = a.index;
        parts.push(
          // 클릭 hit area (두께 10, 투명)
          `<line class="adj-perm-hit" data-idx="${idx}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>` +
          // 시각 라인
          `<line class="${cls}" data-idx="${idx}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>` +
          `<circle class="${dotCls} ${cls.replace('adj-perm-line','').trim()}" cx="${x1.toFixed(1)}" cy="${y1.toFixed(1)}" r="2.5"/>` +
          `<circle class="${dotCls} ${cls.replace('adj-perm-line','').trim()}" cx="${x2.toFixed(1)}" cy="${y2.toFixed(1)}" r="2.5"/>`
        );
      }
    }

    // ── (2) 현재 선택 중인 임시 선 (From-To 점선) ──
    function pushTempLine(a, b) {
      if (!a || !b) return;
      const [ax, ay] = toScreen(a[0], a[1]);
      const [bx, by] = toScreen(b[0], b[1]);
      parts.push(`<line class="adj-link-line" x1="${ax.toFixed(1)}" y1="${ay.toFixed(1)}" x2="${bx.toFixed(1)}" y2="${by.toFixed(1)}"/>`);
    }
    if (fromXY && toXY) pushTempLine(fromXY, toXY);
    svg.innerHTML = parts.join('');
  }

  // ── 프로빈스 영역 색칠 (overlay-canvas) ──
  // 선택된 프로빈스 RGB와 일치하는 모든 픽셀을 반투명으로 칠한다.
  // From=주황, To=청록. 캔버스 좌표는 이미지 픽셀 그대로 (transform이 알아서 적용).
  function repaintProvinceMask() {
    if (!overlayCanvas || !overlayCtx) return;
    if (typeof state === 'undefined' || !state.loaded) return;

    // 인접 모드 아닐 때는 손대지 않음 (다른 모드의 overlay 잔상 보호)
    if (state.mode !== 'adjacency') return;

    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    if (!fromRgb && !toRgb) return;
    if (!state.imageData) return;

    const src = state.imageData.data;
    const W = state.width, H = state.height;
    const dst = overlayCtx.createImageData(W, H);
    const out = dst.data;

    const [fr, fg, fb] = fromRgb || [-1, -1, -1];
    const [tr, tg, tb] = toRgb || [-1, -1, -1];

    for (let i = 0; i < src.length; i += 4) {
      const r = src[i], g = src[i+1], b = src[i+2];
      if (fromRgb && r === fr && g === fg && b === fb) {
        // 주황 반투명
        out[i] = 255; out[i+1] = 140; out[i+2] = 0; out[i+3] = 140;
      } else if (toRgb && r === tr && g === tg && b === tb) {
        // 청록 반투명
        out[i] = 0; out[i+1] = 200; out[i+2] = 220; out[i+3] = 140;
      }
    }
    overlayCtx.putImageData(dst, 0, 0);
  }

  async function handleClick(x, y) {
    cache();
    if (x < 0 || y < 0 || x >= state.width || y >= state.height) return;
    // 편집 중에는 두 칸이 모두 차 있으면 캔버스 클릭 무시 (의도치 않은 손실 방지).
    // 단, FROM/TO 칩 클릭으로 한쪽을 비운 상태(재선택 시작)라면 클릭을 받는다.
    if (editingIndex !== null && fromId !== null && toId !== null) {
      hint.textContent = '편집 중입니다. FROM/TO 칩을 눌러 재선택하거나 [↺]/[저장]/[삭제]를 사용하세요.';
      hint.classList.add('warn');
      return;
    }
    // 백엔드에서 정확한 프로빈스 ID 조회
    let pid = null;
    try {
      const r = await window.pywebview.api.get_province_id_at_pixel(x, y);
      if (r && r.ok) pid = r.provinceId;
    } catch (_) {}
    if (pid === null || pid === undefined) {
      hint.textContent = '해당 픽셀에서 프로빈스 ID를 찾을 수 없습니다.';
      hint.classList.add('warn');
      return;
    }

    // 클릭 픽셀의 RGB도 같이 얻기 (state.imageData에서 직접)
    const [r0, g0, b0] = getPixel(x, y);
    const clickedRgb = [r0, g0, b0];
    // centroid 좌표 (영구 선 표시용은 centroid가 자연스럽지만, 임시 선은 클릭점 그대로)
    const c = centroidsById ? centroidsById[pid] : null;
    const xy = c ? [c[0], c[1]] : [x, y];

    if (fromId === null) {
      fromId = pid;
      fromXY = xy;
      fromRgb = clickedRgb;
    } else if (toId === null) {
      if (pid === fromId) {
        hint.textContent = 'From과 같은 프로빈스입니다. 다른 프로빈스를 클릭하세요.';
        hint.classList.add('warn');
        return;
      }
      toId = pid;
      toXY = xy;
      toRgb = clickedRgb;
    } else {
      // 둘 다 차 있으면 다시 처음부터 (사용자가 마음을 바꿈)
      fromId = pid;
      fromXY = xy;
      fromRgb = clickedRgb;
      toId = null;
      toXY = null;
      toRgb = null;
    }
    updateStepStyles();
    updateHint();
    updateAddBtn();
    render();
    repaintProvinceMask();
    // From과 To가 모두 정해지면 Through 자동 추론(수동 편집 안 했을 때만)
    if (fromId !== null && toId !== null) {
      autoFillThrough(false);
    } else if (throughRefreshBtn) {
      throughRefreshBtn.disabled = true;
    }
  }

  function cancel() {
    // 신규/편집 모두 동일하게: 선택과 입력을 모두 해제 (단 입력 필드값은 유지)
    cache();
    clearSelection();
  }

  async function add() {
    cache();
    if (fromId === null || toId === null) return;
    const ruleVal = ruleInput.value.trim();
    if (ruleVal && !RULE_RE.test(ruleVal)) {
      validateRuleLive();
      return;
    }
    addBtn.disabled = true;
    const origText = addBtn.textContent;
    addBtn.textContent = '추가 중…';
    try {
      const r = await window.pywebview.api.add_adjacency(
        fromId, toId,
        typeSel.value || '',
        parseInt(throughInput.value, 10),
        ruleVal,
        commentInput.value.trim(),
      );
      if (!r || !r.ok) {
        hint.textContent = '추가 실패: ' + (r && r.error ? r.error : 'unknown');
        hint.classList.add('warn');
        return;
      }
      // 성공 → 다음 입력 준비. 옵션 값은 유지(연속 작업 편의).
      hint.textContent = `✓ ${fromId} ↔ ${toId} 추가됨 (총 ${r.count}개). 다음 프로빈스를 클릭하세요.`;
      hint.classList.add('ok');
      fromId = toId = null;
      fromXY = toXY = null;
      fromRgb = toRgb = null;
      throughManuallyEdited = false;
      if (throughRefreshBtn) {
        throughRefreshBtn.disabled = true;
        throughRefreshBtn.classList.remove('auto-filled');
      }
      updateStepStyles();
      updateAddBtn();
      // 마스크 클리어
      if (overlayCanvas && overlayCtx) {
        overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
      }
      // 인접 목록 즉시 갱신해서 영구 선 표시
      await refreshList();
      render();
    } catch (e) {
      hint.textContent = '추가 중 예외: ' + e.message;
      hint.classList.add('warn');
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = origText;
      updateAddBtn();
    }
  }

  // 이전 버전의 기능 패널 badge는 제거됨. 호환을 위해 no-op로 유지.
  function updateCountBadge(_n) { /* no-op */ }

  async function refreshList() {
    cache();
    if (!listBody) return;
    try {
      const r = await window.pywebview.api.list_adjacencies();
      if (!r || !r.ok) {
        listBody.innerHTML = `<p style="padding:12px;color:var(--muted)">불러오기 실패: ${r && r.error ? escapeHtml(r.error) : 'unknown'}</p>`;
        return;
      }
      const items = r.items || [];
      allAdjacencies = items;  // 캐시: render()가 영구 선 그릴 때 사용
      listCount.textContent = `${items.length}개`;
      // 영구 선 다시 그리기
      render();
      if (!items.length) {
        listBody.innerHTML = '<p style="padding:12px;color:var(--muted)">아직 등록된 인접이 없습니다.</p>';
        return;
      }
      const html = items.map(it => {
        const route = `${it.fromId} ↔ ${it.toId}`;
        const typeBadge = it.type ? it.type : '(strait/canal)';
        const through = (it.through !== null && it.through !== -1) ? `through ${it.through}` : '';
        const rule = it.ruleName ? `rule: ${it.ruleName}` : '';
        const cmt = it.comment || '';
        const metaParts = [
          `<span class="badge-mini">${escapeHtml(typeBadge)}</span>`,
          through ? escapeHtml(through) : '',
          rule ? escapeHtml(rule) : '',
          cmt ? escapeHtml(cmt) : '',
        ].filter(Boolean).join(' · ');
        return `
          <div class="adj-item" data-index="${it.index}">
            <div class="adj-item-main">
              <div class="adj-item-route">${escapeHtml(route)}</div>
              <div class="adj-item-meta">${metaParts}</div>
            </div>
            <button class="adj-item-del" data-index="${it.index}" title="삭제">×</button>
          </div>`;
      }).join('');
      listBody.innerHTML = html;
    } catch (e) {
      listBody.innerHTML = `<p style="padding:12px;color:var(--muted)">예외: ${escapeHtml(e.message)}</p>`;
    }
  }

  function toggleList() {
    cache();
    if (!listPanel) return;
    const wasHidden = listPanel.classList.contains('hidden');
    listPanel.classList.toggle('hidden');
    if (wasHidden) refreshList();
  }

  // 모드별 버튼 표시: 신규(creating) vs 편집(editing)
  function updateModeButtons() {
    cache();
    const editing = (editingIndex !== null);
    if (addBtn) addBtn.hidden = editing;
    if (saveBtn) saveBtn.hidden = !editing;
    if (deleteBtn) deleteBtn.hidden = !editing;
  }

  // 선택 진입: index의 항목 값들을 하단 바에 채우고 편집 모드로
  function selectAdjacency(index) {
    cache();
    const item = allAdjacencies.find(x => x.index === index);
    if (!item) return;

    editingIndex = index;
    fromId = item.fromId;
    toId = item.toId;
    // RGB는 백엔드에서 가져와 마스크에 사용
    refreshSelectedProvinceRgbs(item.fromId, item.toId);

    // centroid → 임시 좌표 (선 표시용 / 신규 모드와 동일 처리)
    const c1 = centroidsById ? centroidsById[item.fromId] : null;
    const c2 = centroidsById ? centroidsById[item.toId] : null;
    fromXY = c1 ? [c1[0], c1[1]] : null;
    toXY = c2 ? [c2[0], c2[1]] : null;

    // 필드 값 채움
    typeSel.value = item.type || '';
    throughInput.value = (item.through !== undefined && item.through !== null) ? String(item.through) : '-1';
    // 편집 진입 시 through는 "이미 저장된 사용자 결정"으로 간주 → 자동 채움 안 함
    throughManuallyEdited = true;
    if (throughRefreshBtn) {
      throughRefreshBtn.disabled = false;
      throughRefreshBtn.classList.remove('auto-filled');
    }
    // rule이 현재 옵션 목록에 없으면(다른 모드에서 이식 등) 임시 옵션으로 추가
    if (item.ruleName) {
      const has = Array.from(ruleInput.options).some(o => o.value === item.ruleName);
      if (!has) {
        const opt = document.createElement('option');
        opt.value = item.ruleName;
        opt.textContent = item.ruleName + ' (외부)';
        ruleInput.appendChild(opt);
      }
    }
    ruleInput.value = item.ruleName || '';
    commentInput.value = item.comment || '';

    updateStepStyles();
    updateModeButtons();
    validateRuleLive();
    if (hint) {
      hint.textContent = `편집 중: ${item.fromId} ↔ ${item.toId}. 값 수정 후 [저장] 또는 [삭제].`;
      hint.classList.remove('warn');
      hint.classList.add('ok');
    }
    // 목록 항목 selected 갱신
    if (listBody) {
      listBody.querySelectorAll('.adj-item').forEach(el => {
        const i = parseInt(el.dataset.index, 10);
        el.classList.toggle('selected', i === index);
      });
    }
    render();
  }

  async function refreshSelectedProvinceRgbs(fId, tId) {
    try {
      const a = await window.pywebview.api.get_province_rgb(fId);
      if (a && a.ok) fromRgb = a.rgb;
    } catch (_) {}
    try {
      const b = await window.pywebview.api.get_province_rgb(tId);
      if (b && b.ok) toRgb = b.rgb;
    } catch (_) {}
    repaintProvinceMask();
  }

  // 편집 저장
  async function saveEdit() {
    cache();
    if (editingIndex === null) return;
    if (fromId === null || toId === null) {
      hint.textContent = 'From/To가 비어있습니다.';
      hint.classList.add('warn');
      return;
    }
    const ruleVal = ruleInput.value.trim();
    if (ruleVal && !RULE_RE.test(ruleVal)) {
      validateRuleLive();
      return;
    }
    saveBtn.disabled = true;
    try {
      const r = await window.pywebview.api.update_adjacency(
        editingIndex,
        fromId, toId,
        typeSel.value || '',
        parseInt(throughInput.value, 10),
        ruleVal,
        commentInput.value.trim(),
      );
      if (!r || !r.ok) {
        hint.textContent = '저장 실패: ' + (r && r.error ? r.error : 'unknown');
        hint.classList.add('warn');
        return;
      }
      hint.textContent = `✓ 저장됨 (#${editingIndex}).`;
      hint.classList.add('ok');
      await refreshList();   // allAdjacencies 갱신
      // 편집 상태는 유지 (사용자가 연속 수정 후 닫고 싶을 때 ↺)
      render();
    } catch (e) {
      hint.textContent = '저장 중 예외: ' + e.message;
      hint.classList.add('warn');
    } finally {
      saveBtn.disabled = false;
    }
  }

  // 편집 삭제
  async function deleteEdit() {
    cache();
    if (editingIndex === null) return;
    if (!confirm(`이 인접 항목 (#${editingIndex}: ${fromId} ↔ ${toId}) 을 삭제할까요?`)) return;
    const idx = editingIndex;
    deleteBtn.disabled = true;
    try {
      const r = await window.pywebview.api.delete_adjacency(idx);
      if (!r || !r.ok) {
        hint.textContent = '삭제 실패: ' + (r && r.error ? r.error : 'unknown');
        hint.classList.add('warn');
        return;
      }
      hint.textContent = `✓ #${idx} 삭제됨 (남은 ${r.count}개).`;
      hint.classList.add('ok');
      // 선택 해제 후 새 입력 대기 상태로
      clearSelection();
      await refreshList();
      render();
    } catch (e) {
      hint.textContent = '삭제 중 예외: ' + e.message;
      hint.classList.add('warn');
    } finally {
      deleteBtn.disabled = false;
    }
  }

  // Through 자동 추론. force=true면 사용자 수동편집 플래그를 무시하고 강제 실행.
  async function autoFillThrough(force) {
    cache();
    if (fromId === null || toId === null) {
      if (throughRefreshBtn) throughRefreshBtn.disabled = true;
      return;
    }
    if (throughRefreshBtn) throughRefreshBtn.disabled = false;
    if (!force && throughManuallyEdited) return;

    // 새로고침 버튼이면 회전 애니메이션
    if (throughRefreshBtn) {
      throughRefreshBtn.classList.add('spinning');
      throughRefreshBtn.disabled = true;
    }
    try {
      const r = await window.pywebview.api.pick_through_province(fromId, toId);
      if (!r || !r.ok) return;
      const v = (typeof r.through === 'number') ? r.through : -1;
      throughInput.value = String(v);
      // 자동 채워졌음을 시각으로 표시 (다음 사용자 입력 전까지)
      if (throughRefreshBtn) throughRefreshBtn.classList.toggle('auto-filled', v !== -1);
      throughManuallyEdited = false;  // 자동 채움 후엔 다시 플래그 리셋
      // 상태 메시지
      if (hint && v !== -1 && r.via) {
        hint.textContent = `Through 자동 추론: ${v} (via ${r.via})`;
      } else if (hint && v === -1) {
        hint.textContent = 'Through 자동 추론 실패 (사이에 바다/호수 없음). 수동 입력 가능.';
      }
    } catch (_) {
      // 실패는 조용히 — 사용자가 수동 입력하면 됨
    } finally {
      if (throughRefreshBtn) {
        // 다음 프레임에서 클래스 제거 (transition 트리거)
        setTimeout(() => throughRefreshBtn.classList.remove('spinning'), 350);
        throughRefreshBtn.disabled = (fromId === null || toId === null);
      }
    }
  }

  // From/To 칩 클릭 시 해당 단계만 비워 캔버스로 재선택받기.
  // 편집 모드(editingIndex != null)에서도 사용 가능 — 한쪽만 갈아끼우고 [저장]하면 됨.
  function reopenStep(stepNo) {
    cache();
    if (state.mode !== 'adjacency') return;
    if (stepNo === 1) {
      fromId = null;
      fromXY = null;
      fromRgb = null;
    } else if (stepNo === 2) {
      toId = null;
      toXY = null;
      toRgb = null;
    } else {
      return;
    }
    // 한쪽이 비워지면 Through 자동 추론 불가 → 버튼 비활성, 자동 표시 해제
    if (throughRefreshBtn) {
      throughRefreshBtn.disabled = true;
      throughRefreshBtn.classList.remove('auto-filled');
    }
    updateStepStyles();
    updateHint();
    updateAddBtn();
    repaintProvinceMask();
    render();
  }

  // 편집/선택 해제 (cancel과 분리: cancel은 신규 입력 중에도 사용)
  function clearSelection() {
    editingIndex = null;
    fromId = toId = null;
    fromXY = toXY = null;
    fromRgb = toRgb = null;
    throughManuallyEdited = false;
    if (throughRefreshBtn) {
      throughRefreshBtn.disabled = true;
      throughRefreshBtn.classList.remove('auto-filled');
    }
    updateStepStyles();
    updateHint();
    updateAddBtn();
    updateModeButtons();
    if (overlayCanvas && overlayCtx) {
      overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    }
    if (listBody) {
      listBody.querySelectorAll('.adj-item.selected').forEach(el => el.classList.remove('selected'));
    }
    render();
  }

  function init() {
    cache();
    if (!addBtn) return;  // 패널이 없으면 작동 안 함
    addBtn.addEventListener('click', add);
    if (saveBtn) saveBtn.addEventListener('click', saveEdit);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteEdit);
    clearBtn.addEventListener('click', cancel);
    ruleInput.addEventListener('input', validateRuleLive);
    // From/To 칩 클릭 → 해당 단계만 재선택
    if (fromEl) fromEl.addEventListener('click', () => reopenStep(1));
    if (toEl) toEl.addEventListener('click', () => reopenStep(2));

    // Through 사용자 직접 수정 감지 (자동 채움 비활성)
    if (throughInput) {
      throughInput.addEventListener('input', () => {
        throughManuallyEdited = true;
        if (throughRefreshBtn) throughRefreshBtn.classList.remove('auto-filled');
      });
    }
    // Through 새로고침 버튼 → 강제 재계산
    if (throughRefreshBtn) {
      throughRefreshBtn.addEventListener('click', () => autoFillThrough(true));
    }
    if (listClose) listClose.addEventListener('click', () => listPanel.classList.add('hidden'));

    // SVG 위의 영구 선 클릭으로 선택
    if (svg) {
      svg.addEventListener('click', (e) => {
        if (state.mode !== 'adjacency') return;
        const hit = e.target.closest('.adj-perm-hit, .adj-perm-line');
        if (!hit) return;
        const idx = parseInt(hit.getAttribute('data-idx'), 10);
        if (Number.isNaN(idx)) return;
        selectAdjacency(idx);
      });
    }
    if (listBody) listBody.addEventListener('click', async (e) => {
      // 삭제 버튼 클릭
      const delBtn = e.target.closest('button.adj-item-del');
      if (delBtn) {
        e.stopPropagation();
        const idx = parseInt(delBtn.dataset.index, 10);
        if (Number.isNaN(idx)) return;
        if (!confirm('이 인접 항목을 삭제할까요?')) return;
        try {
          const r = await window.pywebview.api.delete_adjacency(idx);
          if (!r || !r.ok) {
            alert('삭제 실패: ' + (r && r.error ? r.error : 'unknown'));
            return;
          }
          // 만약 삭제한 항목이 현재 편집 중이면 선택 해제
          if (editingIndex === idx) clearSelection();
          refreshList();
        } catch (err) {
          alert('삭제 중 예외: ' + err.message);
        }
        return;
      }
      // 항목 전체 클릭 → 선택 진입
      const itemEl = e.target.closest('.adj-item');
      if (!itemEl) return;
      const idx = parseInt(itemEl.dataset.index, 10);
      if (Number.isNaN(idx)) return;
      selectAdjacency(idx);
    });
    updateStepStyles();
    updateHint();
    updateAddBtn();
    updateModeButtons();
    // pywebview 준비 후 카운트 가져오기 시도 (지연 호출)
    function tryFetchCount() {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.list_adjacencies) {
        window.pywebview.api.list_adjacencies().then(r => {
          if (r && r.ok) updateCountBadge((r.items || []).length);
        }).catch(() => {});
      }
    }
    window.addEventListener('pywebviewready', tryFetchCount);
    // 이미 준비된 경우 (재실행 등)
    setTimeout(tryFetchCount, 1000);
  }

  // adjacency_rules.txt 의 rule 이름들을 드롭다운에 채움.
  async function populateRuleOptions() {
    cache();
    if (!ruleInput) return;
    let names = [];
    try {
      const r = await window.pywebview.api.list_adjacency_rules();
      if (r && r.ok) names = r.names || [];
    } catch (_) {}
    const current = ruleInput.value;
    const opts = ['<option value="">(없음)</option>'];
    for (const n of names) {
      opts.push(`<option value="${n}">${n}</option>`);
    }
    ruleInput.innerHTML = opts.join('');
    // 기존 값이 옵션에 있으면 유지(편집 중 진입 안전).
    if (current && names.includes(current)) ruleInput.value = current;
  }

  // 인접 모드 진입: centroids 로드 + rule 목록 로드 + 영구 인접 목록 로드 → 영구 선 표시.
  async function enter() {
    cache();
    if (!centroidsById) {
      try {
        const r = await window.pywebview.api.get_province_centroids();
        if (r && r.ok) centroidsById = r.centroids || {};
      } catch (_) {
        centroidsById = {};
      }
    }
    await populateRuleOptions();
    await refreshList();
    render();
  }

  document.addEventListener('DOMContentLoaded', init);

  return { handleClick, render, cancel, enter, repaintProvinceMask };
})();
