'use strict';

// Models and terrain share one perspective camera and depth buffer.
class BuildingModels {
  constructor(load, redraw, status) {
    Object.assign(this, {load, redraw, status});
    this.canvas = document.createElement('canvas');
    this.models = new Map(); this.queue = []; this.pending = new Set(); this.generation = 0;
    this.activeLoads = 0;
    this.gl = this.canvas.getContext('webgl2', {alpha: true, antialias: true, premultipliedAlpha: false});
    this.canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault(); this.available = false; this.redraw();
      this.status('모델 표시 장치가 중단되어 위치 마커로 표시합니다.');
    });
    this.canvas.addEventListener('webglcontextrestored', () => { this.initialize(); this.reset(); this.redraw(); });
    this.initialize();
  }

  initialize() {
    const gl = this.gl;
    this.available = false;
    if (!gl) return this.status('WebGL2를 사용할 수 없어 위치 마커로 표시합니다.');
    try {
      const vertex = `#version 300 es
        layout(location=0) in vec3 position;
        layout(location=1) in vec3 normal;
        layout(location=2) in vec2 uv;
        layout(location=3) in vec4 placement;
        layout(location=4) in float modelScale;
        ${BuildingCamera.shader}
        out vec2 textureUV;
        out float lighting;
        void main() {
          float c=cos(placement.w), s=sin(placement.w);
          vec3 rotated=vec3(c*position.x+s*position.z,position.y,-s*position.x+c*position.z);
          gl_Position=projectWorld(placement.xyz+rotated*modelScale);
          vec3 worldNormal=vec3(c*normal.x+s*normal.z,normal.y,-s*normal.x+c*normal.z);
          lighting=.55+.45*max(0.,dot(normalize(worldNormal+vec3(0.,.0001,0.)),normalize(vec3(-.4,1.,.5))));
          textureUV=uv;
        }`;
      const fragment = `#version 300 es
        precision mediump float;
        uniform sampler2D diffuse;
        in vec2 textureUV;
        in float lighting;
        out vec4 color;
        void main() {
          vec4 sampleColor=texture(diffuse,textureUV);
          if(sampleColor.a<.1) discard;
          color=vec4(sampleColor.rgb*lighting,sampleColor.a);
        }`;
      this.program = createBuildingProgram(gl, vertex, fragment);
      this.instances = gl.createBuffer();
      this.terrain = new BuildingTerrain(gl);
      this.available = true;
    } catch (error) { this.status(`모델 표시를 초기화할 수 없습니다: ${error.message}`); }
  }

  reset() {
    this.generation++;
    for (const model of this.models.values()) {
      for (const mesh of model?.meshes || []) {
        for (const buffer of mesh.buffers) this.gl.deleteBuffer(buffer);
        this.gl.deleteTexture(mesh.texture); this.gl.deleteVertexArray(mesh.vertexArray);
      }
    }
    this.models.clear(); this.queue = []; this.pending.clear();
    if (this.terrain) { this.terrain.dirty = true; this.terrain.selectionDirty = true; }
  }

  request(name) {
    if (!this.available || this.models.has(name) || this.pending.has(name)) return;
    this.pending.add(name); this.queue.push({name, generation: this.generation}); this.pump();
  }

  pump() {
    while (this.activeLoads < 2 && this.queue.length) {
      const job = this.queue.shift(); this.activeLoads++;
      this.fetch(job).finally(() => { this.activeLoads--; this.pump(); });
    }
  }

  async fetch({name, generation}) {
    try {
      const model = await this.load(name);
      if (generation !== this.generation || !this.available) return;
      if (!model.ok) throw new Error(model.error);
      const images = await Promise.all(model.meshes.map(mesh => mesh.texture ? new Promise((resolve, reject) => {
        const image = new Image(); image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('텍스처 표시 실패')); image.src = mesh.texture;
      }) : null));
      if (generation !== this.generation || !this.available) return;
      const minimum = [Infinity, Infinity, Infinity], maximum = [-Infinity, -Infinity, -Infinity];
      for (const mesh of model.meshes) for (let index = 0; index < mesh.positions.length; index++) {
        const axis = index % 3;
        minimum[axis] = Math.min(minimum[axis], mesh.positions[index] * model.scale);
        maximum[axis] = Math.max(maximum[axis], mesh.positions[index] * model.scale);
      }
      this.models.set(name, {scale: model.scale, minimum, maximum,
        meshes: model.meshes.map((mesh, index) => this.upload(mesh, images[index]))});
      if (model.warnings.length) this.status(`${name}: ${model.warnings.join(' · ')}`);
    } catch (error) {
      if (generation !== this.generation) return;
      this.models.set(name, null);
      this.status(`${name}: ${error.message} (위치 마커로 표시)`);
    } finally {
      if (generation === this.generation) { this.pending.delete(name); this.redraw(); }
    }
  }

  upload(mesh, image) {
    const gl = this.gl, buffers = [], vertexArray = gl.createVertexArray();
    gl.bindVertexArray(vertexArray);
    const attribute = (index, size, values) => {
      const buffer = gl.createBuffer(); buffers.push(buffer);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(values), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(index); gl.vertexAttribPointer(index, size, gl.FLOAT, false, 0, 0);
    };
    attribute(0, 3, mesh.positions); attribute(1, 3, mesh.normals); attribute(2, 2, mesh.uv);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instances);
    gl.enableVertexAttribArray(3); gl.vertexAttribPointer(3, 4, gl.FLOAT, false, 20, 0); gl.vertexAttribDivisor(3, 1);
    gl.enableVertexAttribArray(4); gl.vertexAttribPointer(4, 1, gl.FLOAT, false, 20, 16); gl.vertexAttribDivisor(4, 1);
    const indices = gl.createBuffer(); buffers.push(indices);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indices);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(mesh.indices), gl.STATIC_DRAW);
    const texture = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    if (image) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([205, 213, 225, 255]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
    return {vertexArray, buffers, texture, count: mesh.indices.length};
  }

  bounds(row, camera) {
    const model = this.models.get(row.type);
    if (!model) return null;
    const points = [], cosine = Math.cos(row.rotation), sine = Math.sin(row.rotation);
    for (const x of [model.minimum[0], model.maximum[0]])
      for (const y of [model.minimum[1], model.maximum[1]])
        for (const z of [model.minimum[2], model.maximum[2]])
          points.push(camera.project(row.x + x * cosine + z * sine, row.y + y, row.z - x * sine + z * cosine));
    if (points.some(point => point[2] < camera.near)) return null;
    return [Math.min(...points.map(point => point[0])), Math.min(...points.map(point => point[1])),
            Math.max(...points.map(point => point[0])), Math.max(...points.map(point => point[1]))];
  }

  draw(context, visible, camera, state, base, mask, ratio) {
    const rendered = new Set();
    if (!this.available) return rendered;
    const gl = this.gl, {width, height} = camera;
    if (this.canvas.width !== Math.round(width * ratio) || this.canvas.height !== Math.round(height * ratio)) {
      this.canvas.width = Math.round(width * ratio); this.canvas.height = Math.round(height * ratio);
    }
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE);
    try { this.terrain.draw(camera, state, base, mask); }
    catch (error) { this.available = false; this.status(error.message); this.redraw(); return rendered; }
    gl.useProgram(this.program); camera.bind(gl, this.program);
    gl.activeTexture(gl.TEXTURE0);
    const groups = new Map();
    if (state.zoom >= 2) for (const {row} of visible) {
      this.request(row.type);
      const model = this.models.get(row.type);
      if (!model) continue;
      if (!groups.has(row.type)) groups.set(row.type, []);
      groups.get(row.type).push(row.x, row.y, row.z, row.rotation, model.scale);
      rendered.add(row.id);
    }
    for (const [name, placements] of groups) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.instances);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(placements), gl.DYNAMIC_DRAW);
      for (const mesh of this.models.get(name).meshes) {
        gl.bindVertexArray(mesh.vertexArray); gl.bindTexture(gl.TEXTURE_2D, mesh.texture);
        gl.drawElementsInstanced(gl.TRIANGLES, mesh.count, gl.UNSIGNED_INT, 0, placements.length / 5);
      }
    }
    context.drawImage(this.canvas, 0, 0, width, height);
    return rendered;
  }
}
