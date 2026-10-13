(function () {
  const content = document.getElementById("content");
  const topTitle = document.getElementById("topTitle");
  const toastBox = document.getElementById("toast");
  let currentUser = null;
  let activeView = "home";

  const labels = {
    PENDING_ACCEPTANCE: "待理赔受理", ACCEPTED: "理赔已受理", COMPLETED: "理赔已完成",
    RECEIVED: "处理中", STRUCTURED: "分析已完成",
    CARE_TRIGGERED: "关怀准备中", CARE_READY: "关怀已就绪", PUBLISHED: "已发布", DELIVERED_MOCK: "已送达",
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
    content.innerHTML = `<div class="page-head"><div><h1>${esc(currentUser.display_name)}，您好</h1><p class="muted">查看已关联理赔案件、接收关怀结果并提交反馈</p></div><button class="btn primary" id="viewCases">查看已关联案件</button></div>
      <section class="metric-grid"><article class="card metric"><span class="muted">我的案件</span><strong>${data.total}</strong></article><article class="card metric"><span class="muted">结果已送达</span><strong>${data.results_available}</strong></article><article class="card metric"><span class="muted">结果准备中</span><strong>${data.awaiting_result}</strong></article><article class="card metric"><span class="muted">已提交反馈</span><strong>${data.feedback_submitted}</strong></article></section>
      <section class="card"><div class="card-pad section-title"><h2>最近案件</h2><button class="link-btn" id="allCases">查看全部</button></div>${caseRows(data.recent_cases)}</section>`;
    document.getElementById("viewCases").onclick = () => navigate("cases");
    document.getElementById("allCases").onclick = () => navigate("cases"); bindCases();
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
    content.innerHTML = `<div class="page-head"><div><h1>关怀结果</h1><p class="muted">这里展示已正式送达的就医回顾、关怀建议与可靠推荐</p></div></div><div class="result-list">${items.length ? items.map(item => `<article class="card card-pad result-summary"><div><strong>${esc(item.claim_event_id)}</strong><p class="muted">送达时间：${esc(formatTime(item.delivered_at))}</p></div><button class="btn primary open-case" data-id="${esc(item.case_id)}">查看结果</button></article>`).join("") : '<section class="card"><div class="empty">关怀结果正在准备中，送达后会在这里显示</div></section>'}</div>`;
    bindCases();
  }

  function careSection(section) { return section ? `<article class="care-section"><h3>${esc(section.title)}</h3><p>${esc(section.content)}</p></article>` : ""; }
  function actionList(title, items) { return items && items.length ? `<article class="care-section"><h3>${esc(title)}</h3><div class="action-list">${items.map(item => `<div><strong>${esc(item.action)}</strong><span>${esc(item.timing || "")}</span><p>${esc(item.reason)}</p></div>`).join("")}</div></article>` : ""; }
  function resultSection(kicker, title, body, extraClass = "") {
    return `<section class="card card-pad customer-section ${extraClass}"><div class="section-title"><div><span class="customer-section-kicker">${esc(kicker)}</span><h2>${esc(title)}</h2></div></div>${body}</section>`;
  }
  function emptyResult(message) { return `<div class="customer-empty-state">${esc(message)}</div>`; }
  function normalizedRecommendationType(item, report) {
    const raw = String(item.item_type || report.entity_type || "").toUpperCase();
    return ({ INSTITUTION: "HOSPITAL", CLINICIAN: "DOCTOR", TREATMENT_PLAN: "TREATMENT" })[raw] || raw;
  }
  function recommendationCards(reports, unavailableReason) {
    const items = (reports || []).flatMap(report => (report.recommendations || []).map(item => ({
      ...item, customer_type: normalizedRecommendationType(item, report)
    }))).filter(item => item.entity_name);
    const groups = [
      ["HOSPITAL", "医院建议", "仅展示已有可靠名称和依据的医院信息"],
      ["DOCTOR", "医生建议", "暂无可靠医生推荐"],
      ["TREATMENT", "就医与进一步评估方向", "暂无经审核的就医或进一步评估方向"],
    ];
    const body = groups.map(([type, title, emptyCopy]) => {
      const matches = items.filter(item => item.customer_type === type);
      return `<article class="recommend-group"><h3>${esc(title)}</h3>${matches.length ? `<div class="recommend-grid">${matches.map(item => `<article class="recommend-card"><strong>${esc(item.entity_name)}</strong>${item.city || item.department ? `<p class="muted">${[item.city, item.department].filter(Boolean).map(esc).join(" · ")}</p>` : ""}${(item.reasons || []).length ? `<ul>${item.reasons.map(reason => `<li>${esc(reason.title || "推荐理由")}${reason.explanation ? `：${esc(reason.explanation)}` : ""}</li>`).join("")}</ul>` : ""}${(item.sources || []).length ? `<div class="recommend-sources">${item.sources.map(source => `<p>${esc(source.summary || "推荐依据")}${source.name ? ` · ${source.url ? `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.name)}</a>` : esc(source.name)}` : ""}</p>`).join("")}</div>` : ""}${(item.limitations || []).length ? `<p class="muted">说明：${esc(item.limitations.join("；"))}</p>` : ""}</article>`).join("")}</div>` : `<p class="muted">${esc(emptyCopy)}</p>`}</article>`;
    }).join("");
    return resultSection("下次就医", "下次就医推荐", `${body}${unavailableReason && !items.length ? `<div class="notice customer-delivery-note">${esc(unavailableReason)}</div>` : ""}<div class="customer-boundary">推荐基于当前已审核资料与可核验信息，仅供后续就医沟通参考。</div>`);
  }

  async function openCase(caseId) {
    loading();
    try {
      const item = await api("/api/customer/cases/" + encodeURIComponent(caseId));
      topTitle.textContent = "案件与关怀结果";
      const delivery = item.delivery;
      const guide = delivery && delivery.content.care_guide && delivery.content.care_guide.guide;
      const openingHtml = guide && guide.opening ? resultSection("给您的话", guide.opening.title || "关怀说明", `<p class="customer-opening">${esc(guide.opening.content)}</p>`, "customer-opening-card") : "";
      const reviewBody = guide ? [careSection(guide.diagnosis_explanation), careSection(guide.treatment_review), careSection(guide.cost_explanation)].filter(Boolean).join("") : "";
      const reviewHtml = resultSection("本次就医", "本次就医回顾", reviewBody || emptyResult("本次交付暂未包含可展示的病情、治疗或费用回顾。"));
      const attentionItems = delivery && delivery.content.attention_items || [];
      const attentionHtml = resultSection("医疗审核后展示", "需要关注", attentionItems.length ? `<div class="action-list">${attentionItems.map(item => `<div><strong>${esc(item.title)}</strong><span>${esc(label(item.severity))}</span><p>${esc(item.summary)}</p><p class="muted">建议：${esc(item.suggestion)}</p></div>`).join("")}</div>` : emptyResult("经医疗人员审核后可面向客户展示的关注事项，将在确认后显示在这里。"));
      const nextBody = guide ? [actionList("康复计划", guide.recovery_plan), careSection(guide.psychological_support), actionList("复诊与随访", guide.follow_up_plan),
        (guide.red_flags || []).length ? `<article class="notice error"><strong>需要及时就医的情况</strong><ul>${guide.red_flags.map(flag => `<li>${esc(flag)}</li>`).join("")}</ul></article>` : "",
        (guide.limitations || []).length ? `<article class="notice"><strong>使用说明</strong><ul>${guide.limitations.map(note => `<li>${esc(note)}</li>`).join("")}</ul></article>` : ""].filter(Boolean).join("") : "";
      const nextHtml = resultSection("康复关怀", "康复与下一步建议", nextBody || emptyResult("本次交付暂未包含可展示的康复与随访建议。"));
      const quality = delivery && delivery.content.quality_observation;
      const qualityHtml = resultSection("本次病例", "本次就医品质观察", `${quality?`<p>${esc(quality.summary)}</p>`:emptyResult("本次交付暂未包含经医疗审核、可面向客户展示的品质观察。")}<div class="customer-boundary">${esc(quality&&quality.disclaimer||"单次病例观察不代表医院或医生的长期质量。")}</div>`);
      const recommendationHtml = recommendationCards(delivery && delivery.content.recommendation, delivery && delivery.content.recommendation_unavailable_reason);
      content.innerHTML = `<button class="link-btn back-link" id="backCases">← 返回我的案件</button><section class="card case-header"><div class="case-header-main"><div><h1>${esc(item.claim_event_id)}</h1><div class="case-meta">${status(item.claim_status)}${status(item.care_status)}${delivery ? '<span class="tag success">结果已送达</span>' : '<span class="tag warning">结果准备中</span>'}</div></div><div><span class="muted">最近更新</span><p><strong>${esc(formatTime(item.updated_at))}</strong></p></div></div></section>
        ${delivery ? `<div class="customer-result-stack">${openingHtml}${reviewHtml}${attentionHtml}${nextHtml}${qualityHtml}${recommendationHtml}${feedbackForm(delivery, item.feedback_submitted)}</div>` : '<section class="card"><div class="empty">您的关怀结果尚未正式送达，请稍后再来查看。</div></section>'}`;
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
  async function render() { loading(); try { await ({ home, cases, results, feedback, profile })[activeView](); } catch (error) { errorState(error); } }
  document.querySelectorAll(".nav-item").forEach(item => { item.onclick = () => navigate(item.dataset.view); });
  document.getElementById("logoutButton").onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } };
  (async () => { try { currentUser = await api("/api/auth/me"); if (currentUser.role_code !== "CUSTOMER") throw new Error("当前账号无权进入客户工作区"); document.getElementById("userName").textContent = currentUser.display_name; await render(); } catch (error) { if (error.message.includes("无权进入")) { alert(error.message); location.replace("/login"); } else { errorState(error); } } })();
})();
