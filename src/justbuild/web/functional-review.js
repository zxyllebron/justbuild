const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const faceNames = {x_min:'左侧',x_max:'右侧',y_min:'前侧',y_max:'后侧',z_min:'底部',z_max:'顶部'};
const names = {USB1:'USB-C',CARD1:'TF 存储卡',CARD2:'SIM 卡',U11:'KEY 按钮',SW2:'FEL 按钮',SW3:'ADB 开关',SW4:'4G 开关',P1:'CAM 相机排线',FPC1:'FPC1 排线',RF1:'WiFi / BT 天线',RF2:'GPS 天线',RF3:'4G 天线',U8:'UART3 调试排针',U10:'TX / RX / GND 排针',H3:'顶部 4P 排针',H2:'3V3 附近 4P 排针',CN5:'MIC 麦克风连接',CN1:'SPK 扬声器连接',BUZZER1:'蜂鸣器声路',U6:'AIR780E 通信模块',X2:'晶振'};

export function renderFunctionalReview(job, container, focus) {
  const request = job.proposal?.request;
  const features = request?.constraints?.functional_features || [];
  const ports = request?.constraints?.ports || [];
  const checks = job.stage_results?.verifying?.result?.checks || [];
  container.hidden = !features.length;
  if (!features.length) return;
  container.innerHTML = `<div class="panel-title"><h3>接口与操作空间</h3><span class="small-tag">${features.length} 个命名组件 · 原始 STEP 对应</span></div><p class="muted">逐项列出按钮、卡槽、排线、调试、天线与声路。服务窗口的空腔和接近路径由实体检查；实际插头配合、按压/拨动/锁扣行程、线缆与外设仍需对应模型或实物核验。点击一行定位。</p><div class="functional-list">${features.map((f,i)=>{
    const linked = ports.filter(p=>(f.port_ids||[]).includes(p.id));
    const active = linked.filter(p=>!['covered','internal'].includes(p.access));
    const results = checks.filter(c=>active.some(p=>p.id===c.feature_id)&&c.required);
    const state = !active.length?'内部保留':!results.length?'待实体核验':results.some(c=>c.status==='fail')?'发现问题':results.some(c=>!['pass','not_applicable'].includes(c.status))?'核验未齐':'声明窗口通过';
    return `<button class="functional-row" data-functional="${i}"><span><b>${escape(f.id)} · ${escape(names[f.id]||f.label)}</b><small>${escape(f.kind)} · ${f.solid_indices?.length||0} 个实体</small></span><span>${escape(active.length?active.map(p=>faceNames[p.face]||p.face).join(' / '):'无需外部开孔')}<small>${state}</small></span></button>`;
  }).join('')}</div>`;
  container.querySelectorAll('[data-functional]').forEach(row=>row.onclick=()=>focus(features[Number(row.dataset.functional)]));
}
