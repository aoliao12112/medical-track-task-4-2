(function () {
  const content = document.getElementById("content");
  const topTitle = document.getElementById("topTitle");
  const materialFailures = new Map();
  let currentUser = null;
  let activeView = "home";
  let activeCaseId = null;
  let activeTab = "overview";
  let profileMode = "view";

  const labels = {
    ACTIVE: "启用", authorized: "已授权", pending: "待确认", unknown: "待确认",
    confirmed: "已确认脱敏", user_upload: "用户上传", customer_self_service: "客户自助申请",
    phone_report: "电话报案", offline_material: "线下材料", historical_migration: "历史数据迁移",
    customer_service_entry: "客服代录", PENDING_ACCEPTANCE: "待理赔受理", ACCEPTED: "理赔已受理",
    RECEIVED: "尚未进入医护关怀", STRUCTURED: "等待移交医护关怀",
    CARE_TRIGGERED: "已移交医护关怀", PROCESSING: "处理中",
    PAIN_READY: "医疗处理中", CARE_READY: "等待医疗复核",
    DELIVERY_READY: "等待发送", REVIEW_REQUIRED: "等待医疗复核",
    APPROVED: "医疗复核已通过", REJECTED: "医疗复核未通过",
    BLOCKED: "已阻止", DELIVERED_MOCK: "已完成模拟触达",
    FEEDBACK_RECEIVED: "已收到客户反馈", FOLLOWUP_PENDING: "后续服务处理中",
    RESOLVED: "已完成", COMPLETED: "理赔已完成",
    not_submitted: "未提交", submitted: "已提交", processing: "处理中",
    completed: "处理完成", failed: "处理失败"
  };

  function esc(value) {
    return String(value == null ? "—" : value).replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[char]);
  }
  function label(value) { return labels[value] || (typeof value === "string" ? value : "—"); }
  function status(value) {
    const warning = ["pending", "unknown", "PENDING_ACCEPTANCE", "REVIEW_REQUIRED", "FOLLOWUP_PENDING", "CARE_TRIGGERED", "processing", "submitted"].includes(value);
    const success = ["authorized", "confirmed", "ACCEPTED", "COMPLETED", "CARE_READY", "APPROVED", "RESOLVED", "completed"].includes(value);
    const danger = ["REJECTED", "BLOCKED", "failed"].includes(value);
    return `<span class="tag ${danger ? "danger" : warning ? "warning" : success ? "success" : ""}">${esc(label(value))}</span>`;
  }
  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
  }
  function toast(message) {
    const node = document.getElementById("toast");
    node.textContent = message;
    node.classList.remove("hidden");
    setTimeout(() => node.classList.add("hidden"), 3000);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    if (response.status === 401) {
      location.replace("/login");
      throw new Error("登录已失效");
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(body.detail) ? body.detail.map((item) => item.msg || "参数不正确").join("；") : body.detail;
      throw new Error(detail || "请求失败");
    }
    return body;
  }
  function loading() { content.innerHTML = '<div class="card empty">正在读取真实业务数据…</div>'; }
  function errorState(error) {
    content.innerHTML = `<div class="card empty"><p>页面加载失败</p><p class="muted">${esc(error.message)}</p><button class="btn secondary" id="retry">重新加载</button></div>`;
    document.getElementById("retry").onclick = render;
  }
  function materialState(item) { return materialFailures.has(item.case_id) ? "failed" : item.material_status; }
  function caseRows(items, showOwner = false) {
    if (!items.length) return '<div class="empty">暂无符合条件的案件</div>';
    return `<div class="table-wrap"><table class="data-table"><thead><tr>
      <th>案件编号</th><th>理赔事件编号</th><th>客户</th><th>材料状态</th><th>授权状态</th>
      <th>理赔状态</th><th>医护关怀状态</th>${showOwner ? "<th>理赔负责人</th>" : ""}<th>更新时间</th><th>操作</th>
      </tr></thead><tbody>${items.map((item) => `<tr>
      <td>${esc(item.case_id)}</td><td>${esc(item.claim_event_id)}</td><td>${esc(item.customer_display_name || "未关联客户")}</td>
      <td>${status(materialState(item))}</td><td>${status(item.consent_status)}</td><td>${status(item.claim_status)}</td>
      <td>${status(item.care_status)}</td>${showOwner ? `<td>${esc(item.claim_owner_name || "未分配")}</td>` : ""}
      <td>${esc(formatTime(item.updated_at))}</td><td><button class="link-btn open-case" data-id="${esc(item.case_id)}">查看详情</button></td>
      </tr>`).join("")}</tbody></table></div>`;
  }
  function bindCaseLinks() {
    document.querySelectorAll(".open-case").forEach((button) => { button.onclick = () => openCase(button.dataset.id); });
  }

  async function home() {
    topTitle.textContent = "理赔工作台";
    const data = await api("/api/claim/dashboard");
    content.innerHTML = `<div class="page-head"><div><h1>首页</h1><p class="muted">案件入口与流程状态概览</p></div><button class="btn primary" id="newCase">新建理赔案件</button></div>
      <section class="metric-grid">
        <article class="card metric"><span class="muted">我的待处理案件</span><strong>${data.pending}</strong></article>
        <article class="card metric"><span class="muted">材料未就绪</span><strong>${data.awaiting_material}</strong></article>
        <article class="card metric"><span class="muted">授权 / 脱敏异常</span><strong>${data.awaiting_authorization}</strong></article>
        <article class="card metric"><span class="muted">可移交医护关怀</span><strong>${data.ready_for_care}</strong></article>
        <article class="card metric"><span class="muted">已进入医护关怀</span><strong>${data.care_started}</strong></article>
      </section><section class="card"><div class="card-pad section-title"><h2>最近案件</h2><button class="link-btn" id="allCases">查看全部</button></div>${caseRows(data.recent_cases)}</section>`;
    document.getElementById("newCase").onclick = openCreateDialog;
    document.getElementById("allCases").onclick = () => navigate("cases");
    bindCaseLinks();
  }

  async function cases() {
    topTitle.textContent = "我的案件";
    content.innerHTML = `<div class="page-head"><div><h1>我的案件</h1><p class="muted">仅展示您作为参与人员有权访问的案件</p></div><button class="btn primary" id="newCase">新建理赔案件</button></div>
      <section class="card card-pad"><form id="filters" class="toolbar">
      <input id="search" class="input grow" placeholder="搜索案件编号或理赔事件编号">
      <select id="claimStatus" class="input"><option value="">全部理赔状态</option><option value="PENDING_ACCEPTANCE">待理赔受理</option><option value="ACCEPTED">理赔已受理</option><option value="COMPLETED">理赔已完成</option></select>
      <select id="consentStatus" class="input"><option value="">全部授权状态</option><option value="authorized">已授权</option><option value="pending">待确认</option></select>
      <select id="careStatus" class="input"><option value="">全部关怀状态</option><option value="RECEIVED">尚未进入</option><option value="STRUCTURED">等待移交</option><option value="CARE_TRIGGERED">已移交</option><option value="CARE_READY">等待医疗复核</option></select>
      <button class="btn secondary">查询</button></form><div id="caseTable" class="empty">正在读取案件…</div></section>`;
    document.getElementById("newCase").onclick = openCreateDialog;
    async function load() {
      const params = new URLSearchParams();
      [["search", "search"], ["claim_status", "claimStatus"], ["consent_status", "consentStatus"], ["care_status", "careStatus"]].forEach(([key, id]) => {
        const value = document.getElementById(id).value.trim(); if (value) params.set(key, value);
      });
      const items = await api("/api/claim/cases?" + params);
      document.getElementById("caseTable").innerHTML = caseRows(items, true);
      bindCaseLinks();
    }
    document.getElementById("filters").onsubmit = (event) => { event.preventDefault(); load().catch(errorState); };
    await load();
  }

  async function tasks() {
    topTitle.textContent = "我的待办";
    const items = await api("/api/claim/tasks");
    content.innerHTML = `<div class="page-head"><div><h1>我的待办</h1><p class="muted">仅包含材料、授权、脱敏和关怀移交等理赔职责事项</p></div></div>
      <section class="card card-pad"><div class="task-list">${items.length ? items.map((item) => `<article class="task"><div><strong>${esc(item.claim_event_id)}</strong><p class="muted">${item.reasons.map(esc).join(" · ")}</p></div><button class="btn secondary open-case" data-id="${esc(item.case_id)}">处理案件</button></article>`).join("") : '<div class="empty">当前没有理赔待处理事项</div>'}</div></section>`;
    bindCaseLinks();
  }

  async function profile() {
    topTitle.textContent = "个人信息";
    if (profileMode === "edit") { renderProfileEdit(); return; }
    if (profileMode === "password") { renderPasswordEdit(); return; }
    content.innerHTML = `<div class="page-head"><div><h1>个人信息</h1><p class="muted">账号资料来自当前登录身份</p></div></div><section class="card card-pad"><div class="detail-grid">
      ${detailItem("显示名称", currentUser.display_name)}${detailItem("账号 / 工号", currentUser.username)}${detailItem("角色", currentUser.role_name)}
      ${detailItem("账号状态", status(currentUser.status), true)}${detailItem("手机号", currentUser.phone || "未填写")}${detailItem("邮箱", currentUser.email || "未填写")}
      </div><div class="profile-actions"><button class="btn primary" id="editProfile">编辑资料</button><button class="btn secondary" id="changePassword">修改密码</button></div></section>`;
    document.getElementById("editProfile").onclick = () => { profileMode = "edit"; profile(); };
    document.getElementById("changePassword").onclick = () => { profileMode = "password"; profile(); };
  }

  function renderProfileEdit() {
    content.innerHTML = `<div class="page-head"><div><h1>编辑个人资料</h1><p class="muted">账号、角色和账号状态不可自行修改</p></div></div><section class="card card-pad profile-card">
      <form id="profileForm" class="profile-form">
        <div class="detail-grid profile-readonly">${detailItem("账号 / 工号", currentUser.username)}${detailItem("角色", currentUser.role_name)}${detailItem("账号状态", status(currentUser.status), true)}</div>
        <div class="form-field"><label for="profileDisplayName">显示名称</label><input class="input" id="profileDisplayName" maxlength="100" required value="${esc(currentUser.display_name || "")}"></div>
        <div class="form-field"><label for="profilePhone">手机号</label><input class="input" id="profilePhone" maxlength="50" value="${esc(currentUser.phone || "")}" placeholder="可留空，支持国际号码常用格式"></div>
        <div class="form-field"><label for="profileEmail">邮箱</label><input class="input" id="profileEmail" maxlength="254" type="email" value="${esc(currentUser.email || "")}" placeholder="可留空"></div>
        <p id="profileError" class="form-error"></p><div class="profile-actions"><button class="btn primary" id="saveProfile" type="submit">保存</button><button class="btn secondary" id="cancelProfile" type="button">取消</button></div>
      </form></section>`;
    document.getElementById("cancelProfile").onclick = () => { profileMode = "view"; profile(); };
    document.getElementById("profileForm").onsubmit = async (event) => {
      event.preventDefault();
      const button = document.getElementById("saveProfile");
      const error = document.getElementById("profileError");
      button.disabled = true; button.textContent = "保存中…"; error.textContent = "";
      try {
        currentUser = await api("/api/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          display_name: document.getElementById("profileDisplayName").value,
          phone: document.getElementById("profilePhone").value,
          email: document.getElementById("profileEmail").value
        }) });
        document.getElementById("userName").textContent = currentUser.display_name;
        profileMode = "view"; toast("个人资料已更新"); await profile();
      } catch (exception) { error.textContent = exception.message; button.disabled = false; button.textContent = "保存"; }
    };
  }

  function renderPasswordEdit() {
    content.innerHTML = `<div class="page-head"><div><h1>修改密码</h1><p class="muted">修改后当前登录状态保持有效</p></div></div><section class="card card-pad profile-card"><form id="passwordForm" class="profile-form">
      <div class="form-field"><label for="currentPassword">当前密码</label><input class="input" id="currentPassword" type="password" autocomplete="current-password" required></div>
      <div class="form-field"><label for="newPassword">新密码</label><input class="input" id="newPassword" type="password" minlength="8" autocomplete="new-password" required><small class="muted">至少 8 位，且不能与当前密码相同</small></div>
      <div class="form-field"><label for="confirmNewPassword">确认新密码</label><input class="input" id="confirmNewPassword" type="password" minlength="8" autocomplete="new-password" required></div>
      <p id="passwordError" class="form-error"></p><div class="profile-actions"><button class="btn primary" id="savePassword" type="submit">保存新密码</button><button class="btn secondary" id="cancelPassword" type="button">取消</button></div>
      </form></section>`;
    document.getElementById("cancelPassword").onclick = () => { profileMode = "view"; profile(); };
    document.getElementById("passwordForm").onsubmit = async (event) => {
      event.preventDefault();
      const button = document.getElementById("savePassword");
      const error = document.getElementById("passwordError");
      button.disabled = true; button.textContent = "修改中…"; error.textContent = "";
      try {
        const result = await api("/api/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          current_password: document.getElementById("currentPassword").value,
          new_password: document.getElementById("newPassword").value,
          confirm_new_password: document.getElementById("confirmNewPassword").value
        }) });
        profileMode = "view"; toast(result.message || "密码已修改"); await profile();
      } catch (exception) { error.textContent = exception.message; button.disabled = false; button.textContent = "保存新密码"; }
    };
  }

  async function openCase(caseId, tab = "overview") {
    activeCaseId = caseId; activeTab = tab;
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    loading();
    try { renderCase(await api("/api/claim/cases/" + encodeURIComponent(caseId))); }
    catch (error) { errorState(error); }
  }
  function renderCase(item) {
    topTitle.textContent = "案件详情";
    const tabs = [["overview", "案件概览"], ["materials", "材料状态"], ["consent", "授权与合规"], ["claim-status", "理赔状态"], ["care", "医护关怀"]];
    content.innerHTML = `<section class="card case-header"><div class="case-header-main"><div><button class="link-btn" id="backCases">← 返回我的案件</button><h1>${esc(item.case_id)}</h1>
      <div class="case-meta"><span>客户：${esc(item.customer_display_name || "未关联客户")}</span>${status(materialState(item))}${status(item.claim_status)}${status(item.care_status)}</div></div>
      <div><span class="muted">理赔负责人</span><p><strong>${esc(item.claim_owner_name || "未分配")}</strong></p></div></div></section>
      <section class="card"><div class="tabs">${tabs.map(([id, name]) => `<button class="tab ${activeTab === id ? "active" : ""}" data-tab="${id}">${name}</button>`).join("")}</div><div id="tabPanel" class="tab-panel"></div></section>`;
    document.getElementById("backCases").onclick = () => navigate("cases");
    document.querySelectorAll(".tab").forEach((button) => { button.onclick = () => { activeTab = button.dataset.tab; renderCase(item); }; });
    renderTab(item);
  }
  function detailItem(name, value, raw = false) {
    return `<div class="detail-item"><span>${name}</span>${raw ? value : `<strong>${esc(value == null ? "—" : value)}</strong>`}</div>`;
  }
  function materialMessage(state) {
    return ({
      not_submitted: "尚未提交可处理的医疗材料。",
      submitted: "材料已接收，但医护分析准备尚未完成，可重新提交或补充材料。",
      processing: "材料正在处理中，请稍后刷新查看状态。",
      completed: "材料处理已完成，医护分析准备已就绪。",
      failed: materialFailures.get(activeCaseId) || "材料处理失败，请重新上传或补充材料。"
    })[state] || "材料状态待确认。";
  }
  function careBlockers(data) {
    const blockers = [];
    if (data.claim_status === "PENDING_ACCEPTANCE") blockers.push("理赔案件尚未受理");
    if (data.consent_status !== "authorized") blockers.push("用户授权尚未确认");
    if (data.deidentification_status !== "confirmed") blockers.push("材料脱敏状态尚未通过");
    if (!data.analysis_ready) blockers.push("材料处理尚未完成");
    return blockers;
  }
  function renderTab(data) {
    const panel = document.getElementById("tabPanel");
    if (activeTab === "overview") {
      panel.innerHTML = `<div class="detail-grid">${detailItem("案件编号", data.case_id)}${detailItem("理赔事件编号", data.claim_event_id)}
        ${detailItem("客户脱敏信息", data.customer_display_name || "未关联客户")}${detailItem("创建时间", formatTime(data.created_at))}
        ${detailItem("数据来源", label(data.data_source))}${detailItem("理赔负责人", data.claim_owner_name || "未分配")}
        ${detailItem("授权状态", status(data.consent_status), true)}${detailItem("脱敏状态", status(data.deidentification_status), true)}
        ${detailItem("材料状态", status(materialState(data)), true)}${detailItem("理赔状态", status(data.claim_status), true)}
        ${detailItem("医护关怀状态", status(data.care_status), true)}${detailItem("最近更新时间", formatTime(data.updated_at))}</div>
        <div class="reviewer-assign"><div><strong>${data.customer_username ? "已绑定客户" : "补录绑定客户"}</strong><p class="muted">${data.customer_username ? `当前客户账号：${esc(data.customer_username)}。结果送达后不可更换。` : "输入已有客户账号进行绑定；不会创建新的客户身份。"}</p></div><div class="inline-form"><input id="bindCustomerUsername" class="input" maxlength="200" placeholder="客户账号" value="${esc(data.customer_username || "")}"><button id="bindCustomer" class="btn secondary">确认绑定</button></div></div>`;
      document.getElementById("bindCustomer").onclick = async () => {
        const username = document.getElementById("bindCustomerUsername").value.trim();
        if (!username) { toast("请输入客户账号"); return; }
        const button = document.getElementById("bindCustomer"); button.disabled = true;
        try {
          const result = await api(`/api/claim/cases/${encodeURIComponent(data.case_id)}/customer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username }) });
          toast(`${result.message}：${result.customer.display_name}`); await openCase(data.case_id, "overview");
        } catch (error) { toast(error.message); button.disabled = false; }
      };
    }
    if (activeTab === "materials") {
      const state = materialState(data);
      panel.innerHTML = `<div class="section-title"><div><h2>材料状态</h2><p class="muted">查看材料是否已提交、是否处理完成以及是否需要补充</p></div>${status(state)}</div>
        <div class="notice ${state === "failed" ? "error" : ""}">${esc(materialMessage(state))}</div>
        <div class="upload-area" style="margin-top:18px"><input id="documentFiles" type="file" multiple accept=".jpg,.jpeg,.png,.bmp,.webp">
        <p class="muted">仅上传已获授权并完成脱敏的医疗图片。系统不会在理赔页面展示医学明细。</p>
        <button id="processDocuments" class="btn primary">${state === "not_submitted" ? "上传材料" : "重新上传 / 补充材料"}</button></div>`;
      document.getElementById("processDocuments").onclick = processDocuments;
    }
    if (activeTab === "consent") {
      const blockers = careBlockers(data);
      panel.innerHTML = `<div class="detail-grid">${detailItem("数据来源", label(data.data_source))}${detailItem("用户授权状态", status(data.consent_status), true)}
        ${detailItem("脱敏状态", status(data.deidentification_status), true)}${detailItem("允许进入后续医护流程", blockers.length ? status("BLOCKED") : status("APPROVED"), true)}</div>
        <div class="notice ${blockers.length ? "error" : ""}" style="margin-top:20px">${blockers.length ? `当前无法进入医护关怀流程：${blockers.map(esc).join("；")}。请重新处理或补充材料。` : "授权和脱敏条件已满足；系统仅向后续流程提供必要的脱敏信息。"}</div>
        <div class="reviewer-assign"><div><strong>补录授权与脱敏确认</strong><p class="muted">必须依据客户真实授权和已完成的材料脱敏操作填写，不能为了通过流程虚假勾选。</p></div><div class="inline-form"><select id="consentStatusUpdate" class="input"><option value="pending" ${data.consent_status === "pending" ? "selected" : ""}>授权待确认</option><option value="authorized" ${data.consent_status === "authorized" ? "selected" : ""}>已获得授权</option></select><select id="deidentificationStatusUpdate" class="input"><option value="pending" ${data.deidentification_status === "pending" ? "selected" : ""}>脱敏待确认</option><option value="confirmed" ${data.deidentification_status === "confirmed" ? "selected" : ""}>已确认脱敏</option></select><button id="saveCompliance" class="btn secondary" ${data.claim_status === "PENDING_ACCEPTANCE" ? "disabled" : ""}>保存补录</button></div></div>`;
      document.getElementById("saveCompliance").onclick = async () => {
        const button = document.getElementById("saveCompliance"); button.disabled = true;
        try {
          const result = await api(`/api/claim/cases/${encodeURIComponent(data.case_id)}/compliance`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ consent_status: document.getElementById("consentStatusUpdate").value, deidentification_status: document.getElementById("deidentificationStatusUpdate").value }) });
          toast(result.message); await openCase(data.case_id, "consent");
        } catch (error) { toast(error.message); button.disabled = false; }
      };
    }
    if (activeTab === "claim-status") {
      panel.innerHTML = `<h2>理赔状态</h2><p class="muted">客户自助申请先进入待受理；理赔人员确认接案后方可继续补录与医护移交。</p>
        <div class="status-flow"><div class="status-node">当前状态：${status(data.claim_status)}</div></div>
        ${data.claim_status === "PENDING_ACCEPTANCE" ? '<div style="margin-top:18px"><button id="acceptClaim" class="btn primary">受理案件</button></div>' : ""}`;
      const acceptButton = document.getElementById("acceptClaim");
      if (acceptButton) acceptButton.onclick = async () => {
        acceptButton.disabled = true;
        try { const result = await api(`/api/claim/cases/${encodeURIComponent(data.case_id)}/accept`, { method: "POST" }); toast(result.message); await openCase(data.case_id, "claim-status"); }
        catch (error) { toast(error.message); acceptButton.disabled = false; }
      };
    }
    if (activeTab === "care") {
      const blockers = careBlockers(data);
      const handedOff = !["RECEIVED", "STRUCTURED"].includes(data.care_status);
      panel.innerHTML = `<div class="section-title"><div><h2>医护关怀</h2><p class="muted">将满足条件的理赔案件正式交给后续医疗工作流</p></div>${status(data.care_status)}</div>
        ${handedOff ? '<div class="notice"><strong>已移交医护关怀</strong><br>下一阶段：等待医疗处理 / 医疗复核。理赔人员无需操作医学内容。</div>' : blockers.length ? `<div class="notice error"><strong>暂不能移交</strong><br>${blockers.map(esc).join("；")}</div>` : '<div class="notice"><strong>可以移交</strong><br>材料、授权和脱敏条件均已满足。</div>'}
        ${!handedOff ? `<div style="margin-top:18px"><button id="triggerCare" class="btn primary" ${blockers.length ? "disabled" : ""}>移交医护关怀</button></div>` : ""}
        <div class="reviewer-assign"><div><strong>分配医疗复核人员</strong><p class="muted">使用医疗复核账号；分配后该人员才能在医疗工作区看到本案。</p></div><div class="inline-form"><input id="medicalReviewerUsername" class="input" maxlength="200" placeholder="例如 medical_demo"><button id="assignMedicalReviewer" class="btn secondary">确认分配</button></div></div>`;
      const button = document.getElementById("triggerCare");
      if (button) button.onclick = async () => {
        button.disabled = true;
        try { const result = await api(`/api/claim/cases/${encodeURIComponent(data.case_id)}/care/trigger`, { method: "POST" }); toast(result.message); await openCase(data.case_id, "care"); }
        catch (error) { toast(error.message); button.disabled = false; }
      };
      document.getElementById("assignMedicalReviewer").onclick = async () => {
        const username = document.getElementById("medicalReviewerUsername").value.trim();
        if (!username) { toast("请输入医疗复核账号"); return; }
        const assignButton = document.getElementById("assignMedicalReviewer"); assignButton.disabled = true;
        try { const result = await api(`/api/claim/cases/${encodeURIComponent(data.case_id)}/medical-reviewer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username }) }); toast(`${result.message}：${result.reviewer.display_name}`); }
        catch (error) { toast(error.message); assignButton.disabled = false; }
      };
    }
  }

  async function processDocuments() {
    const input = document.getElementById("documentFiles");
    const button = document.getElementById("processDocuments");
    if (!input.files.length) { toast("请先选择医疗图片"); return; }
    const form = new FormData(); Array.from(input.files).forEach((file) => form.append("files", file));
    button.disabled = true; button.textContent = "材料处理中…";
    try {
      const result = await api(`/api/claim/cases/${encodeURIComponent(activeCaseId)}/documents/process`, { method: "POST", body: form });
      materialFailures.delete(activeCaseId); toast(result.message); await openCase(activeCaseId, "materials");
    } catch (error) {
      materialFailures.set(activeCaseId, error.message); toast(error.message);
      const data = await api(`/api/claim/cases/${encodeURIComponent(activeCaseId)}`);
      activeTab = "materials"; renderCase(data);
    }
  }
  function openCreateDialog() {
    document.getElementById("caseError").textContent = "";
    document.getElementById("customerUsername").innerHTML = '<option value="">请先搜索客户</option>';
    document.getElementById("caseDialog").classList.remove("hidden");
  }
  function navigate(view) {
    activeView = view; activeCaseId = null;
    profileMode = "view";
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    render();
  }
  async function render() { loading(); try { await ({ home, cases, tasks, profile })[activeView](); } catch (error) { errorState(error); } }

  document.querySelectorAll(".nav-item").forEach((item) => { item.onclick = () => navigate(item.dataset.view); });
  document.getElementById("cancelCase").onclick = () => document.getElementById("caseDialog").classList.add("hidden");
  document.getElementById("searchCustomer").onclick = async () => {
    const query = document.getElementById("customerSearch").value.trim();
    const select = document.getElementById("customerUsername");
    const error = document.getElementById("caseError");
    if (!query) { error.textContent = "请输入客户账号、姓名或手机号"; return; }
    error.textContent = "";
    try {
      const customers = await api(`/api/claim/customers?search=${encodeURIComponent(query)}`);
      select.innerHTML = customers.length ? '<option value="">请选择匹配客户</option>' + customers.map(item => `<option value="${esc(item.username)}">${esc(item.username)} · ${esc(item.display_name)}${item.phone_last4 ? ` · 尾号 ${esc(item.phone_last4)}` : ""}</option>`).join("") : '<option value="">未找到可用客户</option>';
    } catch (exception) { error.textContent = exception.message; }
  };
  document.getElementById("caseForm").onsubmit = async (event) => {
    event.preventDefault(); const error = document.getElementById("caseError");
    const customerUsername = document.getElementById("customerUsername").value;
    if (!customerUsername) { error.textContent = "请先搜索并选择已有客户"; return; }
    try {
      const created = await api("/api/claim/cases", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        claim_event_id: document.getElementById("claimEventId").value.trim(),
        customer_username: customerUsername, data_source: document.getElementById("caseDataSource").value,
        consent_status: document.getElementById("caseAuthorized").checked ? "authorized" : "pending",
        deidentification_status: document.getElementById("caseDeidentified").checked ? "confirmed" : "pending"
      }) });
      document.getElementById("caseDialog").classList.add("hidden"); document.getElementById("caseForm").reset(); toast("已代客户建案并完成绑定"); await openCase(created.case_id);
    } catch (exception) { error.textContent = exception.message; }
  };
  document.getElementById("logoutButton").onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } };
  (async () => {
    try {
      currentUser = await api("/api/auth/me");
      if (currentUser.role_code !== "CLAIM") throw new Error("当前账号无权进入理赔工作区");
      document.getElementById("userName").textContent = currentUser.display_name; await render();
    } catch (error) { if (error.message === "当前账号无权进入理赔工作区") { alert(error.message); location.replace("/login"); } }
  })();
})();
