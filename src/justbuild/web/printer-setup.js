"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const form = byId("pair-form"), message = byId("message");
  const details = byId("status-details"), fingerprints = byId("fingerprints");
  const probeButton = byId("probe"), pairButton = byId("pair"), checkButton = byId("check");
  let observation = null, busy = false;
  function say(text, error = false) { message.textContent = text; message.classList.toggle("error", error); }
  function setBusy(value) {
    busy = value; probeButton.disabled = value; checkButton.disabled = value;
    for (const input of form.querySelectorAll("input")) input.disabled = value;
    byId("fingerprint-confirmed").disabled = value;
    pairButton.disabled = value || !observation || !byId("fingerprint-confirmed").checked;
  }
  function invalidate() {
    observation = null; fingerprints.hidden = true; byId("fingerprint-confirmed").checked = false;
    pairButton.disabled = true;
  }
  async function api(path, body) {
    const response = await fetch(path, {method: body === undefined ? "GET" : "POST", credentials: "same-origin",
      cache: "no-store", redirect: "error", headers: body === undefined ? {} : {
        "Content-Type": "application/json", "X-Justbuild-Intent": "printer-setup"},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const value = await response.json();
    if (!response.ok) {
      const error = value.detail || value;
      throw new Error(typeof error === "object" ? (error.message || "本机配对操作未完成。") : String(error));
    }
    return value;
  }
  function showStatus(profile) {
    if (profile?.status === "configured") {
      say(`已保存 ${profile.model || "A1"} · ${profile.host} · ${profile.serial_masked || ""}。可以进行只读连接检查；尚未发印。`);
      checkButton.hidden = false;
      details.textContent = `固件：${profile.firmware || "未确认"}\nDeveloper LAN：${profile.developer_mode_confirmed ? "用户已确认" : "未确认"}\nMQTT / FTPS 证书：${profile.mqtt_pin_present && profile.ftp_pin_present ? "已固定" : "未完整固定"}`;
      details.hidden = false;
    } else {
      say("尚未完成本机配对。请按打印机屏幕填写资料，然后读取证书。");
      details.hidden = true; checkButton.hidden = true;
    }
  }
  form.addEventListener("input", () => { if (!busy) invalidate(); });
  byId("fingerprint-confirmed").addEventListener("change", () => setBusy(busy));
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    invalidate(); setBusy(true); say("正在读取两个 TLS 服务的证书，尚未发送访问码…");
    try {
      const host = byId("host").value.trim();
      const developer = byId("developer-confirmed").checked;
      const value = await api("/api/printers/local/probe", {host, developer_mode_confirmed: developer});
      // Ignore results if form fields changed while the read was in flight.
      if (host !== byId("host").value.trim() || !byId("developer-confirmed").checked) {
        say("设备资料已更改，请重新读取证书。", true); return;
      }
      observation = value;
      byId("observed-host").textContent = value.host;
      byId("mqtt-pin").textContent = value.mqtt_certificate_sha256;
      byId("ftp-pin").textContent = value.ftp_certificate_sha256;
      fingerprints.hidden = false;
      say("已读取证书。请先核对目标设备，再确认保存配对。");
    } catch (error) { say(error.message || "证书探测失败。", true); }
    finally { setBusy(false); }
  });
  pairButton.addEventListener("click", async () => {
    if (busy || !observation || !byId("fingerprint-confirmed").checked || !form.reportValidity()) return;
    const captured = observation;
    if (captured.host !== byId("host").value.trim()) { invalidate(); return; }
    setBusy(true); say("正在把配对资料保存到本机私有配置…");
    try {
      const result = await api("/api/printers/local/pair", {
        probe_id: captured.probe_id, host: captured.host,
        mqtt_certificate_sha256: captured.mqtt_certificate_sha256,
        ftp_certificate_sha256: captured.ftp_certificate_sha256,
        serial: byId("serial").value.trim(), access_code: byId("access-code").value,
        firmware: byId("firmware").value.trim(), developer_mode_confirmed: byId("developer-confirmed").checked,
        fingerprint_confirmed: byId("fingerprint-confirmed").checked});
      byId("access-code").value = ""; invalidate(); showStatus(result.profile);
    } catch (error) { say(error.message || "无法保存配对。", true); }
    finally { setBusy(false); }
  });
  checkButton.addEventListener("click", async () => {
    if (busy) return; setBusy(true); say("正在读取状态和版本并检查 FTPS 登录，不会启动打印…");
    try {
      const result = await api("/api/printers/local/check", {});
      const printer = result.printer || {};
      const connected = result.connection_verified === true;
      const stateLabel = ({RUNNING:"正在打印",PAUSE:"已暂停",PREPARE:"准备中",IDLE:"空闲",FINISH:"已完成"})[printer.state] || printer.state || "未读到";
      const progress = typeof printer.progress_percent === "number" ? ` · ${printer.progress_percent}%` : "";
      say(connected && result.ready_for_new_print !== true ? `已连接并核对设备与固件；打印机${stateLabel}${progress}。当前任务保持，不提交新打印。` : result.status === "ok" ? "只读连接检查通过，设备与固件匹配，当前可准备新任务。尚未上传文件或启动打印。" : "只读检查未全部通过，请核对设备状态和配对资料。", !connected && result.status !== "ok");
      details.textContent = `状态：${printer.state || "未读到"}\n实际固件：${printer.firmware_observed || "未读到"}\n设备身份匹配：${printer.identity_verified ? "是" : "未确认"}\nMQTT 登录：${result.mqtt_authenticated ? "已确认" : "未确认"}\nFTPS 登录：${result.ftps_authenticated ? "已确认" : "未确认"}\n${(result.issues || []).map((item) => item.code + "：" + item.message).join("\n")}`;
      details.hidden = false;
    } catch (error) { say(error.message || "只读连接检查失败。", true); }
    finally { setBusy(false); }
  });
  api("/api/printers/local").then((value) => showStatus(value.profile))
    .catch((error) => say(error.message || "无法读取配对状态。", true));
})();
