'use strict';
importScripts('country-overlay-data.js', 'province-id-colors.js', 'impassable-overlay.js');

self.onmessage = ({ data: request }) => {
  try {
    const data = request.kind === 'country'
      ? buildCountryOverlayData(request.map, request)
      : request.kind === 'impassable'
      ? { fillPixels: buildImpassablePixels(request.map) }
      : { fillPixels: buildProvinceIdPixels(request.map) };
    const transfers = [];
    if (data.fillPixels) transfers.push(data.fillPixels.buffer);
    if (data.borderSegments) transfers.push(data.borderSegments.buffer);
    self.postMessage({ revision: request.revision, ...data }, transfers);
  } catch (error) {
    self.postMessage({ revision: request.revision, error: error.message });
  }
};
