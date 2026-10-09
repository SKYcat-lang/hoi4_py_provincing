'use strict';

class BuildingTerrain {
  constructor(gl) {
    this.gl = gl; this.dirty = true; this.selectionDirty = true;
    this.program = createBuildingProgram(gl, `#version 300 es
      precision highp float;
      ${BuildingCamera.shader}
      uniform vec4 region;
      uniform ivec2 divisions;
      uniform vec2 mapSize;
      uniform sampler2D heightmap;
      out vec2 uv;
      out vec3 world;
      void main() {
        int column=gl_VertexID%(divisions.x+1), row=gl_VertexID/(divisions.x+1);
        vec2 point=mix(region.xy,region.zw,vec2(column,row)/vec2(divisions));
        uv=vec2(point.x/mapSize.x,point.y/mapSize.y);
        float altitude=max(9.5,texture(heightmap,uv).r*25.5);
        world=vec3(point.x,altitude,mapSize.y-point.y);
        gl_Position=projectWorld(world);
      }`, `#version 300 es
      precision highp float;
      uniform sampler2D mapImage;
      uniform sampler2D selection;
      in vec2 uv;
      in vec3 world;
      out vec4 color;
      void main() {
        vec3 normal=normalize(cross(dFdx(world),dFdy(world)));
        if(normal.y<0.) normal=-normal;
        float light=.65+.35*max(0.,dot(normal,normalize(vec3(-.4,1.,.5))));
        vec4 highlight=texture(selection,uv);
        vec3 base=texture(mapImage,uv).rgb*light;
        color=vec4(mix(base,highlight.rgb,highlight.a),1.);
      }`);
    this.locations = Object.fromEntries(['region', 'divisions', 'mapSize', 'heightmap', 'mapImage', 'selection']
      .map(name => [name, gl.getUniformLocation(this.program, name)]));
    this.vertexArray = gl.createVertexArray(); this.indices = gl.createBuffer();
    this.textures = [gl.createTexture(), gl.createTexture(), gl.createTexture()];
  }

  upload(state, base, mask) {
    const gl = this.gl;
    if (state.width > gl.getParameter(gl.MAX_TEXTURE_SIZE) || state.height > gl.getParameter(gl.MAX_TEXTURE_SIZE)) {
      throw new Error('지도가 그래픽 장치의 최대 텍스처 크기를 초과합니다.');
    }
    for (let unit = 0; unit < 3; unit++) {
      if (!this.dirty && !(unit === 2 && this.selectionDirty)) continue;
      gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, this.textures[unit]);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      if (unit === 0) {
        const values = state.heightmapValueBuf;
        if (values?.length === state.width * state.height) {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, state.width, state.height, 0, gl.RED, gl.UNSIGNED_BYTE, values);
        } else gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 1, 1, 0, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array([95]));
      } else {
        const source = unit === 1 ? base : mask;
        if (source.width && source.height) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
        else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
      }
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, unit === 0 ? gl.LINEAR : gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, unit === 0 ? gl.LINEAR : gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    this.dirty = false; this.selectionDirty = false;
  }

  draw(camera, state, base, mask) {
    const gl = this.gl;
    this.upload(state, base, mask);
    const corners = [[0, 0], [camera.width, 0], [0, camera.height], [camera.width, camera.height]]
      .map(point => camera.unproject(...point));
    const left = Math.max(0, Math.min(...corners.map(point => point[0])) - 40);
    const top = Math.max(0, Math.min(...corners.map(point => point[1])) - 40);
    const right = Math.min(state.width, Math.max(...corners.map(point => point[0])) + 40);
    const bottom = Math.min(state.height, Math.max(...corners.map(point => point[1])) + 40);
    if (right <= left || bottom <= top) return;
    const step = Math.max(1, 3 / state.zoom);
    const columns = Math.min(512, Math.max(1, Math.ceil((right - left) / step)));
    const rows = Math.min(512, Math.max(1, Math.ceil((bottom - top) / step)));
    gl.bindVertexArray(this.vertexArray); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indices);
    if (this.columns !== columns || this.rows !== rows) {
      const indices = new Uint32Array(columns * rows * 6);
      let offset = 0;
      for (let row = 0; row < rows; row++) for (let column = 0; column < columns; column++) {
        const first = row * (columns + 1) + column;
        indices.set([first, first + 1, first + columns + 1, first + 1, first + columns + 2, first + columns + 1], offset);
        offset += 6;
      }
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
      this.columns = columns; this.rows = rows;
    }
    gl.useProgram(this.program); camera.bind(gl, this.program);
    gl.uniform4f(this.locations.region, left, top, right, bottom);
    gl.uniform2i(this.locations.divisions, columns, rows); gl.uniform2f(this.locations.mapSize, state.width, state.height);
    for (let unit = 0; unit < 3; unit++) {
      gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, this.textures[unit]);
    }
    gl.uniform1i(this.locations.heightmap, 0); gl.uniform1i(this.locations.mapImage, 1); gl.uniform1i(this.locations.selection, 2);
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
    gl.drawElements(gl.TRIANGLES, columns * rows * 6, gl.UNSIGNED_INT, 0);
    gl.disable(gl.POLYGON_OFFSET_FILL); gl.activeTexture(gl.TEXTURE0);
  }
}
