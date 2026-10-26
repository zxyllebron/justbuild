// Mock API and minimal DOM only. No fetch, browser/native app or printer access.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const source=await readFile(path.join(root,'src/justbuild/web/print-review.js'),'utf8');
const {installPrintReview,validatePrintManifest}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.style={};this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.open=false;this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  append(...items){for(const item of items){item.parentNode=this;this.children.push(item);}}
  replaceChildren(...items){for(const c of this.children)c.parentNode=null;this.children=[];this._text='';this.append(...items);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  removeAttribute(name){delete this.attributes[name];}
  addEventListener(type,callback){(this.listeners[type]||=[]).push(callback);}
  showModal(){this.open=true;}
  close(){this.open=false;}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}}
}
const all=node=>[node,...node.children.flatMap(all)];
const byClass=(ui,name)=>all(ui.dialog).find(n=>(n.className||'').split(' ').includes(name));
let now=Date.parse('2026-10-24T11:00:00Z');const originalNow=Date.now;Date.now=()=>now;
const job=()=>({job_id:'1'.repeat(32),state:'ready_to_print',can_edit:true,request_sha256:'2'.repeat(64),artifacts:[{kind:'sliced_3mf',sha256:'3'.repeat(64)}]});
const response=(j=job())=>({status:'awaiting_review',job_id:j.job_id,manifest_sha256:'4'.repeat(64),manifest:{schema_version:'0.1',job_id:j.job_id,review_id:'5'.repeat(32),created_at:new Date(now).toISOString(),expires_at:new Date(now+900000).toISOString(),expires_epoch:now/1000+900,simulation:false,owner_bound:true,print_hold:false,transport:'a1_developer_lan_prototype',printer:{model:'Bambu Lab A1',serial_masked:'***AB12',id_sha256:'6'.repeat(64),firmware:'test-fw',authorization_mode:'developer_lan'},settings:{nozzle_diameter_mm:.4,material:'PLA',bed_type:'Textured PEI Plate',profiles:Object.fromEntries(['machine','process','filament'].map(role=>[role,{id:role+' profile',sha256:'7'.repeat(64)}]))},print_options:{bed_leveling:true,flow_calibration:false,vibration_calibration:false,timelapse:false,use_ams:false,bed_type:'textured_plate'},sliced_artifact_sha256:'3'.repeat(64),sliced_artifact_bytes:633265,bound_print_job_sha256:'8'.repeat(64),input_hashes:{request_sha256:j.request_sha256,cad_result_sha256:'9'.repeat(64),verification_sha256:'a'.repeat(64),source_print_job_sha256:'b'.repeat(64)},risks:[{id:'risk-1',message:'Actual connector travel is not checked.'},{id:'risk-2',message:'Actual hardware dispatch is not accepted.'}]}});
function setup({j=job(),answers=[]}={}){
  const body=new Element('body'),button=new Element('button'),note=new Element('span');button.id='print';note.id='printnote';body.append(button,note);
  const doc={body,createElement:tag=>new Element(tag),getElementById:id=>all(body).find(n=>n.id===id)||null};globalThis.document=doc;
  const calls=[],toasts=[];let index=0,refreshes=0;
  const api=async(path,options)=>{calls.push({path,options});const answer=answers[index++];if(typeof answer==='function')return await answer(path,options);if(answer instanceof Error)throw answer;return answer;};
  let current=j;
  const ui=installPrintReview({api,getJob:()=>current,onRefresh:async()=>{refreshes++;},toast:message=>toasts.push(message)});
  return {ui,body,button,note,calls,toasts,api,setJob(value){current=value;ui.update(value);},get refreshes(){return refreshes;}};
}
const consent=ui=>all(ui.dialog).find(n=>n.getAttribute('data-print-role')==='consent');
let tests=0;
async function check(name,run){await run();tests++;}
await check('installation, update and singleton modal are passive',async()=>{
  const s=setup();assert.equal(s.calls.length,0);assert.equal(s.button.disabled,false);assert.equal(all(s.body).filter(n=>n.tagName==='DIALOG').length,1);const duplicate=installPrintReview({api:s.api,getJob:job});assert.equal(duplicate,s.ui);assert.equal(s.calls.length,0);assert.equal(s.ui.dialog.open,false);s.ui.dispose();
});
await check('read-only share cannot prepare or submit',async()=>{
  const j=job();j.can_edit=false;const s=setup({j});assert.equal(s.button.disabled,true);await s.ui.onClick();assert.equal(s.calls.length,0);assert.match(s.note.textContent,/只读/);s.ui.dispose();
});
await check('every truthy hold blocks preparation without changing hold',async()=>{
  for(const hold of [true,{},'pending',{message:'Review preserved'}]){const j=job();j.print_hold=hold;const s=setup({j});await s.ui.onClick();assert.equal(s.button.disabled,true);assert.equal(s.calls.length,0);assert.equal(j.print_hold,hold);s.ui.dispose();}
});
await check('design hold explanation precedes readonly access without implying ownership claim',async()=>{
  const j=job();j.can_edit=false;j.print_hold={message:'Renewed design review'};const s=setup({j});await s.ui.onClick();assert.equal(s.calls.length,0);assert.equal(s.button.disabled,true);assert.match(s.note.textContent,/待本版审阅，尚未发印/);assert.match(s.note.textContent,/链接仅供查看/);s.ui.dispose();
});
await check('not-ready job never calls preparation',async()=>{
  const j=job();j.state='slicing';const s=setup({j});await s.ui.onClick();assert.equal(s.calls.length,0);assert.match(s.note.textContent,/检查与切片/);s.ui.dispose();
});
await check('prepare is an explicit same-origin JSON intent and never starts automatically',async()=>{
  const r=response(),s=setup({answers:[r]});await s.ui.onClick();assert.equal(s.calls.length,1);assert.equal(s.calls[0].path,`/api/jobs/${job().job_id}/print/prepare`);assert.equal(s.calls[0].options.method,'POST');assert.equal(s.calls[0].options.headers['X-Justbuild-Intent'],'print-review');assert.equal(s.calls[0].options.headers['Content-Type'],'application/json');assert.deepEqual(JSON.parse(s.calls[0].options.body),{});assert.equal(s.ui.dialog.open,true);assert.equal(byClass(s.ui,'print-review-start').disabled,true);assert(s.ui.dialog.textContent.includes('***AB12'));assert(s.ui.dialog.textContent.includes('3'.repeat(64)));assert(s.ui.dialog.textContent.includes(r.manifest.risks[0].message));assert(s.ui.dialog.textContent.includes('flow_calibration')===false);assert(s.ui.dialog.textContent.includes('流量校准'));s.ui.dispose();
});
await check('all exact risk IDs and reviewed hash are bound to one explicit start',async()=>{
  const r=response(),s=setup({answers:[r,{status:'ok',state:'start_acknowledged',milestones:{start_acknowledgement:{status:'observed'}}}]});await s.ui.onClick();const start=byClass(s.ui,'print-review-start');await start.onclick();assert.equal(s.calls.length,1);const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();assert.equal(start.disabled,false);await start.onclick();assert.equal(s.calls.length,2);assert.equal(s.calls[1].path,`/api/jobs/${job().job_id}/print`);assert.deepEqual(JSON.parse(s.calls[1].options.body),{reviewed_manifest_sha256:r.manifest_sha256,acknowledged_risks:['risk-1','risk-2'],action:'start_print'});assert.equal(s.refreshes,1);await s.ui.onClick();assert.equal(s.calls.length,2);assert(s.ui.dialog.textContent.includes('设备已确认启动'));assert.equal(byClass(s.ui,'print-review-start').hidden,true);s.ui.dispose();
});
await check('submit timeout becomes unknown and only a manual monitor request is available',async()=>{
  const s=setup({answers:[response(),new Error('network timeout'),{status:'ok',state:'printing',milestones:{}}]});await s.ui.onClick();const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();await byClass(s.ui,'print-review-start').onclick();assert.equal(s.calls.length,2);assert.match(s.ui.dialog.textContent,/不能重试发送/);assert.equal(byClass(s.ui,'print-review-start').hidden,true);await s.ui.onClick();assert.equal(s.calls.length,2);await byClass(s.ui,'print-review-monitor').onclick();assert.equal(s.calls.length,3);assert(s.calls[2].path.endsWith('/print/monitor'));assert.deepEqual(JSON.parse(s.calls[2].options.body),{});assert.equal(s.calls.filter(c=>c.path.endsWith('/print')).length,1);assert(s.ui.dialog.textContent.includes('设备报告打印中'));assert(s.ui.dialog.textContent.includes('尚无证据'));s.ui.dispose();
});
await check('backend unknown or dispatching state opens read-only status without prepare',async()=>{
  for(const state of ['unknown','dispatching']){const j=job();j.print_receipt={state,reconciliation_required:true};const s=setup({j});await s.ui.onClick();assert.equal(s.calls.length,0);assert.equal(byClass(s.ui,'print-review-start').hidden,true);assert.equal(byClass(s.ui,'print-review-monitor').hidden,false);s.ui.dispose();}
});
await check('configuration navigation accepts only literal local setup route',async()=>{
  for(const url of ['/printer-setup','https://evil.example/printer-setup','javascript:alert(1)','//evil.example','/printer-setup?token=bad']){const s=setup({answers:[{status:'needs_input',configuration_required:'local_printer_pairing',configuration_url:url}]});await s.ui.onClick();const links=all(s.ui.dialog).filter(n=>n.tagName==='A');assert.equal(links.length,url==='/printer-setup'?1:0);if(links.length)assert.equal(links[0].href,'/printer-setup');assert.equal(byClass(s.ui,'print-review-start').hidden,true);s.ui.dispose();}
});
await check('exact 15 minute expiry disables start even after consent',async()=>{
  const r=response(),s=setup({answers:[r]});await s.ui.onClick();const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();assert.equal(byClass(s.ui,'print-review-start').disabled,false);now+=900000;s.ui.update(job());assert.equal(byClass(s.ui,'print-review-start').disabled,true);await byClass(s.ui,'print-review-start').onclick();assert.equal(s.calls.length,1);assert.match(s.ui.dialog.textContent,/过期/);s.ui.dispose();
});
await check('hold or ownership changes after prepare block submit',async()=>{
  for(const change of [j=>j.print_hold={message:'Renewed review needed'},j=>j.can_edit=false]){const r=response(),s=setup({answers:[r]});await s.ui.onClick();const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();const changed=job();change(changed);s.setJob(changed);await byClass(s.ui,'print-review-start').onclick();assert.equal(s.calls.length,1);assert.equal(byClass(s.ui,'print-review-start').disabled,true);s.ui.dispose();}
});
await check('changed task, request or slice cannot submit old manifest',async()=>{
  for(const change of [j=>j.job_id='c'.repeat(32),j=>j.request_sha256='c'.repeat(64),j=>j.artifacts[0].sha256='c'.repeat(64)]){const s=setup({answers:[response()]});await s.ui.onClick();const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();const changed=job();change(changed);s.setJob(changed);await byClass(s.ui,'print-review-start').onclick();assert.equal(s.calls.length,1);assert.equal(byClass(s.ui,'print-review-start').disabled,true);s.ui.dispose();}
});
await check('malformed identity, risk or options manifests fail closed',async()=>{
  for(const change of [r=>r.manifest_sha256='bad',r=>r.manifest.printer.serial_masked='FULLSERIAL123456',r=>r.manifest.owner_bound=false,r=>r.manifest.print_options.use_ams='false',r=>r.manifest.print_options.unreviewed_action=true,r=>r.manifest.risks.push({...r.manifest.risks[0]}),r=>delete r.manifest.settings.profiles.machine,r=>r.manifest.expires_epoch+=1e4]){const r=response();change(r);assert.equal(validatePrintManifest(r,job()).valid,false);const s=setup({answers:[r]});await s.ui.onClick();assert.equal(byClass(s.ui,'print-review-start').hidden,true);assert.equal(s.calls.length,1);s.ui.dispose();}
});
await check('literal text prevents HTML from manifest risks and issues becoming DOM',async()=>{
  const r=response();r.manifest.risks[0].message='<img src=x onerror=alert(1)><script>bad()</script>';const s=setup({answers:[r]});await s.ui.onClick();assert(s.ui.dialog.textContent.includes(r.manifest.risks[0].message));assert.equal(all(s.ui.dialog).filter(n=>['IMG','SCRIPT'].includes(n.tagName)).length,0);s.ui.dispose();
});
await check('rapid duplicate clicks while pending issue only one prepare and one start',async()=>{
  let resolvePrepare,resolveStart;const preparePending=new Promise(resolve=>resolvePrepare=resolve),startPending=new Promise(resolve=>resolveStart=resolve);const s=setup({answers:[()=>preparePending,()=>startPending]});const first=s.ui.onClick();await s.ui.onClick();assert.equal(s.calls.length,1);resolvePrepare(response());await first;const checkbox=consent(s.ui);checkbox.checked=true;checkbox.onchange();const start=byClass(s.ui,'print-review-start');const started=start.onclick();await start.onclick();await s.ui.onClick();assert.equal(s.calls.length,2);resolveStart({status:'needs_input',state:'unknown',reconciliation_required:true});await started;assert.equal(s.calls.length,2);s.ui.dispose();
});
await check('a completed device report is distinct from physical assembly acceptance',async()=>{
  const j=job();j.print_receipt={state:'completed',milestones:{reported_completion:{status:'observed'}}};const s=setup({j});await s.ui.onClick();assert.match(s.ui.dialog.textContent,/设备报告完成/);assert.match(s.ui.dialog.textContent,/实物成品与板卡装配仍需实际核验/);assert.equal(s.calls.length,0);s.ui.dispose();
});
Date.now=originalNow;process.stdout.write(JSON.stringify({passed:true,tests}));
