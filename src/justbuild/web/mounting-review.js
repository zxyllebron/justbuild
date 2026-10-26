import * as THREE from 'three';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const coordinate = values => values.slice(0, 2).map(n => Number(n).toFixed(2)).join(', ');

// Display annotations only. The measurements below come from the independent
// STEP checker; these rings and labels are never used to establish alignment.
export class MountingReview {
  constructor(scene, camera, viewport, container) {
    this.scene = scene;
    this.camera = camera;
    this.viewport = viewport;
    this.container = container;
    this.group = new THREE.Group();
    scene.add(this.group);
    this.labels = [];
    this.enabled = true;
  }

  update(job, panel, summary) {
    this.group.traverse(node => {
      node.geometry?.dispose();
      node.material?.dispose();
    });
    this.group.clear();
    this.container.replaceChildren();
    this.labels = [];
    const cad = job.stage_results?.generating?.result;
    const holes = cad?.metadata?.mounting_holes || [];
    const lid = cad?.metadata?.lid_screw_centers_mm || [];
    const checks = job.stage_results?.verifying?.result?.checks || [];
    const targetName = cad?.metadata?.hardware?.pcb_mount_part === 'pcb_carrier' ? '载架' : '底座';
    const rows = [];
    const deviations = [];
    holes.forEach((hole, i) => {
      const id = `P${i + 1}`;
      const datum = checks.find(c => c.id === `mounting:${hole.id}:board_datum`);
      const base = checks.find(c => c.id === `mounting:${hole.id}:0`);
      const matches = base?.measurements?.matching_bores || [];
      const deviation = matches.length ? Math.min(...matches.map(b => b.axis_offset_mm)) : null;
      const passed = datum?.status === 'pass' && base?.status === 'pass' && Number.isFinite(deviation);
      if (passed) deviations.push(deviation);
      rows.push(`<tr><td><b class="pcb-key">${id}</b> PCB 固定</td><td>${escape(coordinate(hole.center_mm))}</td><td>板孔 Ø${escape(hole.diameter_mm)} → ${targetName}导孔 Ø${escape(base?.measurements?.minimum_diameter_mm ?? '待测')}</td><td>${passed ? `${deviation.toFixed(3)} mm · 通过` : '未取得完整检查证据'}</td></tr>`);
      this.add(id, hole.center_mm, 0x68e6e2, 'pcb');
    });
    lid.forEach((center, i) => {
      const id = `L${i + 1}`;
      rows.push(`<tr><td><b class="lid-key">${id}</b> 上下盖固定</td><td>${escape(coordinate(center))}</td><td>壳体新增锁盖孔</td><td>独立于 PCB 孔位</td></tr>`);
      this.add(id, center, 0xffb66d, 'lid');
    });
    summary.textContent = holes.length
      ? `PCB ${holes.length} 孔：${deviations.length === holes.length ? `${targetName}孔轴最大偏差 ${Math.max(...deviations).toFixed(3)} mm` : '孔轴检查证据未齐全'}；壳体另有 ${lid.length} 个锁盖孔。`
      : '生成外壳后显示原始 STEP 与支撑孔轴的对照。';
    panel.innerHTML = rows.length ? `<table><thead><tr><th>用途</th><th>X, Y（mm）</th><th>来源与尺寸</th><th>检查结果</th></tr></thead><tbody>${rows.join('')}</tbody></table><p class="muted">P 孔坐标来自本任务的板卡 STEP 约束，并由 OpenCascade 重新读取 STEP 圆柱面核验。${targetName}采用自攻螺丝导孔，因此孔径与板孔不同。L 孔用于锁合外壳，由外壳结构生成。彩色环与编号仅为审阅标注。</p>` : '';
    this.setVisible(this.enabled);
  }

  add(id, center, color, part) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(part === 'pcb' ? 2 : 2.8, .22, 6, 24), new THREE.MeshBasicMaterial({color, depthTest:false}));
    ring.position.set(...center);
    ring.userData.originalPosition = ring.position.clone();
    ring.userData.part = part;
    ring.renderOrder = 101;
    this.group.add(ring);
    const label = document.createElement('span');
    label.className = `hole-label ${part}`;
    label.textContent = id;
    this.container.append(label);
    this.labels.push({ring, label});
  }

  setExploded(distance, pcbDistance = 0) {
    for (const {ring} of this.labels) {
      ring.position.copy(ring.userData.originalPosition);
      if (ring.userData.part === 'lid') ring.position.z += distance;
      if (ring.userData.part === 'pcb') ring.position.z += pcbDistance;
    }
  }

  setVisible(enabled) {
    this.enabled = enabled;
    this.group.visible = enabled;
    this.container.hidden = !enabled;
  }

  project() {
    if (!this.enabled) return;
    for (const {ring, label} of this.labels) {
      const position = ring.position.clone().project(this.camera);
      label.hidden = position.z < -1 || position.z > 1 || Math.abs(position.x) > 1 || Math.abs(position.y) > 1;
      label.style.left = `${(position.x + 1) * this.viewport.clientWidth / 2}px`;
      label.style.top = `${(1 - position.y) * this.viewport.clientHeight / 2}px`;
    }
  }
}
