const DIGEST = /^[a-f0-9]{64}$/;
const JOB_ID = /^[a-f0-9]{32}$/;
const OPTION_KEYS = ['bed_leveling','flow_calibration','vibration_calibration','timelapse','use_ams','bed_type'];
const STATE_NAMES = {unknown:'结果未确认',dispatching:'正在发送',uploaded:'文件已上传',printer_received:'设备已接收',start_acknowledged:'设备已确认启动',printing:'设备报告打印中',paused:'设备报告已暂停',completed:'设备报告完成',failed:'设备报告失败',cancelled:'设备报告已取消',blocked:'发送受阻'};
const RISK_NAMES = {lan_prototype_not_hardware_verified:'LAN 原型尚未完成实机验收',printer_state_and_material_not_observed:'实际设备与耗材尚未读回确认',toolpath_safety_not_verified:'尚无完整路径与实物安全验收',synthetic_design:'本任务为合成参考设计'};
const OPTION_NAMES = {bed_leveling:'热床调平',flow_calibration:'流量校准',vibration_calibration:'振动校准',timelapse:'延时摄影',use_ams:'使用 AMS',bed_type:'平台类型'};
const installations = new WeakMap();
const truthyHold = job => Boolean(job?.print_hold);
const publicReceipt = job => job?.print_receipt || job?.print_dispatch || job?.stage_results?.printing?.result || null;
const attemptState = receipt => receipt && (receipt.attempt_consumed === true || receipt.reconciliation_required === true || receipt.external_side_effects_possible === true || ['unknown','dispatching','uploaded','printer_received','start_acknowledged','printing','paused','completed','failed','cancelled'].includes(receipt.state));
const displayValue = value => typeof value === 'boolean' ? value ? '开启' : '关闭' : value === null || value === undefined ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const textError = error => typeof error?.message === 'string' ? error.message.slice(0,1000) : '请求未完成，请查看连接与任务状态。';

export function validatePrintManifest(response, job, nowSeconds = Date.now() / 1000) {
  const m = response?.manifest;
  if (!job || !JOB_ID.test(job.job_id || '') || response?.job_id !== job.job_id || !m || m.job_id !== job.job_id || !DIGEST.test(response.manifest_sha256 || '')) return {valid:false,reason:'打印审阅版本与当前任务不匹配。'};
  if (!['awaiting_review','needs_review_hold'].includes(response.status) || m.schema_version !== '0.1' || m.simulation !== false || m.owner_bound !== true) return {valid:false,reason:'没有取得属于当前会话的真实打印审阅。'};
  if (m.printer?.model !== 'Bambu Lab A1' || !/^\*{3}[A-Za-z0-9]{4}$/.test(m.printer?.serial_masked || '') || !DIGEST.test(m.printer?.id_sha256 || '') || !DIGEST.test(m.sliced_artifact_sha256 || '')) return {valid:false,reason:'打印机或切片身份不完整。'};
  if (!JOB_ID.test(m.review_id || '') || !DIGEST.test(m.bound_print_job_sha256 || '') || ['request_sha256','cad_result_sha256','verification_sha256','source_print_job_sha256'].some(key => !DIGEST.test(m.input_hashes?.[key] || ''))) return {valid:false,reason:'设计、检查或打印任务版本身份不完整。'};
  if (!Number.isFinite(m.settings?.nozzle_diameter_mm) || m.settings.nozzle_diameter_mm <= 0 || typeof m.settings?.material !== 'string' || !m.settings.material || typeof m.settings?.bed_type !== 'string' || !m.settings.bed_type || ['machine','process','filament'].some(role => typeof m.settings?.profiles?.[role]?.id !== 'string' || !m.settings.profiles[role].id || !DIGEST.test(m.settings?.profiles?.[role]?.sha256 || ''))) return {valid:false,reason:'喷嘴、耗材、平台或切片配置身份不完整。'};
  const created = Date.parse(m.created_at) / 1000, expires = Date.parse(m.expires_at) / 1000;
  if (![created,expires,m.expires_epoch,nowSeconds].every(Number.isFinite) || Math.abs(expires - m.expires_epoch) > 1 || expires <= created || expires - created > 900 || m.expires_epoch - nowSeconds > 901) return {valid:false,reason:'审阅有效期无效；需要重新取得当前版本。'};
  if (!m.print_options || OPTION_KEYS.some(key => key === 'bed_type' ? typeof m.print_options[key] !== 'string' || !m.print_options[key] : typeof m.print_options[key] !== 'boolean') || Object.keys(m.print_options).some(key => !OPTION_KEYS.includes(key))) return {valid:false,reason:'打印选项不完整或不受当前审阅界面支持。'};
  if (!Array.isArray(m.risks) || m.risks.length > 256 || m.risks.some(r => !r || typeof r.id !== 'string' || !r.id || typeof r.message !== 'string') || new Set(m.risks.map(r => r.id)).size !== m.risks.length) return {valid:false,reason:'风险清单不完整；不能授权打印。'};
  if (job.request_sha256 && m.input_hashes?.request_sha256 !== job.request_sha256) return {valid:false,reason:'设计需求已更新，请重新审阅当前版本。'};
  const sliced = (job.artifacts || []).find(a => a.kind === 'sliced_3mf');
  if (sliced && sliced.sha256 !== m.sliced_artifact_sha256) return {valid:false,reason:'当前切片文件与本次审阅版本不同。'};
  return {valid:true,expired:m.expires_epoch <= nowSeconds};
}

