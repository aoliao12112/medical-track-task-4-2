(function () {
  "use strict";

  const role = document.body.dataset.role;
  const app = document.getElementById("roleApp");
  if (!app) return;

  const backend = (localStorage.getItem("medicalClaimsBackend") || "http://127.0.0.1:8000").replace(/\/$/, "");
  const savedCaseId = localStorage.getItem("medicalClaimsCaseId") || "";
  const configs = {
    claims_service: {
      eyebrow: "CLAIMS SERVICE", title: "理赔服务工作台", badge: "理赔客服",
      summary: "处理案件进度、材料状态和服务待办。这里只显示完成客服工作所需的摘要，不展示完整OCR原文和医学推断。",
      action: "打开综合案件台", actionUrl: "./index.html?view=delivery",
    },
    medical_reviewer: {
      eyebrow: "MEDICAL REVIEW", title: "医疗内容审核台", badge: "医学审核员",
      summary: "审核脱敏病例、痛点证据、温度指引和医学安全边界。收款账户、联系方式等非必要身份信息不进入本页面。",
      action: "打开温度指引", actionUrl: "./index.html?view=care",
    },
    operations: {
      eyebrow: "SERVICE OPERATIONS", title: "服务运营看板", badge: "运营 / 产品",
      summary: "查看聚合覆盖、推荐就绪情况和反馈待办。运营人员不进入单份病历原文，也不能批准医学内容。",
      action: "打开品质推荐", actionUrl: "./index.html?view=quality",
    },
    system_admin: {
      eyebrow: "ACCESS & AUDIT", title: "权限与审计中心", badge: "系统管理员",
      summary: "查看身份、角色、访问范围和审计配置。系统管理员默认不拥有脱敏病历浏览权。",
      action: "返回角色门户", actionUrl: "./roles.html",
    },
  };
  const config = configs[role];
  if (!config) return;

  const navigation = [
    ["claims_service", "客服端", "./claims-service.html"],
    ["medical_reviewer", "医学审核", "./medical-review.html"],
    ["operations", "运营看板", "./operations.html"],
    ["system_admin", "系统管理", "./system-admin.html"],
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, function (char) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char];
    });
  }

  app.innerHTML = `
    <header class="role-topbar">
      <a class="role-brand" href="./roles.html"><span>十</span><div><strong>医护洞察</strong><small>角色业务工作台</small></div></a>
      <nav class="role-nav" aria-label="角色页面">${navigation.map(function (entry) {
        return `<a class="${entry[0] === role ? "active" : ""}" href="${entry[2]}">${entry[1]}</a>`;
      }).join("")}<a href="./patient.html">用户端</a></nav>
      <span class="role-badge">${config.badge} · 原型角色</span>
    </header>
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <div><span class="eyebrow">${config.eyebrow}</span><h1>${config.title}</h1><p>${config.summary}</p></div>
        <div class="dashboard-toolbar">
          ${role !== "operations" && role !== "system_admin" ? '<input id="roleCaseId" aria-label="案件编号" placeholder="输入案件编号">' : ""}
          <button class="button primary" id="roleLoadButton">刷新当前视图</button>
          <a class="button secondary" href="${config.actionUrl}">${config.action}</a>
        </div>
      </section>
      <div class="connection" id="connectionState"><i></i><span>正在检测本地后端</span></div>
      <section class="kpi-grid" id="roleKpis"></section>
      <section class="dashboard-grid">
        <article class="panel"><div class="panel-head"><div><span class="eyebrow" id="primaryEyebrow">业务视图</span><h2 id="primaryTitle">正在载入</h2></div></div><div class="panel-body" id="primaryContent"><div class="empty">正在读取当前可用数据</div></div></article>
        <aside class="panel"><div class="panel-head"><div><span class="eyebrow" id="secondaryEyebrow">权限边界</span><h2 id="secondaryTitle">最小必要信息</h2></div></div><div class="panel-body" id="secondaryContent"></div></aside>
      </section>
      <section class="notice"><strong>原型说明</strong><span>页面已按角色拆分，但后端API鉴权与自动审计仍需继续接入。任何前端隐藏都不能替代后端权限判断。</span></section>
      <footer class="role-page-footer">医护洞察 · 理赔后医护关怀比赛原型</footer>
    </main>`;

  const caseInput = document.getElementById("roleCaseId");
  if (caseInput) caseInput.value = savedCaseId;
  document.getElementById("roleLoadButton").addEventListener("click", load);
  if (caseInput) caseInput.addEventListener("keydown", function (event) { if (event.key === "Enter") load(); });

  async function api(path) {
    const response = await fetch(backend + path, { headers: { "Accept": "application/json" } });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
    if (!response.ok) throw new Error(data && data.detail ? data.detail : `HTTP ${response.status}`);
    return data;
  }

  function setConnection(ok, text) {
    const el = document.getElementById("connectionState");
    el.classList.toggle("connected", ok);
    el.querySelector("span").textContent = text;
  }

  function setKpis(items) {
    document.getElementById("roleKpis").innerHTML = items.map(function (item) {
      return `<article class="kpi"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong><small>${escapeHtml(item.note)}</small></article>`;
    }).join("");
  }

  function setPanel(which, eyebrow, title, html) {
    document.getElementById(which + "Eyebrow").textContent = eyebrow;
    document.getElementById(which + "Title").textContent = title;
    document.getElementById(which + "Content").innerHTML = html;
  }

  function listItem(title, detail, status, tone) {
    return `<div class="list-item"><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div><span class="status ${tone || ""}">${escapeHtml(status)}</span></div>`;
  }

  function empty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }

  async function health() {
    try {
      const data = await api("/api/health");
      setConnection(true, `本地后端已连接 · OCR ${data.ocr || "可用性待核验"} · LLM ${data.llm || "可用性待核验"}`);
      return true;
    } catch (error) {
      setConnection(false, `未连接后端：${error.message}`);
      return false;
    }
  }

  async function loadClaims() {
    const caseId = caseInput.value.trim();
    if (!caseId) {
      setKpis([["案件状态", "—", "请输入案件编号"], ["结构化", "—", "仅显示处理状态"], ["待办", "—", "等待读取"], ["授权", "—", "等待读取"]].map(k => ({label:k[0],value:k[1],note:k[2]})));
      setPanel("primary", "理赔案件", "尚未选择案件", empty("从综合工作台复制案件编号后再读取。"));
      setPanel("secondary", "可见范围", "客服信息边界", privacyClaims());
      return;
    }
    localStorage.setItem("medicalClaimsCaseId", caseId);
    const results = await Promise.allSettled([
      api(`/api/workflow/cases/${encodeURIComponent(caseId)}`),
      api(`/api/workflow/cases/${encodeURIComponent(caseId)}/follow-ups`),
    ]);
    if (results[0].status === "rejected") throw results[0].reason;
    const meta = results[0].value;
    const tasks = results[1].status === "fulfilled" ? results[1].value : [];
    const open = tasks.filter(t => ["OPEN", "PROCESSING"].includes(t.status)).length;
    setKpis([
      {label:"案件状态",value:meta.status,note:"来自案件工作流"},
      {label:"病历结构化",value:meta.has_structured_result ? "已完成" : "未完成",note:"不展示完整OCR原文"},
      {label:"待处理服务",value:String(open),note:"反馈或会话产生的待办"},
      {label:"授权/脱敏",value:meta.consent_status === "authorized" && meta.deidentification_status === "confirmed" ? "已确认" : "待确认",note:"未确认时不得调用外部模型"},
    ]);
    setPanel("primary", "案件摘要", `案件 ${caseId}`, `<div class="list">${[
      ["材料与结构化", meta.has_structured_result ? "结构化结果已保存，可继续查看服务进展。" : "尚未形成结构化结果，请回到案件台处理材料。", meta.has_structured_result ? "完成" : "等待", meta.has_structured_result ? "" : "warn"],
      ["痛点分析", meta.has_pain_result ? "已形成临床、费用与体验痛点报告。" : "尚未完成痛点分析。", meta.has_pain_result ? "完成" : "等待", meta.has_pain_result ? "" : "warn"],
      ["关怀指引", meta.has_care_result ? "已生成用户可读关怀内容，等待或已完成触达审核。" : "尚未生成温度指引。", meta.has_care_result ? "完成" : "等待", meta.has_care_result ? "" : "warn"],
    ].map(x => listItem(x[0],x[1],x[2],x[3])).join("")}</div>`);
    setPanel("secondary", "服务待办", `当前 ${tasks.length} 项记录`, tasks.length ? `<div class="list">${tasks.map(t => listItem(t.category || "服务问题", t.note || "等待处理", t.status, t.priority === "high" ? "danger" : "warn")).join("")}</div>` : empty("当前案件没有服务待办。") + privacyClaims());
  }

  function privacyClaims() {
    return '<div class="privacy-note"><strong>客服可以看</strong><br>案件进度、材料状态、服务待办和必要摘要。<br><br><strong>客服不可看</strong><br>完整OCR原文、银行卡信息、与客服处理无关的病历细节，也不能作出医学结论。</div>';
  }

  async function loadMedical() {
    const caseId = caseInput.value.trim();
    if (!caseId) {
      setKpis([{label:"脱敏病例",value:"—",note:"请输入案件编号"},{label:"痛点",value:"—",note:"等待读取"},{label:"安全检查",value:"—",note:"等待读取"},{label:"审核记录",value:"—",note:"等待读取"}]);
      setPanel("primary", "医学审核", "尚未选择案件", empty("输入案件编号后读取脱敏病例与温度指引。"));
      setPanel("secondary", "审核边界", "不替代经治医生", '<div class="privacy-note">医学审核员核对证据、表述和安全边界，不判断医疗事故，不允许凭单份材料指责医生，也不查看收款账户等非必要身份信息。</div>');
      return;
    }
    localStorage.setItem("medicalClaimsCaseId", caseId);
    const [meta, saved, reviews] = await Promise.all([
      api(`/api/workflow/cases/${encodeURIComponent(caseId)}`),
      api(`/api/workflow/cases/${encodeURIComponent(caseId)}/result`),
      api(`/api/workflow/cases/${encodeURIComponent(caseId)}/reviews`).catch(() => []),
    ]);
    const structured = saved.structured_result || {};
    const pain = saved.pain_result || null;
    const care = saved.care_result || null;
    const safety = care && Array.isArray(care.safety_checks) ? care.safety_checks : [];
    const blocked = safety.filter(x => x.status === "blocked").length;
    setKpis([
      {label:"诊断记录",value:String((structured.diagnoses || []).length),note:"仅脱敏结构化字段"},
      {label:"痛点",value:pain ? String(pain.summary.total) : "—",note:pain ? "含原文证据定位" : "尚未分析"},
      {label:"安全检查",value:care ? (blocked ? `${blocked}项阻断` : "无阻断") : "—",note:"医学边界与证据检查"},
      {label:"审核记录",value:String(reviews.length),note:`案件状态 ${meta.status}`},
    ]);
    const points = pain ? pain.pain_points : [];
    setPanel("primary", "痛点与证据", `案件 ${caseId}`, points.length ? `<div class="list">${points.map(p => listItem(p.title, p.description, p.severity, p.severity === "high" || p.severity === "critical" ? "danger" : "warn")).join("")}</div>` : empty("当前案件尚未保存痛点报告。"));
    const safetyHtml = safety.length ? safety.map(x => listItem(x.code, x.message, x.status, x.status === "blocked" ? "danger" : x.status === "review" ? "warn" : "")).join("") : empty("尚未生成温度指引或安全检查。 ");
    setPanel("secondary", "医学安全", "发布前检查", `<div class="list">${safetyHtml}</div><div class="privacy-note">审核动作仍需在综合工作台完成；BLOCKED内容不能通过人工批准绕过。</div>`);
  }

  async function loadOperations() {
    const results = await Promise.allSettled([api("/api/quality/coverage"), api("/api/quality/portfolio"), api("/api/follow-ups")]);
    const coverage = results[0].status === "fulfilled" ? results[0].value : null;
    const portfolio = results[1].status === "fulfilled" ? results[1].value : null;
    const tasks = results[2].status === "fulfilled" ? results[2].value : [];
    const diseases = coverage && Array.isArray(coverage.diseases) ? coverage.diseases : [];
    const open = tasks.filter(t => ["OPEN", "PROCESSING"].includes(t.status)).length;
    setKpis([
      {label:"覆盖病种",value:coverage ? `${coverage.disease_count || diseases.length}/3` : "—",note:"目标至少3个病种"},
      {label:"画像就绪",value:portfolio && portfolio.portfolio ? "已生成" : "未生成",note:"只展示聚合结果"},
      {label:"开放待办",value:String(open),note:"不展开病历原文"},
      {label:"推荐状态",value:coverage ? (coverage.status || "已评估") : "—",note:"样本不足不得推荐"},
    ]);
    setPanel("primary", "聚合覆盖", "病种与机构覆盖", diseases.length ? `<table class="data-table"><thead><tr><th>病种</th><th>机构</th><th>合格机构</th><th>有效维度</th></tr></thead><tbody>${diseases.map(d => `<tr><td>${escapeHtml(d.disease_group)}</td><td>${escapeHtml(d.institution_count)}</td><td>${escapeHtml(d.qualified_institution_count)}</td><td>${escapeHtml(d.available_dimension_count)}</td></tr>`).join("")}</tbody></table>` : empty("尚未生成覆盖审计；运营端不会用演示数字替代真实结果。"));
    setPanel("secondary", "运营边界", "只能查看聚合结果", `<div class="list">${listItem("触达与反馈", tasks.length ? `当前共有${tasks.length}条服务待办记录。` : "尚无服务待办数据。", open ? `${open}项开放` : "无开放项", open ? "warn" : "")}${listItem("病历访问", "运营角色不进入单份OCR原文、诊断证据或用户身份字段。", "禁止", "danger")}</div>`);
  }

  async function loadAdmin() {
    const roles = [
      ["用户/患者", "查看本人关怀、提交本人反馈", "否"],
      ["理赔客服", "案件摘要、材料状态、服务待办", "否"],
      ["医学审核员", "脱敏病历、痛点与指引审核", "是"],
      ["运营/产品", "聚合统计、触达和反馈趋势", "否"],
      ["系统管理员", "账号角色、访问范围、审计元数据", "否"],
    ];
    setKpis([
      {label:"数据库表",value:"19",note:"Schema版本5"},
      {label:"固定角色",value:"5",note:"后端固定权限矩阵"},
      {label:"本地账号",value:"未开放API",note:"数据库当前可为空"},
      {label:"审计接入",value:"待接API",note:"不能用前端隐藏替代"},
    ]);
    setPanel("primary", "角色矩阵", "最小权限配置", `<table class="data-table"><thead><tr><th>角色</th><th>必要权限</th><th>可查看脱敏病历</th></tr></thead><tbody>${roles.map(r => `<tr><td><strong>${r[0]}</strong></td><td>${r[1]}</td><td class="${r[2] === "是" ? "permission-yes" : "permission-no"}">${r[2]}</td></tr>`).join("")}</tbody></table>`);
    setPanel("secondary", "安全边界", "管理员也受限制", `<div class="list">${listItem("身份来源", "本地原型使用密码哈希和会话；正式上线应接企业统一登录。", "已接本地认证", "")}${listItem("案件范围", "角色决定能做什么，case_participants决定能看哪个案件。", "已接数据层", "")}${listItem("审计日志", "敏感操作统一写入system_logs，不保存完整医疗原文。", "持续完善", "warn")}</div>`);
  }

  async function load() {
    const connected = await health();
    try {
      if (role === "claims_service") await loadClaims();
      if (role === "medical_reviewer") await loadMedical();
      if (role === "operations") await loadOperations();
      if (role === "system_admin") await loadAdmin();
    } catch (error) {
      setPanel("primary", "读取失败", "当前视图无法载入", empty(error.message));
      if (!connected) setConnection(false, "请先启动本地后端，再刷新当前视图");
    }
  }

  load();
})();
