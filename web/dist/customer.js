(function () {
  const content = document.getElementById("content");
  const topTitle = document.getElementById("topTitle");
  const toastBox = document.getElementById("toast");
  let currentUser = null;
  let activeView = "home";

  const labels = {
    PENDING_ACCEPTANCE: "待理赔受理", ACCEPTED: "理赔已受理", COMPLETED: "理赔已完成",
    RECEIVED: "处理中", STRUCTURED: "分析已完成",
    CARE_TRIGGERED: "关怀准备中", CARE_READY: "关怀已就绪", DELIVERED_MOCK: "已送达",
    care_guide: "关怀指引", recommendation: "服务推荐", overall_service: "整体服务",
    clear: "内容清楚", not_clear: "内容不清楚", not_personalized: "不够个性化",
    cost_question: "费用疑问", recovery_question: "康复疑问", medication_question: "用药疑问",
    recommendation_question: "推荐疑问", claim_progress: "理赔进度", claim_material: "理赔材料",
    coverage_rule: "保障规则", value_added_service: "增值服务", human_service_request: "需要人工客服",
    other: "其他"
  };

  function esc(value) { const node = document.createElement("div"); node.textContent = value == null ? "" : String(value); return node.innerHTML; }
  function label(value) { return labels[value] || value || "—"; }
  function formatTime(value) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }
  function status(value) { return `<span class="tag">${esc(label(value))}</span>`; }
  function toast(message) { toastBox.textContent = message; toastBox.classList.remove("hidden"); setTimeout(() => toastBox.classList.add("hidden"), 2600); }
  function loading() { content.innerHTML = '<div class="empty">正在加载…</div>'; }
  function errorState(error) { content.innerHTML = `<section class="card card-pad"><div class="notice error">${esc(error.message)}</div></section>`; }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) { location.replace("/login"); throw new Error("登录已失效"); }
    if (!response.ok) throw new Error(body.detail || "请求失败");
    return body;
  }
  function detailItem(name, value, raw = false) { return `<div class="detail-item"><span>${esc(name)}</span>${raw ? value : `<strong>${esc(value == null ? "—" : value)}</strong>`}</div>`; }
  function caseRows(items) {
    if (!items.length) return '<div class="empty">暂时没有与您关联的案件</div>';
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>理赔事件</th><th>理赔状态</th><th>关怀状态</th><th>关怀结果</th><th>更新时间</th><th></th></tr></thead><tbody>${items.map(item => `<tr>
      <td><strong>${esc(item.claim_event_id)}</strong></td><td>${status(item.claim_status)}</td><td>${status(item.care_status)}</td>
      <td>${item.delivery_id ? '<span class="tag success">已送达</span>' : '<span class="tag warning">准备中</span>'}</td><td>${esc(formatTime(item.updated_at))}</td>
      <td><button class="link-btn open-case" data-id="${esc(item.case_id)}">查看</button></td></tr>`).join("")}</tbody></table></div>`;
  }
  function bindCases() { document.querySelectorAll(".open-case").forEach(button => { button.onclick = () => openCase(button.dataset.id); }); }

  async function home() {
    topTitle.textContent = "客户服务中心";
    const data = await api("/api/customer/dashboard");
    content.innerHTML = `<div class="page-head"><div><h1>${esc(currentUser.display_name)}，您好</h1><p class="muted">发起理赔申请、查看处理进度，并对关怀结果提交反馈</p></div><button class="btn primary" id="startClaim">发起理赔申请</button></div>
      <section class="metric-grid"><article class="card metric"><span class="muted">我的案件</span><strong>${data.total}</strong></article><article class="card metric"><span class="muted">结果已送达</span><strong>${data.results_available}</strong></article><article class="card metric"><span class="muted">结果准备中</span><strong>${data.awaiting_result}</strong></article><article class="card metric"><span class="muted">已提交反馈</span><strong>${data.feedback_submitted}</strong></article></section>
      <section class="card"><div class="card-pad section-title"><h2>最近案件</h2><button class="link-btn" id="allCases">查看全部</button></div>${caseRows(data.recent_cases)}</section>`;
    document.getElementById("startClaim").onclick = () => navigate("apply");
    document.getElementById("allCases").onclick = () => navigate("cases"); bindCases();
  }

  async function apply() {
    topTitle.textContent = "发起理赔申请";
    content.innerHTML = `<div class="page-head"><div><h1>发起理赔申请</h1><p class="muted">提交后系统会自动绑定当前账号，并进入理赔待受理队列</p></div></div>
      <section class="card card-pad profile-card"><form id="claimApplicationForm" class="profile-form">
        <div class="notice"><strong>办理说明</strong><br>此处用于建立理赔案件。医疗材料将在理赔人员受理后按授权与脱敏要求补充处理。</div>
        <div class="form-field"><label for="customerClaimEventId">报案号 / 理赔事件编号（选填）</label><input class="input" id="customerClaimEventId" maxlength="200" placeholder="没有编号可留空，由系统自动生成"></div>
        <p id="claimApplicationError" class="form-error"></p><button id="claimApplicationSubmit" class="btn primary">确认发起申请</button>
      </form></section>`;
    document.getElementById("claimApplicationForm").onsubmit = async event => {
      event.preventDefault();
      const button = document.getElementById("claimApplicationSubmit");
      const error = document.getElementById("claimApplicationError");
      button.disabled = true; button.textContent = "正在提交…"; error.textContent = "";
      try {
        const claimEventId = document.getElementById("customerClaimEventId").value.trim();
        const created = await api("/api/customer/cases", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ claim_event_id: claimEventId || null })
        });
        toast("理赔申请已提交，等待理赔人员受理");
        await openCase(created.case_id);
      } catch (exception) {
        error.textContent = exception.message; button.disabled = false; button.textContent = "确认发起申请";
      }
    };
  }

  async function cases() {
    topTitle.textContent = "我的案件";
    const items = await api("/api/customer/cases");
    content.innerHTML = `<div class="page-head"><div><h1>我的案件</h1><p class="muted">仅展示当前账号作为客户参与的案件</p></div></div><section class="card">${caseRows(items)}</section>`;
    bindCases();
  }

  async function results() {
    topTitle.textContent = "关怀结果";
    const items = (await api("/api/customer/cases")).filter(item => item.delivery_id);
    content.innerHTML = `<div class="page-head"><div><h1>关怀结果</h1><p class="muted">这里展示已经正式送达的温度指引与服务推荐</p></div></div><div class="result-list">${items.length ? items.map(item => `<article class="card card-pad result-summary"><div><strong>${esc(item.claim_event_id)}</strong><p class="muted">送达时间：${esc(formatTime(item.delivered_at))}</p></div><button class="btn primary open-case" data-id="${esc(item.case_id)}">查看结果</button></article>`).join("") : '<section class="card"><div class="empty">关怀结果正在准备中，送达后会在这里显示</div></section>'}</div>`;
    bindCases();
  }

  function careSection(section) { return section ? `<article class="care-section"><h3>${esc(section.title)}</h3><p>${esc(section.content)}</p></article>` : ""; }
  function actionList(title, items) { return items && items.length ? `<article class="care-section"><h3>${esc(title)}</h3><div class="action-list">${items.map(item => `<div><strong>${esc(item.action)}</strong><span>${esc(item.timing || "")}</span><p>${esc(item.reason)}</p></div>`).join("")}</div></article>` : ""; }
  function recommendationCards(reports) {
    const items = (reports || []).flatMap(report => (report.recommendations || []).map(item => ({ ...item, entity_type: report.entity_type, disease_group: report.disease_group })));
    if (!items.length) return "";
    return `<section class="card card-pad"><div class="section-title"><h2>服务推荐</h2><span class="tag">基于已审核画像</span></div><div class="recommend-grid">${items.map(item => `<article class="recommend-card"><strong>${esc(item.entity_id)}</strong><p class="muted">${esc(label(item.entity_type))} · ${esc(item.disease_group || "")}</p><ul>${(item.reasons || []).map(reason => `<li>${esc(reason.title)}：${esc(reason.explanation)}</li>`).join("")}</ul>${(item.limitations || []).length ? `<p class="muted">说明：${esc(item.limitations.join("；"))}</p>` : ""}</article>`).join("")}</div></section>`;
  }

  async function openCase(caseId) {
    loading();
    try {
      const item = await api("/api/customer/cases/" + encodeURIComponent(caseId));
      topTitle.textContent = "案件与关怀结果";
      const delivery = item.delivery;
      const guide = delivery && delivery.content.care_guide && delivery.content.care_guide.guide;
      const careHtml = guide ? `<section class="card card-pad care-guide"><div class="section-title"><div><h2>温度指引</h2><p class="muted">${esc(guide.disease_topic || "个性化关怀")}</p></div><span class="tag success">已正式送达</span></div>
        ${careSection(guide.opening)}${careSection(guide.diagnosis_explanation)}${careSection(guide.treatment_review)}${careSection(guide.cost_explanation)}
        ${actionList("康复计划", guide.recovery_plan)}${careSection(guide.psychological_support)}${actionList("复诊与随访", guide.follow_up_plan)}
        ${(guide.red_flags || []).length ? `<article class="notice error"><strong>需要及时就医的情况</strong><ul>${guide.red_flags.map(flag => `<li>${esc(flag)}</li>`).join("")}</ul></article>` : ""}
        ${(guide.limitations || []).length ? `<article class="notice"><strong>使用说明</strong><ul>${guide.limitations.map(note => `<li>${esc(note)}</li>`).join("")}</ul></article>` : ""}</section>` : "";
      content.innerHTML = `<button class="link-btn back-link" id="backCases">← 返回我的案件</button><section class="card case-header"><div class="case-header-main"><div><h1>${esc(item.claim_event_id)}</h1><div class="case-meta">${status(item.claim_status)}${status(item.care_status)}${delivery ? '<span class="tag success">结果已送达</span>' : '<span class="tag warning">结果准备中</span>'}</div></div><div><span class="muted">最近更新</span><p><strong>${esc(formatTime(item.updated_at))}</strong></p></div></div></section>
        ${delivery ? careHtml + recommendationCards(delivery.content.recommendation) + (delivery.content.recommendation_unavailable_reason ? `<div class="notice">${esc(delivery.content.recommendation_unavailable_reason)}</div>` : "") + feedbackForm(delivery, item.feedback_submitted) : '<section class="card"><div class="empty">您的关怀结果尚未正式送达，请稍后再来查看。</div></section>'}`;
      document.getElementById("backCases").onclick = () => navigate("cases");
      const form = document.getElementById("feedbackForm"); if (form) form.onsubmit = event => submitFeedback(event, delivery.delivery_id, caseId);
    } catch (error) { errorState(error); }
  }

  function feedbackForm(delivery, submitted) {
    if (submitted) return '<section class="card card-pad"><div class="notice">您已提交过本次关怀反馈，客服会在需要时与您联系。</div></section>';
    return `<section class="card card-pad feedback-card"><div class="section-title"><div><h2>告诉我们是否有帮助</h2><p class="muted">低评分或具体问题会进入客服跟进队列</p></div></div><form id="feedbackForm" class="feedback-form">
      <div class="form-grid"><div class="form-field"><label for="feedbackTarget">反馈对象</label><select class="input" id="feedbackTarget"><option value="overall_service">整体服务</option><option value="care_guide">关怀指引</option>${delivery.recommendation_included ? '<option value="recommendation">服务推荐</option>' : ""}</select></div>
      <div class="form-field"><label for="feedbackRating">整体评分</label><select class="input" id="feedbackRating"><option value="5">5 - 很满意</option><option value="4">4 - 满意</option><option value="3">3 - 一般</option><option value="2">2 - 不满意</option><option value="1">1 - 很不满意</option></select></div>
      <div class="form-field"><label for="feedbackCategory">问题类型</label><select class="input" id="feedbackCategory"><option value="clear">内容清楚</option><option value="not_clear">内容不清楚</option><option value="not_personalized">不够个性化</option><option value="cost_question">费用疑问</option><option value="recovery_question">康复疑问</option><option value="medication_question">用药疑问</option><option value="recommendation_question">推荐疑问</option><option value="claim_progress">理赔进度</option><option value="claim_material">理赔材料</option><option value="coverage_rule">保障规则</option><option value="human_service_request">需要人工客服</option><option value="other">其他</option></select></div>
      <div class="form-field"><label for="feedbackHelpful">是否有帮助</label><select class="input" id="feedbackHelpful"><option value="true">有帮助</option><option value="false">帮助不大</option></select></div></div>
      <div class="form-field"><label for="feedbackComment">补充说明</label><textarea class="input textarea" id="feedbackComment" maxlength="4000" placeholder="请勿填写身份证号、完整病历号等敏感身份信息"></textarea></div>
      <label class="checkbox"><input id="consentContact" type="checkbox"><span>我同意客服就本次反馈联系我</span></label><p id="feedbackError" class="form-error"></p><button id="feedbackSubmit" class="btn primary">提交反馈</button></form></section>`;
  }

  async function submitFeedback(event, deliveryId, caseId) {
    event.preventDefault(); const button = document.getElementById("feedbackSubmit"); const error = document.getElementById("feedbackError");
    button.disabled = true; error.textContent = "";
    try {
      await api(`/api/customer/deliveries/${encodeURIComponent(deliveryId)}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        target_type: document.getElementById("feedbackTarget").value, rating: Number(document.getElementById("feedbackRating").value), helpful: document.getElementById("feedbackHelpful").value === "true",
        category: document.getElementById("feedbackCategory").value, comment: document.getElementById("feedbackComment").value.trim(), consent_to_contact: document.getElementById("consentContact").checked
      }) }); toast("反馈已提交，谢谢您的帮助"); await openCase(caseId);
    } catch (exception) { error.textContent = exception.message; button.disabled = false; }
  }

  async function feedback() {
    topTitle.textContent = "我的反馈";
    const items = await api("/api/customer/feedback");
    content.innerHTML = `<div class="page-head"><div><h1>我的反馈</h1><p class="muted">仅展示由当前账号提交的反馈</p></div></div><div class="feedback-list">${items.length ? items.map(item => `<article class="card card-pad"><div class="section-title"><div><strong>${esc(label(item.category))}</strong><p class="muted">${esc(formatTime(item.created_at))}</p></div><span class="tag">${item.rating} 分</span></div><p>${esc(item.comment || "未填写补充说明")}</p><p class="muted">${item.helpful ? "有帮助" : "帮助不大"}${item.consent_to_contact ? " · 已同意客服回访" : ""}</p></article>`).join("") : '<section class="card"><div class="empty">您还没有提交反馈</div></section>'}</div>`;
  }

  async function profile() {
    topTitle.textContent = "个人信息";
    content.innerHTML = `<div class="page-head"><div><h1>个人信息</h1><p class="muted">账号和角色不可自行修改</p></div></div><section class="card card-pad profile-card"><form id="profileForm" class="profile-form"><div class="detail-grid profile-readonly">${detailItem("账号", currentUser.username)}${detailItem("角色", currentUser.role_name)}${detailItem("状态", status(currentUser.status), true)}</div><div class="form-field"><label for="displayName">显示名称</label><input class="input" id="displayName" required maxlength="100" value="${esc(currentUser.display_name || "")}"></div><div class="form-field"><label for="phone">手机号</label><input class="input" id="phone" maxlength="50" value="${esc(currentUser.phone || "")}"></div><div class="form-field"><label for="email">邮箱</label><input class="input" id="email" type="email" maxlength="254" value="${esc(currentUser.email || "")}"></div><p id="profileError" class="form-error"></p><button class="btn primary">保存资料</button></form></section>`;
    document.getElementById("profileForm").onsubmit = async event => { event.preventDefault(); const error = document.getElementById("profileError"); error.textContent = ""; try { currentUser = await api("/api/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: document.getElementById("displayName").value, phone: document.getElementById("phone").value, email: document.getElementById("email").value }) }); document.getElementById("userName").textContent = currentUser.display_name; toast("个人资料已更新"); } catch (exception) { error.textContent = exception.message; } };
  }

  function navigate(view) { activeView = view; document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view)); render(); }
  async function render() { loading(); try { await ({ home, apply, cases, results, feedback, profile })[activeView](); } catch (error) { errorState(error); } }
  document.querySelectorAll(".nav-item").forEach(item => { item.onclick = () => navigate(item.dataset.view); });
  document.getElementById("logoutButton").onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } };
  (async () => { try { currentUser = await api("/api/auth/me"); if (currentUser.role_code !== "CUSTOMER") throw new Error("当前账号无权进入客户工作区"); document.getElementById("userName").textContent = currentUser.display_name; await render(); } catch (error) { if (error.message.includes("无权进入")) { alert(error.message); location.replace("/login"); } } })();
})();