function create(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}
function appendValue(list,label,value,code=false) {
  const term = create('dt','',label), definition = create('dd',code?'print-review-hash':'',displayValue(value));
  list.append(term,definition);
}
function issueMessages(result) {
  return Array.isArray(result?.issues) ? result.issues.filter(i => typeof i?.message === 'string').map(i => i.message).join('\n') : '';
}

// Installation and update are passive. Only explicit clicks invoke prepare,
// start, or read-only monitor. No polling, auto-start or retry is scheduled.
export function installPrintReview({api,getJob,onRefresh,toast = () => {}} = {}) {
  if (typeof api !== 'function' || typeof getJob !== 'function') throw new TypeError('Print review requires api and getJob functions');
  if (installations.has(document)) return installations.get(document);
  const ownerDocument = document, attempts = new Map();
  let prepared = null, busy = false, expiredTimer = null, consent = null, activeJobId = null, disposed = false;
  const dialog = create('dialog','print-review-dialog'); dialog.id = 'justbuild-print-review'; dialog.setAttribute('aria-labelledby','justbuild-print-review-title'); dialog.setAttribute('aria-describedby','justbuild-print-review-status');
  const header = create('div','print-review-header'), heading = create('div'), eyebrow = create('span','print-review-eyebrow','打印审阅'), title = create('h2','','确认本次打印'); title.id = 'justbuild-print-review-title';
  const closeButton = create('button','print-review-close','×'); closeButton.type = 'button'; closeButton.setAttribute('aria-label','关闭打印审阅');
  heading.append(eyebrow,title); header.append(heading,closeButton);
  const status = create('p','print-review-status',''); status.id = 'justbuild-print-review-status'; status.setAttribute('role','status');
  const body = create('div','print-review-body'), actions = create('div','print-review-actions');
  const cancel = create('button','print-review-cancel','关闭'), monitor = create('button','print-review-monitor','刷新设备状态'), start = create('button','print-review-start','确认并开始打印');
  for (const button of [cancel,monitor,start]) button.type = 'button';
  monitor.hidden = true; start.hidden = true; start.disabled = true;
  actions.append(cancel,monitor,start); dialog.append(header,status,body,actions); document.body.append(dialog);

  function close() { if (dialog.open && typeof dialog.close === 'function') dialog.close(); else {dialog.open = false;dialog.removeAttribute('open');} clearTimeout(expiredTimer);expiredTimer = null; }
  closeButton.onclick = close; cancel.onclick = close;
  dialog.addEventListener('cancel',() => {clearTimeout(expiredTimer);expiredTimer = null;});
  dialog.addEventListener('click',event => {if (event.target === dialog) close();});
  function open() { if (!dialog.open) {if (typeof dialog.showModal === 'function') dialog.showModal();else {dialog.open = true;dialog.setAttribute('open','');dialog.setAttribute('aria-modal','true');}} }
  function setStatus(message,tone='') { status.textContent = message; status.dataset.tone = tone; }
  function clearBody() { body.replaceChildren();consent = null;start.hidden = true;monitor.hidden = true;start.disabled = true;clearTimeout(expiredTimer);expiredTimer = null; }
  function current() { return getJob(); }
  function pendingMessage(job) {
    if (!job) return '请先选择已完成切片的任务。';
    if (truthyHold(job)) return `此设计仍待本版审阅，尚未发印。${job.can_edit !== true ? ' 当前链接仅供查看。' : ''}${typeof job.print_hold?.message === 'string' ? ' ' + job.print_hold.message : ''}`;
    if (job.can_edit !== true) return '当前是只读审阅；只有拥有此任务的会话可以打印。';
    if (job.state !== 'ready_to_print') return '需要完成当前版本的几何检查与切片。';
    return '';
  }
  function update(job = current()) {
    if (disposed) return;
    const button = document.getElementById('print'), note = document.getElementById('printnote');
    const receipt = attempts.get(job?.job_id) || publicReceipt(job), blocked = pendingMessage(job);
    if (button) {button.onclick = onClick;button.disabled = busy || Boolean(blocked);button.textContent = attemptState(receipt) ? '查看打印状态' : '审阅并打印';button.title = blocked || (attemptState(receipt) ? '仅查看当前发送记录，不会再次发送。' : '核对具体打印机、文件版本与风险后开始。');}
    if (note) note.textContent = blocked || (attemptState(receipt) ? `${STATE_NAMES[receipt.state] || '已有发送记录'} · 可只读刷新，不会重复发送` : '连接自己的 A1，核对本次配置后明确开始。');
    if (prepared && dialog.open) refreshStartGate();
  }
  function refreshStartGate() {
    if (!prepared || start.hidden) return;
    const job = current(), check = validatePrintManifest(prepared,job), blocked = pendingMessage(job);
    const held = prepared.manifest.print_hold;
    const alreadyAttempted = attemptState(attempts.get(activeJobId)) || attemptState(publicReceipt(job));
    start.disabled = busy || !check.valid || check.expired === true || Boolean(blocked) || Boolean(held) || alreadyAttempted || consent?.checked !== true;
    if (!check.valid) setStatus(check.reason,'warning');
    else if (blocked || held) setStatus(blocked || '此版本仍保留打印审阅，不能启动。','warning');
    else if (check.expired) setStatus('本次审阅已过期。关闭后重新打开，核对当前版本。','warning');
    else if (alreadyAttempted) setStatus('已有发送尝试；请只读刷新设备状态，不要重复发送。','warning');
    else if (!busy) setStatus(`审阅在 ${new Date(prepared.manifest.expires_epoch * 1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})} 失效。开始后将提交下面的精确版本。`);
  }
  function renderConfiguration(result) {
    clearBody();title.textContent = '连接自己的 A1';setStatus('尚未取得可用的打印机配置。','warning');
    body.append(create('p','print-review-description',issueMessages(result) || '请先在本机完成一次打印机配对。'));
    // No backend-controlled external or protocol URL can become a navigation link.
    if (result.configuration_url === '/printer-setup') {const link = create('a','print-review-setup','连接打印机');link.href = '/printer-setup';body.append(link);}
  }
  function renderManifest(result,job) {
    clearBody();prepared = result;activeJobId = job.job_id;title.textContent = '确认本次打印';
    const checked = validatePrintManifest(result,job);
    if (!checked.valid) {prepared = null;setStatus(checked.reason,'warning');return;}
    const m = result.manifest, identity = create('div','print-review-identity');
    identity.append(create('strong','',`${m.printer.model} · ${m.printer.serial_masked}`),create('span','',`固件 ${displayValue(m.printer.firmware)} · ${displayValue(m.transport)}`));body.append(identity);
    const settings = create('dl','print-review-settings');
    appendValue(settings,'喷嘴',Number.isFinite(m.settings?.nozzle_diameter_mm) ? `${m.settings.nozzle_diameter_mm} mm` : '未提供');appendValue(settings,'耗材',m.settings?.material);appendValue(settings,'平台',m.settings?.bed_type);
    for (const key of OPTION_KEYS) appendValue(settings,OPTION_NAMES[key],m.print_options[key]);body.append(settings);
    const hash = create('div','print-review-file');hash.append(create('span','','切片 3MF · SHA-256'),create('code','print-review-hash',m.sliced_artifact_sha256));
    if (Number.isSafeInteger(m.sliced_artifact_bytes) && m.sliced_artifact_bytes >= 0) hash.append(create('small','',`${m.sliced_artifact_bytes.toLocaleString('zh-CN')} bytes`));body.append(hash);
    const details = create('details','print-review-details'), summary = create('summary','','版本与配置来源'), bindings = create('dl','print-review-bindings');details.append(summary);
    appendValue(bindings,'审阅 SHA-256',result.manifest_sha256,true);appendValue(bindings,'绑定打印任务 SHA-256',m.bound_print_job_sha256,true);
    for (const [key,value] of Object.entries(m.input_hashes || {})) appendValue(bindings,key,value,true);
    for (const [role,value] of Object.entries(m.settings?.profiles || {})) {appendValue(bindings,`${role} 配置`,value.id);appendValue(bindings,`${role} SHA-256`,value.sha256,true);}details.append(bindings);body.append(details);
    const riskSection = create('section','print-review-risks'), riskTitle = create('h3','',`本次风险与未检查项 · ${m.risks.length}`), list = create('ul');
    for (const risk of m.risks) {const item = create('li');if (RISK_NAMES[risk.id]) item.append(create('b','',RISK_NAMES[risk.id]));item.append(create('span','',risk.message));list.append(item);}riskSection.append(riskTitle,list);body.append(riskSection);
    const consentLabel = create('label','print-review-consent');consent = create('input');consent.type = 'checkbox';consent.checked = false;consent.setAttribute('data-print-role','consent');consent.onchange = refreshStartGate;consentLabel.append(consent,create('span','','我已核对具体打印机、切片版本和全部上述风险，授权本次开始打印。'));body.append(consentLabel);
    start.hidden = false;refreshStartGate();
    const wait = Math.max(0,m.expires_epoch * 1000 - Date.now() + 10);expiredTimer = setTimeout(refreshStartGate,wait);expiredTimer?.unref?.();
  }
  function renderReceipt(receipt,jobId) {
    clearBody();prepared = null;activeJobId = jobId;title.textContent = '打印发送状态';monitor.hidden = false;monitor.disabled = busy || current()?.can_edit !== true || current()?.job_id !== jobId;
    const state = receipt?.state || 'unknown';
    const uncertain = ['unknown','dispatching'].includes(state) || receipt?.reconciliation_required === true;
    setStatus(uncertain ? '发送结果尚未确认。请只读刷新设备状态；不能重试发送。' : `${STATE_NAMES[state] || '已有发送记录'}。此窗口只查看当前记录，不会重复发送。`,uncertain?'warning':'');
    body.append(create('div','print-review-receipt-state',STATE_NAMES[state] || state));
    const milestones = create('dl','print-review-settings');
    for (const [key,label] of [['upload','上传记录'],['printer_receipt','设备接收'],['start_acknowledgement','启动确认'],['reported_completion','设备报告完成']]) appendValue(milestones,label,receipt?.milestones?.[key]?.status === 'observed' ? '已观察到' : '尚无证据');body.append(milestones);
    const issues = issueMessages(receipt);if (issues) body.append(create('p','print-review-description',issues));
    if (state === 'completed') body.append(create('p','print-review-description','设备状态报告完成；实物成品与板卡装配仍需实际核验。'));
  }
  async function refreshPage() {if (typeof onRefresh === 'function') {try {await onRefresh();} catch { /* The action outcome stays visible even if the page refresh fails. */ }} }
  async function onClick() {
    const job = current(), blocked = pendingMessage(job);
    if (busy || disposed) return;
    if (blocked) {toast(blocked);update(job);return;}
    activeJobId = job.job_id;
    const previous = attempts.get(job.job_id) || publicReceipt(job);
    if (attemptState(previous)) {renderReceipt(previous,job.job_id);open();return;}
    busy = true;prepared = null;clearBody();title.textContent = '准备本次审阅';setStatus('读取当前版本与打印机配置…');open();update(job);
    try {
      const result = await api(`/api/jobs/${job.job_id}/print/prepare`,{method:'POST',headers:{'Content-Type':'application/json','X-Justbuild-Intent':'print-review'},body:'{}'});
      if (disposed) return;
      if (current()?.job_id !== job.job_id) {clearBody();setStatus('当前任务已切换，请关闭后审阅新任务。','warning');return;}
      if (attemptState(result)) {attempts.set(job.job_id,result);renderReceipt(result,job.job_id);}
      else if (result?.configuration_required || result?.configuration_url) renderConfiguration(result);
      else if (result?.manifest) renderManifest(result,current());
      else {clearBody();title.textContent = '暂时不能打印';setStatus(issueMessages(result) || '没有取得可提交的打印审阅。','warning');}
    } catch (error) {if (!disposed) {clearBody();title.textContent = '无法准备打印审阅';setStatus(textError(error),'warning');}}
    finally {busy = false;update(current());if (monitor.hidden === false) monitor.disabled = current()?.can_edit !== true || current()?.job_id !== activeJobId;}
  }
  start.onclick = async () => {
    if (busy || !prepared || disposed) return;
    refreshStartGate();if (start.disabled) return;
    const jobId = activeJobId, review = prepared;
    busy = true;start.disabled = true;monitor.disabled = true;consent.disabled = true;
    const pending = {status:'needs_input',state:'dispatching',reconciliation_required:true,retry_allowed:false};attempts.set(jobId,pending);setStatus('正在提交本次授权。请等待回执；不会自动重发。');update(current());
    try {
      const receipt = await api(`/api/jobs/${jobId}/print`,{method:'POST',headers:{'Content-Type':'application/json','X-Justbuild-Intent':'print-review'},body:JSON.stringify({reviewed_manifest_sha256:review.manifest_sha256,acknowledged_risks:review.manifest.risks.map(r => r.id),action:'start_print'})});
      if (disposed) return;
      // Every start request consumes this UI attempt, even an unrecognized reply.
      const safeReceipt = {...receipt,state:receipt?.state || 'unknown',retry_allowed:false,attempt_consumed:true};attempts.set(jobId,safeReceipt);renderReceipt(safeReceipt,jobId);
    } catch (error) {
      const unknown = {status:'needs_input',state:'unknown',retry_allowed:false,reconciliation_required:true,external_side_effects_possible:true,issues:[{message:textError(error)}]};attempts.set(jobId,unknown);if (!disposed) renderReceipt(unknown,jobId);
    } finally {busy = false;if (!disposed) {monitor.disabled = current()?.can_edit !== true || current()?.job_id !== jobId;update(current());await refreshPage();}}
  };
  monitor.onclick = async () => {
    if (busy || disposed || current()?.can_edit !== true || current()?.job_id !== activeJobId) return;
    const jobId = activeJobId;busy = true;monitor.disabled = true;setStatus('只读刷新当前设备状态…');
    try {
      const receipt = await api(`/api/jobs/${jobId}/print/monitor`,{method:'POST',headers:{'Content-Type':'application/json','X-Justbuild-Intent':'print-review'},body:'{}'});
      if (disposed) return;
      const safeReceipt = {...receipt,state:receipt?.state || 'unknown',retry_allowed:false,attempt_consumed:true};attempts.set(jobId,safeReceipt);renderReceipt(safeReceipt,jobId);
    } catch (error) {if (!disposed) setStatus(`状态刷新未完成：${textError(error)}。没有重新发送打印任务。`,'warning');}
    finally {busy = false;if (!disposed) {monitor.disabled = current()?.can_edit !== true || current()?.job_id !== jobId;update(current());await refreshPage();}}
  };
  function dispose() {disposed = true;clearTimeout(expiredTimer);const button = document.getElementById('print');if (button?.onclick === onClick) button.onclick = null;dialog.remove();installations.delete(ownerDocument);}
  const installation = {update,onClick,close,dispose,dialog};installations.set(ownerDocument,installation);update();return installation;
}
