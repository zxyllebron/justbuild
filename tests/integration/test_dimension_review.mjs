// Pure module checks: no browser, native GUI, network or printer operations.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const threeURL = pathToFileURL(path.join(root,'src/justbuild/web/vendor/three.module.js')).href;
const THREE = await import(threeURL);
class Element {
  constructor(name) { this.nodeName=name; this.children=[]; this.attributes={}; this.style={}; this.dataset={}; this.hidden=false; this.classList={add(){}}; this.mutations=0; }
  setAttribute(name,value) { this.attributes[name]=String(value); this.mutations++; }
  getAttribute(name) { return this.attributes[name]; }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children=[...elements]; }
}
globalThis.document={createElementNS:(_,name)=>new Element(name),createElement:name=>new Element(name)};
const moduleSource=(await readFile(path.join(root,'src/justbuild/web/dimension-review.js'),'utf8')).replace("from 'three'",`from '${threeURL}'`);
const {DimensionReview,deriveDimensionData,layoutDimensionLabels}=await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}`);
const bounds=(min_mm,max_mm)=>({min_mm,max_mm});
const fixture=()=>({proposal:{request:{constraints:{}}},stage_results:{generating:{result:{metadata:{
  units:'mm',input_provenance:{step_sha256:'a'.repeat(64),request_sha256:'b'.repeat(64)},
  outer_bounds:bounds([-999,-999,-999],[999,999,999]), // Deliberately contradictory design setting: only BRep part bounds are dimensional evidence.
  parts:[{id:'base',bounds:bounds([-12,-11,-7],[111,74,12])},{id:'lid',bounds:bounds([-12,-11,12],[111,74,15])}],
  input_solids:[{id:'pcb-original',role:'pcb',bounds:bounds([.0127,.0127,0],[99.9875,59.9874,1.586157])}],
  mounting_holes:[{id:'mount-1',center_mm:[2,58,.79],diameter_mm:2,source:'original STEP cylindrical surfaces'},{id:'mount-2',center_mm:[2,2,.79],diameter_mm:2},{id:'mount-3',center_mm:[98,2,.79],diameter_mm:2},{id:'mount-4',center_mm:[98,58,.79],diameter_mm:2}],
  ports:[{id:'USB-left',component_ref:'USB',face:'x_min',shape:'rectangle',center_mm:[-1.4,31,2.7],width_mm:12.94,height_mm:8.2,access:'external'},{id:'sound-top',component_ref:'BUZZER',face:'z_max',shape:'circle',center_mm:[68,53,5],diameter_mm:5,access:'external'},{id:'internal',component_ref:'MODULE',access:'internal',width_mm:1,height_mm:1}]
}}}}});
function overlay(job=fixture(),width=319,height=420) {
  const camera=new THREE.PerspectiveCamera(38,width/height,.01,10000);camera.up.set(0,0,1);camera.position.set(215,-200,180);camera.lookAt(50,30,4);camera.updateProjectionMatrix();camera.updateMatrixWorld();
  const viewport={clientWidth:width,clientHeight:height},container=new Element('div');
  const review=new DimensionReview(camera,viewport,container);review.update(job);review.setVisible(true);
  return {review,camera,container,viewport};
}
let tests=0;
function check(name,run) { run();tests++; }
check('source is union of BRep parts and original PCB metadata, never a screen or configured design box',()=>{
  const data=deriveDimensionData(fixture());assert.deepEqual(data.shell.size_mm,[123,85,22]);assert.equal(data.board.size_mm[0],99.9748);assert.equal(data.board.size_mm[2],1.586157);assert.deepEqual(data.shell.source_paths,['metadata.parts.base.bounds','metadata.parts.lid.bounds']);assert.equal(data.source_sha256,'a'.repeat(64));
});
check('absent or invalid dimensional source remains unknown',()=>{
  const empty=deriveDimensionData({});assert.equal(empty.shell,null);assert.equal(empty.board,null);assert.equal(empty.holes.length,0);
  const job=fixture();job.stage_results.generating.result.metadata.units='m';assert.equal(deriveDimensionData(job).shell,null);
  const missingLid=fixture();missingLid.stage_results.generating.result.metadata.parts.pop();assert.equal(deriveDimensionData(missingLid).shell,null);
  const bad=fixture();bad.stage_results.generating.result.metadata.input_solids[0].bounds.max_mm[2]=NaN;assert.equal(deriveDimensionData(bad).board,null);
  const {review}=overlay({});review.update({}, {bounds:new THREE.Box3(new THREE.Vector3(),new THREE.Vector3(100,100,100))});assert.equal(review.getSummary().measurements.length,0);assert.match(review.caption.textContent,/未确认|尚无/);
});
check('shell and board modes provide every axis with source and millimeter units',()=>{
  const {review}=overlay();assert.equal(review.getSummary().measurements.length,3);assert.deepEqual(review.getSummary().measurements.map(m=>m.value_mm),[123,85,22]);review.setMode('board');const result=review.getSummary();assert.equal(result.measurements.length,3);assert.equal(result.units,'mm');assert.equal(result.measurements[2].label,'厚 1.59 mm');assert(result.measurements.every(m=>m.source.includes('STEP')));
});
check('hole diameters and center spans derive from actual confirmed centers, including nonrectangular patterns',()=>{
  const {review}=overlay();review.setMode('holes');let summary=review.getSummary();assert.equal(summary.measurements.length,6);assert.deepEqual(summary.holes.center_span_xy_mm,[96,56]);assert.equal(summary.measurements.filter(m=>m.label.includes('Ø2.00 mm')).length,4);
  const changed=fixture();changed.stage_results.generating.result.metadata.mounting_holes[3].center_mm[0]=101.2345;review.update(changed);summary=review.getSummary();assert.equal(summary.holes.center_span_xy_mm[0],99.2345);assert(summary.measurements.some(m=>m.label==='孔中心 X 99.23 mm'));
});
check('feature windows use explicit declared opening dimensions on the generated shell face',()=>{
  const {review}=overlay();review.setFeature({id:'USB',label:'USB-C',port_ids:['USB-left']});const data=review.getSummary();assert.equal(data.mode,'feature');assert.deepEqual(data.measurements.map(m=>m.value_mm),[12.94,8.2]);assert(data.measurements.every(m=>m.source.includes('设计开口尺寸')));assert(data.measurements.every(m=>m.start_mm[0]===-12&&m.end_mm[0]===-12));assert.match(review.caption.textContent,/USB-C · 设计开口尺寸/);
  review.setFeature({id:'BUZZER',label:'蜂鸣器'});assert.equal(review.getSummary().measurements[0].label,'Ø5.00 mm');assert.equal(review.getSummary().measurements[0].start_mm[2],15);
});
check('no invented external dimensions for internal or unmatched features',()=>{
  const {review}=overlay();review.setFeature({id:'MODULE',label:'内部模块',bounds:bounds([0,0,0],[10,20,4])});assert.equal(review.getSummary().measurements.length,0);assert.equal(review.caption.textContent,'该组件未提供外部开口尺寸');review.setFeature(null);assert.equal(review.getSummary().mode,'shell');
});
check('all labels remain within 319 pixel viewport without overlap, NaN or overflow',()=>{
  const {review}=overlay();for(const mode of ['shell','board','holes']){
    review.setMode(mode);const visible=review.records.filter(r=>r.group.style.display!=='none');assert.equal(visible.length,mode==='holes'?6:3);const rects=visible.map(r=>Object.fromEntries(['x','y','width','height'].map(k=>[k,Number(r.rect.getAttribute(k))])));
    for(const rect of rects){assert(Object.values(rect).every(Number.isFinite));assert(rect.x>=9.99&&rect.y>=9.99);assert(rect.x+rect.width<=309.01);assert(rect.y+rect.height<=410.01);}
    for(let i=0;i<rects.length;i++)for(let j=0;j<i;j++){const a=rects[i],b=rects[j];assert(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);}
    for(const record of visible)for(const node of [record.extensionA,record.extensionB,record.line,record.leader,record.capA,record.capB])for(const value of Object.values(node.attributes))assert(!/NaN|Infinity/.test(value));
  }
});
check('camera changes move labels, points behind camera hide and unchanged projections reuse nodes',()=>{
  const {review,camera}=overlay();const first=review.records[0].text.getAttribute('x'),record=review.records[0];const mutationCount=record.text.mutations;review.project();assert.equal(record.text.mutations,mutationCount);assert.equal(review.records[0],record);
  camera.position.set(-180,-100,170);camera.lookAt(50,30,4);review.project();assert.notEqual(review.records[0].text.getAttribute('x'),first);
  camera.position.set(0,0,300);camera.lookAt(0,0,600);review.project();assert.equal(review.container.dataset.visibleLabels,'0');assert(review.records.every(r=>r.group.style.display==='none'));
});
check('annotation does not mutate job geometry, numeric values or camera position',()=>{
  const job=fixture(),before=JSON.stringify(job),{review,camera}=overlay(job),cameraBefore=camera.position.toArray();review.setMode('holes');review.project();review.setFeature({id:'USB',label:'<img onerror=alert(1)>'});review.setVisible(false);review.setVisible(true);assert.equal(JSON.stringify(job),before);assert.deepEqual(camera.position.toArray(),cameraBefore);assert(review.caption.textContent.includes('<img onerror=alert(1)>'));
});
check('pure layout clamps a crowded and narrow label set',()=>{
  const labels=Array.from({length:6},(_,id)=>({id,label:`P${id+1} Ø2.00 mm`,x:159,y:210}));const positions=layoutDimensionLabels(labels,319,420);assert.equal(positions.length,6);assert(positions.every(r=>r.x>=10&&r.y>=10&&r.x+r.width<=309&&r.y+r.height<=410));
});
process.stdout.write(JSON.stringify({passed:true,tests}));
