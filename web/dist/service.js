(function () {
  const content = document.getElementById("content");
  const topTitle = document.getElementById("topTitle");
  const toastBox = document.getElementById("toast");
  let currentUser = null;
  let activeView = "home";
  const labels = {
    PENDING: "待分流", ROUTED: "已分流", OPEN: "待领取", PROCESSING: "跟进中", RESOLVED: "已解决", CLOSED: "已关闭",
    low: "低", medium: "中", high: "高", urgent: "紧急", WECHAT: "微信", INBOUND_CALL: "呼入电话", ONLINE: "在线咨询", APP_CHAT: "App 会话",
    clear: "内容清楚", not_clear: "内容不清楚", not_personalized: "不够个性化", cost_question: "费用疑问", recovery_question: "康复疑问",
    medication_question: "用药疑问", recommendation_question: "推荐疑问", claim_progress: "理赔进度", claim_material: "理赔材料",
    coverage_rule: "保障规则", value_added_service: "增值服务", human_service_request: "人工服务请求", other: "其他"
  };
  function esc(value) { const node = document.createElement("div"); node.textContent = value == null ? "" : String(value); return node.innerHTML; }
  function label(value) { return labels[value] || value || "—"; }
  function formatTime(value) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }
  function status(value) { const tone = ["ROUTED", "RESOLVED"].includes(value) ? " success" : ["urgent", "high"].includes(String(value).toLowerCase()) ? " danger" : ["PENDING", "OPEN"].includes(value) ? " warning" : ""; return `<span class="tag${tone}">${esc(label(value))}</span>`; }
  function toast(message) { toastBox.textContent = message; toastBox.classList.remove("hidden"); setTimeout(() => toastBox.classList.add("hidden"), 2600); }
  function loading() { content.innerHTML = '<div class="empty">正在加载…</div>'; }
  function errorState(error) { content.innerHTML = `<section class="card card-pad"><div class="notice error">${esc(error.message)}</div></section>`; }
  async function api(path, options = {}) { const response = await fetch(path, { credentials: "same-origin", ...options }); const body = await response.json().catch(() => ({})); if (response.status === 401) { location.replace("/login"); throw new Error("登录已失效"); } if (!response.ok) throw new Error(body.detail || "请求失败"); return body; }
  function detailItem(name, value, raw = false) { return `<div class="detail-item"><span>${esc(name)}</span>${raw ? value : `<strong>${esc(value == null ? "—" : value)}</strong>`}</div>`; }

  function caseTable(items) {
    if (!items.length) return '<div class="empty">当前没有分配给您的服务案件</div>';
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>理赔事件</th><th>客户</th><th>反馈</th><th>待跟进</th><th>待分流</th><th>更新时间</th></tr></thead><tbody>${items.map(item => `<tr><td><strong>${esc(item.claim_event_id)}</strong></td><td>${esc(item.customer_display_name || "未关联")}</td><td>${item.feedback_count}</td><td>${item.active_follow_up_count}</td><td>${item.pending_consultation_count}</td><td>${esc(formatTime(item.updated_at))}</td></tr>`).join("")}</tbody></table></div>`;
  }
  async function home() {
    topTitle.textContent = "客服工作台";
    const data = await api("/api/service/dashboard");
    content.innerHTML = `<div class="page-head"><div><h1>${esc(currentUser.display_name)}，开始今天的服务</h1><p class="muted">咨询信息、分流动作和反馈跟进均写入正式业务表</p></div><button id="newConsult" class="btn primary">登记咨询</button></div>
      <section class="metric-grid"><article class="card metric"><span class="muted">服务案件</span><strong>${data.total_cases}</strong></article><article class="card metric"><span class="muted">待分流咨询</span><strong>${data.pending_consultations}</strong></article><article class="card metric"><span class="muted">进行中跟进</span><strong>${data.active_follow_ups}</strong></article><article class="card metric"><span class="muted">紧急跟进</span><strong>${data.urgent_follow_ups}</strong></article></section>
      <section class="card"><div class="card-pad section-title"><h2>我的服务案件</h2></div>${caseTable(data.recent_cases)}</section>`;
    document.getElementById("newConsult").onclick = () => navigate("consult");
  }

  async function consult() {
    topTitle.textContent = "咨询登记";
    const cases = await api("/api/service/cases");
    content.innerHTML = `<div class="page-head"><div><h1>登记客户咨询</h1><p class="muted">只能关联已分配给您的案件；正文必须先去除姓名、证件号、手机号等直接身份信息</p></div></div><section class="card card-pad form-card"><form id="consultForm" class="profile-form">
      <div class="form-grid"><div class="form-field"><label for="consultCase">关联案件</label><select id="consultCase" class="input" required><option value="">请选择案件</option>${cases.map(item => `<option value="${esc(item.case_id)}">${esc(item.claim_event_id)} · ${esc(item.customer_display_name || "未关联客户")}</option>`).join("")}</select></div><div class="form-field"><label for="channel">咨询渠道</label><select id="channel" class="input"><option value="INBOUND_CALL">呼入电话</option><option value="WECHAT">微信</option><option value="ONLINE">在线咨询</option><option value="APP_CHAT">App 会话</option></select></div>
      <div class="form-field"><label for="category">诉求类型</label><select id="category" class="input"><option value="claim_progress">理赔进度</option><option value="claim_material">理赔材料</option><option value="coverage_rule">保障规则</option><option value="cost_question">费用疑问</option><option value="recovery_question">康复疑问</option><option value="medication_question">用药疑问</option><option value="recommendation_question">推荐疑问</option><option value="value_added_service">增值服务</option><option value="human_service_request">人工服务请求</option><option value="other">其他</option></select></div><div class="form-field"><label for="priority">紧急程度</label><select id="priority" class="input"><option value="medium">中</option><option value="low">低</option><option value="high">高</option><option value="urgent">紧急</option></select></div></div>
      <div class="form-field"><label for="consultText">脱敏咨询摘要</label><textarea id="consultText" class="input textarea tall" required maxlength="8000" placeholder="记录客户诉求、已答复事项和下一步动作；不要粘贴未经脱敏的原始聊天记录"></textarea></div>
      <label class="checkbox"><input id="consentConfirmed" type="checkbox"><span>已确认客户授权记录本次咨询</span></label><label class="checkbox"><input id="deidentifiedConfirmed" type="checkbox"><span>已确认咨询摘要完成脱敏</span></label><p id="consultError" class="form-error"></p><button id="consultSubmit" class="btn primary" ${cases.length ? "" : "disabled"}>保存并进入分流队列</button></form></section>`;
    document.getElementById("consultForm").onsubmit = async event => { event.preventDefault(); const button = document.getElementById("consultSubmit"); const error = document.getElementById("consultError"); error.textContent = ""; button.disabled = true; try { await api("/api/service/consultations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ case_id: document.getElementById("consultCase").value, channel: document.getElementById("channel").value, category: document.getElementById("category").value, priority: document.getElementById("priority").value, deidentified_text: document.getElementById("consultText").value.trim(), consent_confirmed: document.getElementById("consentConfirmed").checked, deidentification_confirmed: document.getElementById("deidentifiedConfirmed").checked }) }); toast("咨询已登记，等待确认分流"); navigate("routing"); } catch (exception) { error.textContent = exception.message; button.disabled = false; } };
  }

  async function routing() {
    topTitle.textContent = "分流队列";
    const items = await api("/api/service/consultations");
    content.innerHTML = `<div class="page-head"><div><h1>咨询分流</h1><p class="muted">核对诉求、优先级和建议动作后确认分流</p></div><button id="newConsult" class="btn primary">登记咨询</button></div><div class="queue-list">${items.length ? items.map(item => `<article class="card card-pad queue-card"><div class="section-title"><div><strong>${esc(item.claim_event_id)}</strong><p class="muted">${esc(item.customer_display_name || "未关联客户")} · ${esc(label(item.channel))} · ${esc(formatTime(item.session_time))}</p></div><div>${status(item.severity)} ${status(item.review_status)}</div></div><div class="queue-body"><div><span class="muted">诉求类型</span><p><strong>${esc(label(item.primary_intent))}</strong></p></div><div><span class="muted">脱敏咨询摘要</span><p>${esc(item.deidentified_text)}</p></div><div><span class="muted">建议动作</span><p>${esc(item.recommended_action)}</p></div></div>${item.review_status === "PENDING" ? `<button class="btn primary route-consult" data-id="${esc(item.analysis_id)}">确认分流</button>` : ""}</article>`).join("") : '<section class="card"><div class="empty">当前没有咨询记录</div></section>'}</div>`;
    document.getElementById("newConsult").onclick = () => navigate("consult");
    document.querySelectorAll(".route-consult").forEach(button => { button.onclick = async () => { button.disabled = true; try { await api(`/api/service/consultations/${encodeURIComponent(button.dataset.id)}/route`, { method: "POST" }); toast("咨询已完成分流"); await routing(); } catch (error) { toast(error.message); button.disabled = false; } }; });
  }

  async function followups() {
    topTitle.textContent = "跟进任务";
    const statusFilter = document.getElementById("followupStatus")?.value || "";
    const items = await api("/api/service/follow-ups" + (statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : ""));
    content.innerHTML = `<div class="page-head"><div><h1>反馈跟进</h1><p class="muted">客户低评分、未解决问题和人工服务请求会进入这里</p></div><select id="followupStatus" class="input compact"><option value="">全部状态</option><option value="OPEN" ${statusFilter === "OPEN" ? "selected" : ""}>待领取</option><option value="PROCESSING" ${statusFilter === "PROCESSING" ? "selected" : ""}>跟进中</option><option value="RESOLVED" ${statusFilter === "RESOLVED" ? "selected" : ""}>已解决</option></select></div><div class="queue-list">${items.length ? items.map(item => `<article class="card card-pad queue-card"><div class="section-title"><div><strong>${esc(item.claim_event_id)}</strong><p class="muted">${esc(formatTime(item.created_at))} · ${esc(label(item.category))}${item.feedback_rating ? ` · ${item.feedback_rating} 分` : ""}</p></div><div>${status(item.priority)} ${status(item.status)}</div></div><p>${esc(item.feedback_comment || item.note || "客户反馈需要进一步核实")}</p><p class="muted">${item.request_contact ? "客户已同意回访 · " : ""}负责人：${esc(item.assignee || "未领取")}${item.resolution ? ` · 处理结果：${esc(item.resolution)}` : ""}</p><div class="card-actions">${item.status === "OPEN" ? `<button class="btn primary claim-task" data-id="${esc(item.task_id)}">领取任务</button>` : ""}${item.status === "PROCESSING" && item.assignee === currentUser.display_name ? `<button class="btn primary resolve-task" data-id="${esc(item.task_id)}">填写结果并完成</button>` : ""}</div></article>`).join("") : '<section class="card"><div class="empty">当前筛选条件下没有跟进任务</div></section>'}</div>`;
    document.getElementById("followupStatus").onchange = followups;
    document.querySelectorAll(".claim-task").forEach(button => { button.onclick = async () => { button.disabled = true; try { await api(`/api/service/follow-ups/${encodeURIComponent(button.dataset.id)}/claim`, { method: "POST" }); toast("任务已领取"); await followups(); } catch (error) { toast(error.message); button.disabled = false; } }; });
    document.querySelectorAll(".resolve-task").forEach(button => { button.onclick = async () => { const resolution = window.prompt("请填写本次跟进的处理结果"); if (!resolution || !resolution.trim()) return; button.disabled = true; try { await api(`/api/service/follow-ups/${encodeURIComponent(button.dataset.id)}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resolution: resolution.trim() }) }); toast("跟进任务已完成"); await followups(); } catch (error) { toast(error.message); button.disabled = false; } }; });
  }

  async function profile() {
    topTitle.textContent = "个人信息";
    content.innerHTML = `<div class="page-head"><div><h1>个人信息</h1><p class="muted">账号、角色和权限不可自行修改</p></div></div><section class="card card-pad profile-card"><form id="profileForm" class="profile-form"><div class="detail-grid profile-readonly">${detailItem("账号 / 工号", currentUser.username)}${detailItem("角色", currentUser.role_name)}${detailItem("状态", status(currentUser.status), true)}</div><div class="form-field"><label for="displayName">显示名称</label><input class="input" id="displayName" required maxlength="100" value="${esc(currentUser.display_name || "")}"></div><div class="form-field"><label for="phone">手机号</label><input class="input" id="phone" maxlength="50" value="${esc(currentUser.phone || "")}"></div><div class="form-field"><label for="email">邮箱</label><input class="input" id="email" type="email" maxlength="254" value="${esc(currentUser.email || "")}"></div><p id="profileError" class="form-error"></p><button class="btn primary">保存资料</button></form></section>`;
    document.getElementById("profileForm").onsubmit = async event => { event.preventDefault(); const error = document.getElementById("profileError"); error.textContent = ""; try { currentUser = await api("/api/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: document.getElementById("displayName").value, phone: document.getElementById("phone").value, email: document.getElementById("email").value }) }); document.getElementById("userName").textContent = currentUser.display_name; toast("个人资料已更新"); } catch (exception) { error.textContent = exception.message; } };
  }
  function navigate(view) { activeView = view; document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view)); render(); }
  async function render() { loading(); try { await ({ home, consult, routing, followups, profile })[activeView](); } catch (error) { errorState(error); } }
  document.querySelectorAll(".nav-item").forEach(item => { item.onclick = () => navigate(item.dataset.view); });
  document.getElementById("logoutButton").onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } };
  (async () => { try { currentUser = await api("/api/auth/me"); if (currentUser.role_code !== "SERVICE") throw new Error("当前账号无权进入客服工作区"); document.getElementById("userName").textContent = currentUser.display_name; await render(); } catch (error) { if (error.message.includes("无权进入")) { alert(error.message); location.replace("/login"); } } })();
})();
