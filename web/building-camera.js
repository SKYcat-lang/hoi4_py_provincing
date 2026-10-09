'use strict';

// Shared CPU/GPU perspective: world +Y is up, +Z points toward the map's top.
class BuildingCamera {
  static shader = `
    uniform vec3 cameraCenter;
    uniform vec2 cameraTilt;
    uniform vec2 cameraFocal;
    uniform vec3 cameraDepth;
    vec4 projectWorld(vec3 world) {
      vec3 relative=world-cameraCenter;
      float vertical=relative.y*cameraTilt.x+relative.z*cameraTilt.y;
      float depth=cameraDepth.x-relative.y*cameraTilt.y+relative.z*cameraTilt.x;
      return vec4(relative.x*cameraFocal.x,vertical*cameraFocal.y,
                  cameraDepth.y*depth+cameraDepth.z,depth);
    }`;

  constructor(state) { this.state = state; this.uniforms = new WeakMap(); }

  update(width, height) {
    const state = this.state();
    this.width = Math.max(1, width); this.height = Math.max(1, height);
    this.focal = this.height / (2 * Math.tan(Math.PI / 8));
    this.distance = this.focal / state.zoom;
    this.sine = Math.sin(50 * Math.PI / 180); this.cosine = Math.cos(50 * Math.PI / 180);
    this.centerX = (this.width / 2 - state.panX) / state.zoom;
    this.centerZ = state.height - (this.height / 2 - state.panY) / state.zoom;
    this.centerHeight = this.terrainHeight(this.centerX, this.centerZ);
    this.near = Math.max(.01, this.distance / 10000);
    const far = this.distance + 2 * Math.hypot(state.width, state.height) + 100;
    this.depthA = (far + this.near) / (far - this.near);
    this.depthB = -2 * far * this.near / (far - this.near);
  }

  terrainHeight(x, z) {
    const {width, height, heightmapValueBuf: pixels} = this.state();
    if (!pixels || pixels.length !== width * height) return 9.5;
    const sampleX = Math.max(0, Math.min(width - 1, x - .5));
    const sampleZ = Math.max(0, Math.min(height - 1, z - .5));
    const left = Math.floor(sampleX), bottom = Math.floor(sampleZ);
    const right = Math.min(left + 1, width - 1), top = Math.min(bottom + 1, height - 1);
    const horizontal = sampleX - left, vertical = sampleZ - bottom;
    const low = pixels[(height - 1 - bottom) * width + left] * (1 - horizontal) + pixels[(height - 1 - bottom) * width + right] * horizontal;
    const high = pixels[(height - 1 - top) * width + left] * (1 - horizontal) + pixels[(height - 1 - top) * width + right] * horizontal;
    return Math.max(9.5, (low * (1 - vertical) + high * vertical) * .1);
  }

  project(x, altitude, z) {
    const dx = x - this.centerX, dy = altitude - this.centerHeight, dz = z - this.centerZ;
    const depth = this.distance - dy * this.cosine + dz * this.sine;
    return [this.width / 2 + this.focal * dx / depth,
            this.height / 2 - this.focal * (dy * this.sine + dz * this.cosine) / depth, depth];
  }

  unproject(screenX, screenY) {
    const horizontal = (screenX - this.width / 2) / this.focal;
    const vertical = (this.height / 2 - screenY) / this.focal;
    const directionY = vertical * this.sine - this.cosine;
    const directionZ = vertical * this.cosine + this.sine;
    const eyeY = this.centerHeight + this.distance * this.cosine;
    const eyeZ = this.centerZ - this.distance * this.sine;
    // Every viewport ray points down at this tilt/FOV. Bracket the heightfield.
    let near = Math.max(0, (eyeY - 25.5) / -directionY);
    let far = Math.max(near, (eyeY - 9.5) / -directionY);
    // Find the first intersection when a ray crosses several ridges/valleys.
    const start = near, end = far;
    const steps = Math.max(1, Math.ceil((end - start) * Math.hypot(horizontal, directionZ) * 2));
    for (let step = 1; step <= steps; step++) {
      const distance = start + (end - start) * step / steps;
      const x = this.centerX + horizontal * distance, z = eyeZ + directionZ * distance;
      if (eyeY + directionY * distance <= this.terrainHeight(x, z)) { far = distance; break; }
      near = distance;
    }
    for (let iteration = 0; iteration < 32; iteration++) {
      const distance = (near + far) / 2;
      const x = this.centerX + horizontal * distance, z = eyeZ + directionZ * distance;
      if (eyeY + directionY * distance > this.terrainHeight(x, z)) near = distance;
      else far = distance;
    }
    const distance = (near + far) / 2;
    return [this.centerX + horizontal * distance, this.state().height - eyeZ - directionZ * distance];
  }

  bind(gl, program) {
    if (!this.uniforms.has(program)) this.uniforms.set(program, Object.fromEntries(
      ['cameraCenter', 'cameraTilt', 'cameraFocal', 'cameraDepth'].map(name => [name, gl.getUniformLocation(program, name)])));
    const locations = this.uniforms.get(program);
    gl.uniform3f(locations.cameraCenter, this.centerX, this.centerHeight, this.centerZ);
    gl.uniform2f(locations.cameraTilt, this.sine, this.cosine);
    gl.uniform2f(locations.cameraFocal, 2 * this.focal / this.width, 2 * this.focal / this.height);
    gl.uniform3f(locations.cameraDepth, this.distance, this.depthA, this.depthB);
  }
}
