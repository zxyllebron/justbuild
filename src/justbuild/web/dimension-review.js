import * as THREE from 'three';

const NS = 'http://www.w3.org/2000/svg';
const AXES = ['X', 'Y', 'Z'];
const FACE_AXIS = {x_min:0, x_max:0, y_min:1, y_max:1, z_min:2, z_max:2};
const finiteVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const positive = value => Number.isFinite(value) && value > 0;
const mm = value => Number(value).toFixed(2);
const cloneBounds = value => ({min_mm:[...value.min_mm], max_mm:[...value.max_mm]});

function validBounds(value) {
  return value && finiteVector(value.min_mm) && finiteVector(value.max_mm) && value.max_mm.every((n,i) => n >= value.min_mm[i]);
}
function unionBounds(values) {
  if (!values.length || values.some(value => !validBounds(value))) return null;
  return {min_mm:AXES.map((_,i) => Math.min(...values.map(b => b.min_mm[i]))), max_mm:AXES.map((_,i) => Math.max(...values.map(b => b.max_mm[i])))};
}
function dimensionSet(bounds, label, source, sourcePaths) {
  if (!validBounds(bounds)) return null;
  const size = bounds.max_mm.map((v,i) => v - bounds.min_mm[i]);
  if (size.some(n => !positive(n))) return null;
  return {label, bounds:cloneBounds(bounds), size_mm:size, source, source_paths:sourcePaths};
}

// Numeric dimensions are taken from CAD BRep metadata and confirmed constraints.
// A rendered mesh, screenshot, camera distance or optional view bounds is never
// used as a dimensional source.
export function deriveDimensionData(job) {
  const metadata = job?.stage_results?.generating?.result?.metadata || {};
  const constraints = job?.proposal?.request?.constraints || {};
  const units = metadata.units;
  if (units !== 'mm') return {units:'mm', shell:null, board:null, holes:[], ports:[], source_sha256:null, issue:'CAD 尺寸单位未确认为 mm'};
  const parts = (metadata.parts || []).filter(part => ['base','lid'].includes(part.id));
  const shellBounds = parts.some(part => part.id === 'base') && parts.some(part => part.id === 'lid') ? unionBounds(parts.map(part => part.bounds)) : null;
  const shell = dimensionSet(shellBounds, '外壳', 'CAD 实体边界', parts.map(part => `metadata.parts.${part.id}.bounds`));
  const pcb = (metadata.input_solids || []).find(part => part.role === 'pcb' && validBounds(part.bounds));
  const pcbBounds = pcb?.bounds || metadata.board_placement?.pcb_bounds;
  const board = dimensionSet(pcbBounds, '板卡', '原始 STEP 板卡实体', [pcb ? `metadata.input_solids.${pcb.id || pcb.index}.bounds` : 'metadata.board_placement.pcb_bounds']);
  const holesSource = metadata.mounting_holes || constraints.mounting_holes || [];
  const holes = holesSource.filter(h => finiteVector(h.center_mm) && positive(h.diameter_mm)).map((h,i) => ({id:h.id || `hole-${i + 1}`, label:`P${i + 1}`, center_mm:[...h.center_mm], diameter_mm:h.diameter_mm, source:h.provenance || h.source || '已确认板孔约束'}));
  const ports = (metadata.ports || constraints.ports || []).filter(p => p && !['covered','internal'].includes(p.access_mode || p.access)).map(p => ({...p}));
  return {units:'mm', shell, board, holes, ports, source_sha256:metadata.input_provenance?.step_sha256 || null, request_sha256:metadata.input_provenance?.request_sha256 || null, issue:!shell && !board ? '尚无可用的 CAD 实体尺寸' : null};
}

