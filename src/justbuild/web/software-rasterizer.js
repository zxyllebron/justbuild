// Typed-array raster backend for the static mesh review fallback. It consumes
// the same clipped full triangles as Canvas, never proxy geometry or bounds.
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([0x01020304]).buffer)[0] === 4;
export class SoftwareRasterizer {
  constructor(context) { this.context = context; this.width = 0; this.height = 0; this.palette = []; }
  draw(renderer, sorted) {
    const width = Math.max(1, Math.round(renderer.width * renderer._renderPixelRatio)), height = Math.max(1, Math.round(renderer.height * renderer._renderPixelRatio));
    if (width !== this.width || height !== this.height) {
      this.image = this.context.createImageData(width, height); this.words = new Uint32Array(this.image.data.buffer); this.depth = new Float64Array(width * height); this.width = width; this.height = height;
    }
    this.words.fill(0); this.depth.fill(Infinity);
    const image = this.image.data, words = this.words, depth = this.depth, xy = renderer._xy, zs = renderer._z, ratio = renderer._renderPixelRatio;
    for (let i = this.palette.length; i < renderer._styles.length; i++) {
      const values = renderer._styles[i].match(/[\d.]+/g).map(Number), r = values[0], g = values[1], b = values[2];
      this.palette.push([r, g, b, LITTLE_ENDIAN ? ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0 : ((r << 24) | (g << 16) | (b << 8) | 255) >>> 0]);
    }
    let writes = 0;
    for (let i = 0; i < sorted.length; i++) {
      const id = sorted[i], o = id * 6, zi = id * 3, flags = renderer._flags[id], alpha = renderer._alpha[id], color = this.palette[renderer._style[id]];
      let ax = xy[o] * ratio, ay = xy[o + 1] * ratio, bx = xy[o + 2] * ratio, by = xy[o + 3] * ratio, cx = xy[o + 4] * ratio, cy = xy[o + 5] * ratio;
      let az = zs[zi], bz = zs[zi + 1], cz = zs[zi + 2];
      let area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      if (!area || !Number.isFinite(area)) continue;
      if (area < 0) { const tx = bx, ty = by, tz = bz; bx = cx; by = cy; bz = cz; cx = tx; cy = ty; cz = tz; area = -area; }
      const minX = Math.max(0, Math.ceil(Math.min(ax, bx, cx) - .5)), maxX = Math.min(width - 1, Math.floor(Math.max(ax, bx, cx) - .5));
      const minY = Math.max(0, Math.ceil(Math.min(ay, by, cy) - .5)), maxY = Math.min(height - 1, Math.floor(Math.max(ay, by, cy) - .5));
      if (minX > maxX || minY > maxY) continue;
      const e0x = by - cy, e0y = cx - bx, e0c = cy * bx - cx * by;
      const e1x = cy - ay, e1y = ax - cx, e1c = ay * cx - ax * cy;
      const e2x = ay - by, e2y = bx - ax, e2c = by * ax - bx * ay;
      const t0 = cy < by || (cy === by && cx > bx), t1 = ay < cy || (ay === cy && ax > cx), t2 = by < ay || (by === ay && bx > ax);
      const inv = 1 / area, zx = (e0x * az + e1x * bz + e2x * cz) * inv, zy = (e0y * az + e1y * bz + e2y * cz) * inv;
      let row0 = e0x * (minX + .5) + e0y * (minY + .5) + e0c, row1 = e1x * (minX + .5) + e1y * (minY + .5) + e1c, row2 = e2x * (minX + .5) + e2y * (minY + .5) + e2c;
      let rowZ = (row0 * az + row1 * bz + row2 * cz) * inv;
      for (let y = minY; y <= maxY; y++) {
        let v0 = row0, v1 = row1, v2 = row2, z = rowZ, pixel = y * width + minX;
        for (let x = minX; x <= maxX; x++, pixel++, v0 += e0x, v1 += e1x, v2 += e2x, z += zx) {
          if (!(v0 > 0 || (v0 === 0 && t0)) || !(v1 > 0 || (v1 === 0 && t1)) || !(v2 > 0 || (v2 === 0 && t2))) continue;
          if ((flags & 1) && z > depth[pixel]) continue;
          if ((flags & 3) === 3) depth[pixel] = z;
          if (alpha >= 1) words[pixel] = color[3];
          else {
            const p = pixel * 4, destination = image[p + 3] / 255, outAlpha = alpha + destination * (1 - alpha), weight = destination * (1 - alpha);
            if (!outAlpha) continue;
            image[p] = (color[0] * alpha + image[p] * weight) / outAlpha;
            image[p + 1] = (color[1] * alpha + image[p + 1] * weight) / outAlpha;
            image[p + 2] = (color[2] * alpha + image[p + 2] * weight) / outAlpha; image[p + 3] = outAlpha * 255;
          }
          writes++;
        }
        row0 += e0y; row1 += e1y; row2 += e2y; rowZ += zy;
      }
    }
    this.context.putImageData(this.image, 0, 0);
    return writes;
  }
}
