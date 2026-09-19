'use strict';

class CountryBorderRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', { alpha: true, antialias: false, depth: false });
    if (!gl) throw new Error('국가 경계 표시에 WebGL2가 필요합니다.');
    this.gl = gl;
    this.program = createBorderProgram(gl, `#version 300 es
      precision highp float;
      layout(location=0) in vec4 segment;
      uniform vec2 viewport;
      uniform vec2 pan;
      uniform float zoom;
      uniform float line_width;
      out vec2 local_point;
      flat out float segment_length;
      void main() {
        vec2 start = segment.xy * zoom + pan;
        vec2 finish = segment.zw * zoom + pan;
        vec2 direction = normalize(finish - start);
        vec2 normal = vec2(-direction.y, direction.x);
        segment_length = length(finish - start);
        float radius = line_width * 0.5 + 1.0;
        vec2 corners[6] = vec2[6](vec2(0,-1),vec2(1,-1),vec2(0,1),
                                 vec2(0,1),vec2(1,-1),vec2(1,1));
        vec2 corner = corners[gl_VertexID];
        local_point = vec2(mix(-radius, segment_length + radius, corner.x), corner.y * radius);
        vec2 point = start + direction * local_point.x + normal * local_point.y;
        gl_Position = vec4(point.x / viewport.x * 2.0 - 1.0,
                           1.0 - point.y / viewport.y * 2.0, 0, 1);
      }
    `, `#version 300 es
      precision highp float;
      in vec2 local_point;
      flat in float segment_length;
      uniform float line_width;
      out vec4 color;
      void main() {
        vec2 nearest = vec2(clamp(local_point.x, 0.0, segment_length), 0.0);
        float distance_to_line = distance(local_point, nearest);
        float alpha = 1.0 - smoothstep(line_width * 0.5 - 0.5,
                                      line_width * 0.5 + 0.5, distance_to_line);
        color = vec4(alpha, alpha, 0.0, alpha);
      }
    `);
    this.vertexArray = gl.createVertexArray();
    this.buffer = gl.createBuffer();
    gl.bindVertexArray(this.vertexArray);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 16, 0);
    gl.vertexAttribDivisor(0, 1);
    this.locations = {};
    for (const name of ['viewport', 'pan', 'zoom', 'line_width']) {
      this.locations[name] = gl.getUniformLocation(this.program, name);
    }
    this.segmentCount = 0;
  }

  upload(segments) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, segments, gl.STATIC_DRAW);
    this.segmentCount = segments.length / 4;
  }

  render(mapState, lineWidth) {
    const gl = this.gl;
    const scale = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(this.canvas.clientWidth * scale));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * scale));
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vertexArray);
    gl.uniform2f(this.locations.viewport, width, height);
    gl.uniform2f(this.locations.pan, mapState.panX * scale, mapState.panY * scale);
    gl.uniform1f(this.locations.zoom, mapState.zoom * scale);
    gl.uniform1f(this.locations.line_width, lineWidth * scale);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    // One draw call, no CPU Path2D tessellation during zoom/pan.
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, this.segmentCount);
  }
}