function boxMeasurements(set, prefix) {
  if (!set) return [];
  const a = set.bounds.min_mm, b = set.bounds.max_mm;
  const edges = [ [[a[0],a[1],a[2]], [b[0],a[1],a[2]]], [[b[0],a[1],a[2]], [b[0],b[1],a[2]]], [[b[0],b[1],a[2]], [b[0],b[1],b[2]]] ];
  return edges.map(([start,end],axis) => ({id:`${prefix}-${axis}`, label:`${['长','宽','高'][axis]} ${mm(set.size_mm[axis])} mm`, value_mm:set.size_mm[axis], start_mm:start, end_mm:end, source:set.source, kind:'dimension'}));
}
function holeMeasurements(holes) {
  if (!holes.length) return [];
  const result = holes.map(h => ({id:h.id, label:`${h.label} Ø${mm(h.diameter_mm)} mm`, value_mm:h.diameter_mm, start_mm:h.center_mm, end_mm:h.center_mm, source:h.source, kind:'leader'}));
  if (holes.length > 1) {
    // These are board-axis spans of the original hole centers, not a claim that
    // an arbitrary hole pattern is rectangular or that all adjacent pitches match.
    const z = holes.reduce((sum,h) => sum + h.center_mm[2], 0) / holes.length;
    const min = [0,1].map(axis => Math.min(...holes.map(h => h.center_mm[axis])));
    const max = [0,1].map(axis => Math.max(...holes.map(h => h.center_mm[axis])));
    for (const axis of [0,1]) if (positive(max[axis] - min[axis])) {
      const a = [min[0],min[1],z], b = [...a]; b[axis] = max[axis];
      result.unshift({id:`hole-span-${axis}`, label:`孔中心 ${AXES[axis]} ${mm(max[axis] - min[axis])} mm`, value_mm:max[axis] - min[axis], start_mm:a, end_mm:b, source:'原始 STEP 板孔中心的轴向跨度', kind:'dimension'});
    }
  }
  return result;
}
function windowMeasurements(port, shell, suffix) {
  const faceAxis = FACE_AXIS[port.face];
  if (!Number.isInteger(faceAxis) || !finiteVector(port.center_mm)) return [];
  const center = [...port.center_mm];
  if (shell) center[faceAxis] = shell.bounds[port.face.endsWith('_min') ? 'min_mm' : 'max_mm'][faceAxis];
  const planeAxes = [0,1,2].filter(axis => axis !== faceAxis);
  const values = port.shape === 'circle' ? [port.diameter_mm] : [port.width_mm,port.height_mm];
  return values.flatMap((value,index) => {
    if (!positive(value)) return [];
    const a = [...center], b = [...center], axis = planeAxes[index]; a[axis] -= value / 2; b[axis] += value / 2;
    return [{id:`${port.id}-${index}`, label:`${suffix}${port.shape === 'circle' ? 'Ø' : index === 0 ? '宽 ' : '高 '}${mm(value)} mm`, value_mm:value, start_mm:a, end_mm:b, source:'设计开口尺寸；不代表实际插头或操作行程', kind:'dimension'}];
  });
}

function intersects(a, b, gap = 7) {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}
function clamp(n, low, high) { return Math.min(Math.max(n, low), Math.max(low, high)); }
export function dimensionLabelWidth(text, availableWidth = Infinity) {
  return Math.min(availableWidth, Math.max(64, [...text].reduce((n,c) => n + (/[^\x00-\xff]/.test(c) ? 12 : 7), 22)));
}
// Deterministic screen-space label placement. It never changes a measured value
// to make a label fit. The caller may use the returned positions for SVG or DOM.
export function layoutDimensionLabels(labels, width, height, reserved = []) {
  const placed = [], margin = 10, maxWidth = Math.max(1, width - margin * 2), rowHeight = 27;
  for (const item of labels) {
    const w = dimensionLabelWidth(item.label, maxWidth), h = rowHeight;
    const candidates = [];
    for (const dy of [0,-34,34,-68,68,-102,102]) for (const dx of [0,-46,46,-92,92]) candidates.push({x:clamp(item.x - w / 2 + dx,margin,width - margin - w), y:clamp(item.y - h / 2 + dy,margin,height - margin - h), width:w, height:h});
    let rect = candidates.find(candidate => ![...reserved,...placed].some(r => intersects(candidate,r)));
    if (!rect) {
      // Very narrow/zoomed views still keep all numeric labels in the image.
      // Search free rows before accepting a collision on an unusually tiny view.
      for (let y = margin; y + h <= height - margin && !rect; y += h + 8) for (const x of [margin,width - margin - w]) {
        const candidate = {x,y,width:w,height:h};
        if (![...reserved,...placed].some(r => intersects(candidate,r))) { rect = candidate; break; }
      }
    }
    rect ||= candidates[0];
    placed.push({...rect, id:item.id, label:item.label});
  }
  return placed;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(NS,name);
  for (const [key,value] of Object.entries(attributes)) element.setAttribute(key,String(value));
  return element;
}
const setLine = (line,a,b) => { line.setAttribute('x1',a.x); line.setAttribute('y1',a.y); line.setAttribute('x2',b.x); line.setAttribute('y2',b.y); };

export class DimensionReview {
  constructor(camera, viewport, container) {
    this.camera = camera; this.viewport = viewport; this.container = container;
    this.visible = false; this.mode = 'shell'; this.feature = null; this.records = []; this.data = deriveDimensionData(null);
    this.container.classList.add('dimension-review'); this.container.hidden = true;
    this.svg = svgElement('svg', {'aria-hidden':'true', 'class':'dimension-svg'});
    this.caption = document.createElement('div'); this.caption.className = 'dimension-caption';
    this.container.replaceChildren(this.svg,this.caption); this.point = new THREE.Vector3(); this.viewPoint = new THREE.Vector3();
    this.signature = '';
  }
  update(job, options = {}) {
    this.job = job; this.data = deriveDimensionData(job);
    // Optional bounds are accepted for integration compatibility only. They do
    // not provide numbers and cannot replace absent authoritative CAD metadata.
    this.viewBounds = options.bounds || null; this.rebuild();
    return this.getSummary();
  }
  setMode(mode) {
    if (!['shell','board','holes','feature'].includes(mode)) throw new Error('Unknown dimension mode');
    this.mode = mode; this.rebuild(); return this.getSummary();
  }
  setVisible(visible) {
    this.visible = Boolean(visible); this.container.hidden = !this.visible;
    if (this.visible) this.project();
  }
  setFeature(feature) {
    this.feature = feature || null;
    if (this.feature) this.mode = 'feature';
    else if (this.mode === 'feature') this.mode = 'shell';
    this.rebuild(); return this.getSummary();
  }
  getSummary() {
    const {shell,board,holes,source_sha256,request_sha256,issue} = this.data;
    const span = holes.length > 1 ? [0,1].map(axis => Math.max(...holes.map(h => h.center_mm[axis])) - Math.min(...holes.map(h => h.center_mm[axis]))) : null;
    return {mode:this.mode, units:'mm', shell, board, holes:{count:holes.length, diameter_mm:holes.map(h => h.diameter_mm), center_span_xy_mm:span}, source_sha256, request_sha256, issue, measurements:this.measurements?.map(m => ({id:m.id,label:m.label,value_mm:m.value_mm,source:m.source,start_mm:[...m.start_mm],end_mm:[...m.end_mm]})) || []};
  }
  rebuild() {
    let caption, measurements = [];
    if (this.mode === 'shell') { measurements = boxMeasurements(this.data.shell,'shell'); caption = '外壳 · CAD 实体 · 组装尺寸'; }
    if (this.mode === 'board') { measurements = boxMeasurements(this.data.board,'board'); if (measurements[2]) measurements[2].label = `厚 ${mm(this.data.board.size_mm[2])} mm`; caption = '板卡 · 原始 STEP 实体'; }
    if (this.mode === 'holes') { measurements = holeMeasurements(this.data.holes); caption = '板孔 · 已确认中心与孔径'; }
    if (this.mode === 'feature') {
      const linked = this.data.ports.filter(p => p.id === this.feature?.id || (this.feature?.port_ids || []).includes(p.id) || p.component_ref === this.feature?.id);
      // Multiple linked windows are explicit; do not silently label just one.
      measurements = linked.flatMap((p,i) => windowMeasurements(p,this.data.shell,linked.length > 1 ? `${i + 1} · ` : ''));
      caption = measurements.length ? `${this.feature?.label || this.feature?.id || '接口'} · 设计开口尺寸` : '该组件未提供外部开口尺寸';
    }
    this.measurements = measurements;
    this.svg.replaceChildren(); this.records = [];
    for (const measurement of measurements) {
      const group = svgElement('g', {'class':`dimension-item ${measurement.kind}`, 'data-measurement':measurement.id});
      const extensionA = svgElement('line',{'class':'dimension-extension'}), extensionB = svgElement('line',{'class':'dimension-extension'}), line = svgElement('line',{'class':'dimension-line'});
      const capA = svgElement('line',{'class':'dimension-cap'}), capB = svgElement('line',{'class':'dimension-cap'}), leader = svgElement('line',{'class':'dimension-leader'});
      const rect = svgElement('rect',{'class':'dimension-label-bg',rx:7}), text = svgElement('text',{'class':'dimension-label','text-anchor':'middle','dominant-baseline':'central'});
      text.textContent = measurement.label;
      const title = svgElement('title'); title.textContent = `${measurement.source}；单位 mm；装配坐标，爆炸展示不改变数值`;
      group.append(extensionA,extensionB,line,capA,capB,leader,rect,text,title); this.svg.append(group);
      this.records.push({measurement,group,extensionA,extensionB,line,capA,capB,leader,rect,text});
    }
    this.caption.textContent = measurements.length ? `${caption} · mm` : this.data.issue || caption || '尚无尺寸数据';
    this.caption.title = this.data.source_sha256 ? `STEP SHA-256: ${this.data.source_sha256}\n所有数值使用装配坐标；不是爆炸图间距。显示保留两位小数。` : '未建立源 STEP 散列关联';
    this.container.dataset.mode = this.mode; this.container.dataset.measurements = String(measurements.length);
    this.signature = ''; if (this.visible) this.project();
  }
  projectPoint(values,width,height) {
    this.viewPoint.set(...values).applyMatrix4(this.camera.matrixWorldInverse);
    if (this.viewPoint.z >= 0) return null;
    this.point.set(...values).project(this.camera);
    if (![this.point.x,this.point.y,this.point.z].every(Number.isFinite) || this.point.z < -1 || this.point.z > 1) return null;
    return {x:(this.point.x + 1) * width / 2,y:(1 - this.point.y) * height / 2};
  }
  project() {
    if (!this.visible) return;
    const width = this.viewport.clientWidth, height = this.viewport.clientHeight;
    if (!(width > 20 && height > 20)) return;
    this.camera.updateMatrixWorld();
    const signature = `${width},${height};${this.camera.matrixWorldInverse.elements.join(',')};${this.camera.projectionMatrix.elements.join(',')}`;
    if (signature === this.signature) return; this.signature = signature;
    this.svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const set = this.mode === 'board' || this.mode === 'holes' ? this.data.board : this.data.shell;
    const center = set ? set.bounds.min_mm.map((n,i) => (n + set.bounds.max_mm[i]) / 2) : [0,0,0];
    const projectedCenter = this.projectPoint(center,width,height) || {x:width / 2,y:height / 2};
    const candidates = [];
    for (const record of this.records) {
      const {measurement:m} = record, a = this.projectPoint(m.start_mm,width,height), b = this.projectPoint(m.end_mm,width,height);
      record.group.hidden = !a || !b; record.group.style.display = a && b ? '' : 'none';
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx,dy), middle = {x:(a.x+b.x)/2,y:(a.y+b.y)/2};
      let normal = length > .01 ? {x:-dy/length,y:dx/length} : {x:0,y:-1};
      if ((middle.x - projectedCenter.x) * normal.x + (middle.y - projectedCenter.y) * normal.y < 0) normal = {x:-normal.x,y:-normal.y};
      const offset = m.kind === 'leader' ? 26 : 22;
      const c = {x:a.x+normal.x*offset,y:a.y+normal.y*offset}, d = {x:b.x+normal.x*offset,y:b.y+normal.y*offset};
      setLine(record.extensionA,a,c); setLine(record.extensionB,b,d); setLine(record.line,c,d);
      const cap = point => [{x:point.x-normal.x*4,y:point.y-normal.y*4},{x:point.x+normal.x*4,y:point.y+normal.y*4}];
      setLine(record.capA,...cap(c)); setLine(record.capB,...cap(d));
      const dimension = m.kind !== 'leader'; for (const element of [record.extensionA,record.extensionB,record.line,record.capA,record.capB]) element.style.display = dimension ? '' : 'none';
      const anchor = {x:(c.x+d.x)/2,y:(c.y+d.y)/2}; record.anchor = m.kind === 'leader' ? a : anchor;
      candidates.push({id:m.id,label:m.label,x:anchor.x+normal.x*17,y:anchor.y+normal.y*17,record});
    }
    // Reserve the source-caption area. Its width shrinks with the viewport.
    const reserved = [{x:10,y:10,width:Math.max(1,width-20),height:32},{x:Math.max(10,(width-Math.min(300,width-20))/2),y:height-45,width:Math.min(300,width-20),height:35}];
    const positions = layoutDimensionLabels(candidates,width,height,reserved);
    for (let i = 0; i < positions.length; i++) {
      const position = positions[i], record = candidates[i].record, center = {x:position.x+position.width/2,y:position.y+position.height/2};
      for (const [key,value] of Object.entries({x:position.x,y:position.y,width:position.width,height:position.height})) record.rect.setAttribute(key,value);
      record.text.setAttribute('x',center.x); record.text.setAttribute('y',center.y); record.text.setAttribute('textLength',Math.min(dimensionLabelWidth(position.label)-22,position.width-18)); record.text.setAttribute('lengthAdjust','spacingAndGlyphs');
      setLine(record.leader,record.anchor,center);
    }
    this.container.dataset.visibleLabels = String(positions.length);
  }
  dispose() { this.container.replaceChildren(); this.records = []; }
}
