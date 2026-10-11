(function () {
  const content = document.getElementById("content");
  const topTitle = document.getElementById("topTitle");
  let currentUser = null;
  let activeView = "home";
  let activeCaseId = null;
  let activeTab = "overview";
  let profileMode = "view";
  let currentCaseData = null;
  let fieldReviewContext = null;

  const labels = {
    ACTIVE: "启用", CARE_TRIGGERED: "新进入医护关怀", PROCESSING: "处理中",
    PAIN_READY: "痛点分析已完成", CARE_READY: "关怀指引已生成",
    REVIEW_REQUIRED: "需要医疗复核", APPROVED: "已批准发布", RETURNED: "已退回修改",
    NEEDS_CONFIRMATION: "需进一步确认", PENDING: "待复核", REJECTED: "已退回修改",
    BLOCKED: "禁止发布", DELIVERY_READY: "等待发送", DELIVERED_MOCK: "已发送",
    VERIFIED: "已核验", PARTIALLY_VERIFIED: "部分核验",
    PENDING_VERIFICATION: "待核验", CONTRADICTED: "存在矛盾",
    NOT_GENERATED: "未生成", READY: "可生成", GENERATING: "生成中",
    GENERATED: "已生成", INPUT_INSUFFICIENT: "输入不足", STALE: "已过期",
    NOT_REVIEWED: "未复核",
    clinical: "临床", cost: "费用", experience: "就医体验",
    low: "低", medium: "中", high: "高", critical: "紧急",
    extracted: "已提取", derived: "推导", review_required: "待确认",
    conflict: "存在冲突", not_present_in_document: "未见记录", not_applicable: "不适用",
    OBSERVED: "已有观察", DATA_UNAVAILABLE: "数据缺失", AVAILABLE: "可查看", UNAVAILABLE: "未保留"
    ,CONFIRMED: "人工已确认", CORRECTED: "人工已修正", UNRESOLVED: "暂无法确认"
  };
  function esc(value) { return String(value == null ? "—" : value).replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[c]); }
  function label(value) { return labels[value] || (value == null || value === "" ? "—" : String(value)); }
  function tag(value) {
    const danger = ["BLOCKED", "CONTRADICTED", "REJECTED", "RETURNED", "critical"].includes(value);
    const warning = ["PENDING", "REVIEW_REQUIRED", "NEEDS_CONFIRMATION", "PARTIALLY_VERIFIED", "PENDING_VERIFICATION", "high"].includes(value);
    const success = ["APPROVED", "VERIFIED", "CARE_READY", "extracted", "OBSERVED"].includes(value);
    return `<span class="tag ${danger ? "danger" : warning ? "warning" : success ? "success" : ""}">${esc(label(value))}</span>`;
  }
  function formatTime(value) { if (!value) return "—"; const d = new Date(value); return Number.isNaN(d.getTime()) ? esc(value) : d.toLocaleString("zh-CN", {hour12:false}); }
  function toast(message) { const n=document.getElementById("toast"); n.textContent=message; n.classList.remove("hidden"); setTimeout(()=>n.classList.add("hidden"),3000); }
  async function api(path, options={}) {
    const response=await fetch(path,{credentials: "same-origin",...options});
    if(response.status===401){location.replace("/login");throw new Error("登录已失效");}
    const body=await response.json().catch(()=>({}));
    if(!response.ok){const d=Array.isArray(body.detail)?body.detail.map(i=>i.msg||"参数不正确").join("；"):body.detail;throw new Error(d||"请求失败");}
    return body;
  }
  function loading(){content.innerHTML='<div class="card empty">正在读取真实医疗复核数据…</div>';}
  function errorState(error){content.innerHTML=`<div class="card empty"><p>页面加载失败</p><p class="muted">${esc(error.message)}</p><button class="btn secondary" id="retry">重新加载</button></div>`;document.getElementById("retry").onclick=render;}
  function detailItem(name,value,raw=false){return `<div class="detail-item"><span>${esc(name)}</span>${raw?value:`<strong>${esc(value)}</strong>`}</div>`;}
  function caseRows(items){
    if(!items.length)return '<div class="empty">当前没有符合条件的医疗复核案件</div>';
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>案件编号</th><th>客户</th><th>主诊断</th><th>分析准备</th><th>待确认项</th><th>复核状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${items.map(item=>`<tr>
      <td>${esc(item.case_id)}</td><td>${esc(item.customer_display_name||"未关联客户")}</td><td>${esc(item.primary_diagnosis||"尚未提取")}</td>
      <td>${item.analysis_ready.care?tag("CARE_READY"):item.analysis_ready.pain?tag("PAIN_READY"):tag(item.care_status)}</td>
      <td>${item.pending_confirmation_count?tag("NEEDS_CONFIRMATION"):"0 项"}</td><td>${tag(item.review_status)}</td>
      <td>${formatTime(item.updated_at)}</td><td><button class="link-btn open-case" data-id="${esc(item.case_id)}">查看</button></td></tr>`).join("")}</tbody></table></div>`;
  }
  function bindCaseLinks(){document.querySelectorAll(".open-case").forEach(b=>b.onclick=()=>openCase(b.dataset.id));}

  async function home(){
    topTitle.textContent="医疗复核工作台";const data=await api("/api/medical/dashboard");
    content.innerHTML=`<div class="page-head"><div><h1>首页</h1><p class="muted">查看分配给您的医学分析与发布复核任务</p></div></div>
      <section class="metric-grid"><article class="card metric"><span class="muted">待复核案件</span><strong>${data.pending_review}</strong></article>
      <article class="card metric"><span class="muted">新进入医护关怀</span><strong>${data.new_care_cases}</strong></article>
      <article class="card metric"><span class="muted">存在待确认项</span><strong>${data.needs_confirmation}</strong></article>
      <article class="card metric"><span class="muted">已完成复核</span><strong>${data.completed_reviews}</strong></article></section>
      <section class="card"><div class="card-pad section-title"><h2>最近处理案件</h2></div>${caseRows(data.recent_cases)}</section>`;bindCaseLinks();
  }
  async function listCases(pending){
    topTitle.textContent=pending?"待复核案件":"我的案件";const items=await api("/api/medical/cases?pending_only="+pending);
    content.innerHTML=`<div class="page-head"><div><h1>${pending?"待复核案件":"我的案件"}</h1><p class="muted">${pending?"仅显示尚未批准发布的已分配案件":"仅显示已分配给您的医护关怀案件"}</p></div></div><section class="card">${caseRows(items)}</section>`;bindCaseLinks();
  }

  async function openCase(id,tab="overview"){activeCaseId=id;activeTab=tab;document.querySelectorAll(".nav-item").forEach(n=>n.classList.remove("active"));loading();try{currentCaseData=await api("/api/medical/cases/"+encodeURIComponent(id));renderCase(currentCaseData);}catch(e){errorState(e);}}
  function renderCase(data){
    topTitle.textContent="医疗案件详情";
    const tabs=[["overview","案件概览"],["materials","病例材料"],["structured","结构化病例"],["pain","痛点分析"],["care","温度指引"],["quality","本次就医品质评价"],["recommendation","就医推荐"],["review","医疗复核"]];
    content.innerHTML=`<section class="card case-header"><div class="case-header-main"><div><button class="link-btn" id="backCases">← 返回我的案件</button><h1>${esc(data.case_id)}</h1>
      <div class="case-meta"><span>客户：${esc(data.customer_display_name||"未关联客户")}</span>${tag(data.care_status)}${tag(data.review_status)}</div></div>
      <div class="case-review-summary"><span class="muted">主诊断</span><p><strong>${esc(data.primary_diagnosis||"尚未确认")}</strong></p><button class="btn secondary" id="openPendingFields">待确认项 ${esc((data.field_review_summary||{}).pending||0)}</button></div></div>
      <div class="analysis-toolbar"><div class="form-field compact"><label for="recommendationCity">当前推荐城市</label><input id="recommendationCity" class="input" maxlength="100" placeholder="生成就医推荐时填写" value="${esc((data.recommendation||{}).patient_city||"")}"></div><button class="btn primary" id="prepareAnalysis">准备医疗分析结果</button><span class="muted">普通待确认项不会阻止无关模块生成</span></div></section>
      <section class="card"><div class="tabs">${tabs.map(([id,name])=>`<button class="tab ${activeTab===id?"active":""}" data-tab="${id}">${name}</button>`).join("")}</div><div id="tabPanel" class="tab-panel"></div></section>`;
    document.getElementById("backCases").onclick=()=>navigate("cases");
    document.getElementById("openPendingFields").onclick=()=>{activeTab="structured";renderCase(data);setTimeout(()=>document.getElementById("pendingFieldSection")?.scrollIntoView({behavior:"smooth"}),0);};
    document.getElementById("prepareAnalysis").onclick=()=>prepareModules(data,["pain","care","treatment_quality","recommendation"]);
    document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{activeTab=b.dataset.tab;renderCase(data);});
    renderTab(data);
  }
  const moduleNames={pain:"痛点分析",care:"温度指引",treatment_quality:"本次就医品质评价",recommendation:"就医推荐"};
  function modulePanel(module,data){
    const state=(data.module_states||{})[module]||{};const stale=state.generation_status==="STALE";
    const notes=[...(state.blockers||[]),...(state.warnings||[])];
    return `<section class="module-status-panel"><div class="section-title"><div><strong>${esc(moduleNames[module])}</strong><p class="muted">生成状态 ${tag(state.generation_status||"NOT_GENERATED")} · 复核状态 ${tag(state.review_status||"NOT_REVIEWED")}</p></div><button class="btn secondary module-generate" data-module="${module}" ${state.generation_status==="INPUT_INSUFFICIENT"&&module!=="recommendation"?"disabled":""}>${state.result_available?"重新生成":"生成"}</button></div>
      ${stale?'<div class="notice warning">该结果基于旧病例版本生成，仍可查看，但重新生成后才能复核。</div>':state.result_available&&data.pending_confirmation_count?'<div class="notice warning">该结果基于仍有未确认字段的病例版本生成，请结合相关字段复核。</div>':""}
      ${notes.length?`<p class="muted">${notes.map(esc).join("；")}</p>`:""}
      ${state.result_available&&!stale?`<div class="module-review"><textarea class="input textarea module-review-note" data-module="${module}" maxlength="4000" placeholder="退回或需进一步确认时填写复核意见"></textarea><div class="profile-actions"><button class="btn primary module-review-action" data-module="${module}" data-decision="APPROVED">通过</button><button class="btn secondary module-review-action" data-module="${module}" data-decision="RETURNED">退回</button><button class="btn secondary module-review-action" data-module="${module}" data-decision="NEEDS_CONFIRMATION">需进一步确认</button></div></div>`:""}</section>`;
  }
  async function prepareModules(data,modules){
    const button=document.getElementById("prepareAnalysis");if(button){button.disabled=true;button.textContent="正在准备…";}
    const moduleButtons=[...document.querySelectorAll(".module-generate")].filter(item=>modules.includes(item.dataset.module));moduleButtons.forEach(item=>{item.disabled=true;item.textContent="生成中…";});
    try{const city=document.getElementById("recommendationCity")?.value.trim()||null;const response=await api(`/api/medical/cases/${encodeURIComponent(data.case_id)}/analysis/prepare`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({modules,use_llm:true,city})});const failed=Object.entries(response.outcomes||{}).filter(([,value])=>value.status==="FAILED"||value.status==="INPUT_INSUFFICIENT");toast(failed.length?"已生成可用模块，部分模块需要补充信息":"医疗分析结果已准备");await openCase(data.case_id,activeTab);}catch(error){toast(error.message);if(button){button.disabled=false;button.textContent="准备医疗分析结果";}moduleButtons.forEach(item=>{item.disabled=false;item.textContent=((data.module_states||{})[item.dataset.module]||{}).result_available?"重新生成":"生成";});}
  }
  function bindModuleControls(data){
    document.querySelectorAll(".module-generate").forEach(button=>button.onclick=()=>prepareModules(data,[button.dataset.module]));
    document.querySelectorAll(".module-review-action").forEach(button=>button.onclick=async()=>{const module=button.dataset.module;const note=document.querySelector(`.module-review-note[data-module="${module}"]`)?.value||"";button.disabled=true;try{await api(`/api/medical/cases/${encodeURIComponent(data.case_id)}/module-reviews`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({module,decision:button.dataset.decision,comment:note})});toast("模块复核结果已保存");await openCase(data.case_id,activeTab);}catch(error){toast(error.message);button.disabled=false;}});
  }
  function valueOf(field){if(!field||typeof field!=="object")return field??"—";return field.normalized_value??field.value??"—";}
  function fieldReviewFor(data,path){return (data.field_reviews||[]).find(item=>item.field_path===path);}
  function evidenceOf(field,path){const evidence=(field&&field.evidence)||[];if(!evidence.length)return "";return `<button class="link-btn source-action" data-path="${esc(path)}">查看来源</button>`;}
  function fieldCard(title,field,extra="",path="",data=currentCaseData){
    const review=fieldReviewFor(data,path);let values=`<p>${esc(valueOf(field))}</p>`;
    if(review&&review.decision==="CORRECTED")values=`<p class="muted">模型提取值：${esc(review.model_value)}</p><p><strong>人工确认值：${esc(review.final_value)}</strong></p>`;
    const reviewMeta=review?`<div class="field-review-meta">${tag(review.decision)} <small>${esc(review.reviewer_name)} · ${formatTime(review.reviewed_at)}</small>${review.reason?`<p>${esc(review.reason)}</p>`:""}</div>`:"";
    return `<article class="medical-item"><div class="section-title"><strong>${esc(title)}</strong>${review?tag(review.decision):tag(field&&field.status)}</div>${values}${field&&field.confidence!=null?`<small class="muted">可信度：${Math.round(field.confidence*100)}%</small>`:""}${extra}${reviewMeta}<div class="field-actions">${evidenceOf(field,path)}<button class="link-btn field-action" data-path="${esc(path)}" data-label="${esc(title)}" data-action="CORRECTED">修正字段</button></div></article>`;
  }
  function entityList(title,items,renderer){return `<section class="medical-section"><h3>${esc(title)}</h3>${items&&items.length?`<div class="medical-list">${items.map(renderer).join("")}</div>`:'<div class="empty compact">暂无相关记录</div>'}</section>`;}
  function pendingFieldList(data){const items=data.pending_field_reviews||[];return `<section id="pendingFieldSection" class="medical-section"><div class="section-title"><div><h3>待确认项</h3><p class="muted">仅列出低置信度、冲突或上游明确要求复核的字段</p></div><strong>${items.length} 项</strong></div>${items.length?`<div class="medical-list">${items.map(item=>`<article class="medical-item pending-field"><div><strong>${esc(item.field_label)}</strong><p>模型提取值：${esc(item.model_value)}</p><small class="muted">${esc(item.reason)}${item.confidence==null?"":` · 可信度 ${Math.round(item.confidence*100)}%`}</small></div><div class="field-actions"><button class="link-btn source-action" data-path="${esc(item.field_path)}">查看来源</button><button class="btn secondary field-action" data-action="CONFIRMED" data-path="${esc(item.field_path)}" data-label="${esc(item.field_label)}">确认无误</button><button class="btn secondary field-action" data-action="CORRECTED" data-path="${esc(item.field_path)}" data-label="${esc(item.field_label)}">修改并确认</button><button class="btn secondary field-action" data-action="UNRESOLVED" data-path="${esc(item.field_path)}" data-label="${esc(item.field_label)}">无法确认</button></div></article>`).join("")}</div>`:'<div class="empty compact">当前没有待确认的结构化字段</div>'}</section>`;}
  function reviewedFieldList(data){const items=data.field_reviews||[];if(!items.length)return "";return `<section class="medical-section"><h3>已处理字段</h3><div class="medical-list">${items.map(item=>`<article class="medical-item"><div class="section-title"><strong>${esc(item.field_label||item.field_path)}</strong>${tag(item.decision)}</div><p>模型提取值：${esc(item.model_value)}</p>${item.decision==="CORRECTED"?`<p><strong>人工确认值：${esc(item.final_value)}</strong></p>`:""}${item.reason?`<p class="muted">复核说明：${esc(item.reason)}</p>`:""}<small class="muted">复核人：${esc(item.reviewer_name)} · ${formatTime(item.reviewed_at)}</small><div class="field-actions"><button class="link-btn source-action" data-path="${esc(item.field_path)}">查看来源</button><button class="link-btn field-action" data-action="CORRECTED" data-path="${esc(item.field_path)}" data-label="${esc(item.field_label||item.field_path)}">再次修正</button></div></article>`).join("")}</div></section>`;}
  function renderStructured(data){
    const s=data.structured_result;if(!s)return '<div class="empty">结构化病例尚未生成</div>';
    const h=s.hospitalization||{};
    const corrected=(data.field_review_summary||{}).corrected||0;
    return `<div class="notice">低置信度、冲突或待确认字段请结合“查看来源”核对材料。高置信度字段无需逐项确认。</div>${corrected?'<div class="notice warning">已有字段被人工修正，当前痛点、关怀和推荐结果可能仍基于修正前内容。</div>':""}
      ${pendingFieldList(data)}${reviewedFieldList(data)}
      <section class="medical-section"><h3>住院信息</h3><div class="medical-grid">${fieldCard("入院日期",h.admission_date,"","hospitalization.admission_date",data)}${fieldCard("出院日期",h.discharge_date,"","hospitalization.discharge_date",data)}${fieldCard("住院天数",h.length_of_stay_days,"","hospitalization.length_of_stay_days",data)}${fieldCard("科室",h.department,"","hospitalization.department",data)}</div></section>
      ${entityList("诊断",s.diagnoses,(i,n)=>fieldCard(valueOf(i.name),i.name,`<p class="muted">类型：${esc(label(i.diagnosis_type))} · 确定性：${esc(label(i.certainty))}</p>`,`diagnoses[${n}].name`,data))}
      ${entityList("手术 / 操作",s.procedures,(i,n)=>fieldCard(valueOf(i.name),i.name,`<p class="muted">日期：${esc(valueOf(i.performed_date))} · 部位：${esc(valueOf(i.body_site))}</p>`,`procedures[${n}].name`,data))}
      ${entityList("用药",s.medications,(i,n)=>fieldCard(valueOf(i.name),i.name,`<p class="muted">剂量：${esc(valueOf(i.dose))} · 频次：${esc(valueOf(i.frequency))} · 途径：${esc(valueOf(i.route))}</p>`,`medications[${n}].name`,data))}
      ${entityList("检查",s.examinations,(i,n)=>fieldCard(valueOf(i.name),i.name,`<p class="muted">类别：${esc(label(i.category))} · 结论：${esc(valueOf(i.conclusion))}</p>`,`examinations[${n}].name`,data))}
      <section class="medical-section"><h3>费用</h3><div class="medical-grid">${fieldCard("总费用",(s.costs||{}).total_amount,"","costs.total_amount",data)}${fieldCard("政策范围内费用",(s.costs||{}).policy_scope_amount,"","costs.policy_scope_amount",data)}${fieldCard("政策范围外费用",(s.costs||{}).outside_policy_amount,"","costs.outside_policy_amount",data)}</div></section>
      ${entityList("医疗事件",s.events,i=>`<article class="medical-item"><strong>${esc(label(i.event_type))}</strong><p>${esc(i.description)}</p><small class="muted">${esc(i.event_time||"时间未记录")} · 可信度 ${Math.round((i.confidence||0)*100)}%</small></article>`)}
      ${entityList("待确认与质量提示",s.quality_flags,i=>`<article class="medical-item"><div class="section-title"><strong>${esc(i.message)}</strong>${tag(i.severity)}</div><p class="muted">${esc(i.field_path||"案件级提示")}</p></article>`)}`;
  }
  function renderPain(data){
    const p=data.pain_result;if(!p)return '<div class="empty">痛点分析尚未生成</div>';
    return `${modulePanel("pain",data)}<div class="summary-grid"><div class="summary-box"><h3>临床痛点</h3><strong>${p.summary.clinical}</strong></div><div class="summary-box"><h3>费用痛点</h3><strong>${p.summary.cost}</strong></div><div class="summary-box"><h3>就医体验痛点</h3><strong>${p.summary.experience}</strong></div></div>
      ${entityList("分析结果",p.pain_points,i=>`<article class="medical-item"><div class="section-title"><strong>${esc(i.title)}</strong><span>${tag(i.dimension)} ${tag(i.severity)}</span></div><p>${esc(i.description)}</p><p><strong>建议：</strong>${esc(i.recommended_action)}</p><small class="muted">可信度：${Math.round((i.confidence||0)*100)}% · ${i.review_required?"待医疗人员确认":"无需额外确认"}</small>
      ${i.evidence&&i.evidence.length?`<details class="evidence-details"><summary>查看证据</summary>${i.evidence.map(e=>`<p>${esc(e.text)} <small>${esc(e.document_id)} · 第 ${esc(e.page||"—")} 页</small></p>`).join("")}</details>`:""}</article>`)}`;
  }
  function careSection(title,section){return `<article class="medical-item"><h3>${esc(title)}</h3><p>${esc(section&&section.content||"暂无内容")}</p></article>`;}
  function renderCare(data){
    const r=data.care_result;if(!r)return '<div class="empty">温度指引尚未生成</div>';const g=r.guide||{};
    return `${modulePanel("care",data)}<div class="section-title"><div><h2>${esc(g.disease_topic||"温度指引")}</h2><p class="muted">证据覆盖：${Math.round((r.evidence_coverage||0)*100)}%</p></div>${tag(r.review_required?"REVIEW_REQUIRED":"APPROVED")}</div>
      <div class="medical-list">${careSection("开场关怀",g.opening)}${careSection("诊断通俗解释",g.diagnosis_explanation)}${careSection("治疗过程回顾",g.treatment_review)}${careSection("费用解释",g.cost_explanation)}${careSection("心理支持",g.psychological_support)}</div>
      ${entityList("康复建议",g.recovery_plan,i=>`<article class="medical-item"><strong>${esc(i.action)}</strong><p>${esc(i.timing)} · ${esc(i.reason)}</p></article>`)}
      ${entityList("下一步建议",g.follow_up_plan,i=>`<article class="medical-item"><strong>${esc(i.action)}</strong><p>${esc(i.timing)} · ${esc(i.reason)}</p></article>`)}
      ${entityList("安全检查",r.safety_checks,i=>`<article class="medical-item"><div class="section-title"><strong>${esc(i.message)}</strong>${tag(i.status==="blocked"?"BLOCKED":i.status==="review"?"REVIEW_REQUIRED":"APPROVED")}</div></article>`)}
      ${r.warnings&&r.warnings.length?`<div class="notice error">${r.warnings.map(esc).join("；")}</div>`:""}`;
  }
  function renderQuality(data){
    const q=data.quality||{};const observations=q.case_observations||[];const profiles=q.historical_profiles||[];
    const metricNames={cost_efficiency:"费用效率",medication_rationality:"用药合理性",treatment_standardization:"治疗规范性",treatment_diagnosis_match:"诊断与治疗匹配",procedure_rationality:"手术/操作合理性",treatment_completeness:"治疗记录完整性",risk_management:"风险管理",discharge_continuity:"出院与随访完整性",readmission:"再入院与结局",patient_feedback:"患者反馈",data_completeness:"数据完整性"};
    return `${modulePanel("treatment_quality",data)}<div class="notice warning"><strong>本次病例观察</strong><br>${esc(q.single_case_notice)}</div>
      ${entityList("案件级品质观察",observations,i=>`<article class="medical-item"><div class="section-title"><strong>${esc(metricNames[i.metric_code]||i.metric_code)}</strong>${tag(i.status)}</div><p>观察值：${esc(i.raw_value==null?"未获得":i.raw_value)} ${esc(i.unit||"")}</p>${i.evidence&&i.evidence.reasoning?`<p>${esc(i.evidence.reasoning)}</p>`:""}<small class="muted">来源：${esc(i.source_kind)} · 病种：${esc(i.disease_group)}</small></article>`)}
      ${!observations.length?'<p class="muted">当前尚未持久化本次病例品质观察；系统不会据此生成虚假评分。</p>':""}
      ${entityList("历史质量画像",profiles,p=>`<article class="medical-item"><div class="section-title"><strong>${esc(p.provider_id)} · ${esc(p.disease_group)}</strong>${tag(p.data_sufficiency_status)}</div><p>历史样本量：${esc(p.sample_size)}；综合结果：${esc(p.composite_score==null?"数据不足":p.composite_score)}</p><small class="muted">这是多病例聚合画像，与上方本次病例观察分开展示。</small></article>`)}`;
  }
  function renderRecommendation(data){
    const report=data.recommendation;if(!report)return '<div class="empty">当前案件尚无已保存的推荐报告</div>';
    const items=report.items||[];
    return `${modulePanel("recommendation",data)}<div class="notice">以下内容来自后端已保存的推荐结果；医疗端不重新计算排名或总分。</div>
      ${entityList("医院推荐",items.filter(i=>i.item_type==="HOSPITAL"),i=>recommendationCard(i))}
      ${entityList("医生推荐",items.filter(i=>i.item_type==="DOCTOR"),i=>recommendationCard(i))}
      ${entityList("治疗方向",items.filter(i=>i.item_type==="TREATMENT"),i=>recommendationCard(i))}`;
  }
  function recommendationCard(item){
    return `<article class="medical-item"><div class="section-title"><div><strong>#${esc(item.rank_no)} ${esc(item.entity_name)}</strong><p class="muted">${esc(item.city||"地区未记录")} · ${esc(item.department||"科室未记录")}</p></div><div><strong>${esc(item.total_score)}</strong> 分 ${tag(item.evidence_status)}</div></div>
      ${item.scores&&item.scores.length?`<div class="score-grid">${item.scores.map(s=>`<div><span>${esc(s.dimension_name)}</span><strong>${esc(s.score)}</strong><small>${esc(s.reason)}</small></div>`).join("")}</div>`:""}
      ${item.evidence&&item.evidence.length?`<details class="evidence-details"><summary>查看推荐依据</summary>${item.evidence.map(e=>`<div class="evidence-row"><strong>${esc(e.reason)}</strong><p>${esc(e.evidence_summary||"公开资料有限")}</p><small>${esc(e.source_name||"来源待补充")} · ${esc(label(e.evidence_status))}</small>${e.source_url?`<p><a href="${esc(e.source_url)}" target="_blank" rel="noopener">查看原始页面</a></p>`:""}</div>`).join("")}</details>`:""}</article>`;
  }
  function renderReview(data){
    const gate=data.review_gate||{};const ready=gate.decision==="REVIEW_REQUIRED";
    const parts=[["结构化病例",!!data.structured_result],["痛点分析",!!data.pain_result],["温度指引",!!data.care_result],["品质评价",(data.quality.case_observations||[]).length>0],["就医推荐",!!data.recommendation]];
    return `<div class="detail-grid">${parts.map(([name,ok])=>detailItem(name,tag(ok?"APPROVED":"PENDING"),true)).join("")}${detailItem("待确认问题",data.pending_confirmation_count+" 项")}${detailItem("当前复核状态",tag(data.review_status),true)}</div>
      ${((data.field_review_summary||{}).pending||0)||((data.field_review_summary||{}).unresolved||0)?`<div class="notice warning" style="margin-top:20px">当前仍有 ${(data.field_review_summary||{}).pending||0} 个结构化字段待处理、${(data.field_review_summary||{}).unresolved||0} 个字段暂无法确认。提交案件级复核前请结合风险判断。</div>`:""}
      ${(data.field_review_summary||{}).corrected?'<div class="notice warning" style="margin-top:12px">字段人工修正已保存；现有痛点、关怀或推荐可能基于修正前结果。</div>':""}
      <div class="notice ${gate.decision==="BLOCKED"?"error":""}" style="margin-top:20px"><strong>发布门禁：${esc(label(gate.decision))}</strong><br>${(gate.reasons||[]).map(esc).join("；")||"暂无门禁说明"}</div>
      ${entityList("历史复核记录",data.reviews||[],r=>`<article class="medical-item"><div class="section-title"><strong>${esc(r.reviewer)}</strong>${tag(r.decision)}</div><p>${esc(r.note||"未填写意见")}</p><small class="muted">${formatTime(r.created_at)}</small></article>`)}
      <form id="reviewForm" class="profile-form"><div class="form-field"><label for="reviewComment">医疗复核意见</label><textarea id="reviewComment" class="input textarea" maxlength="4000" placeholder="退回修改或标记进一步确认时必须填写"></textarea></div>
      <p id="reviewError" class="form-error"></p><div class="profile-actions"><button type="button" class="btn primary review-action" data-decision="APPROVED" ${ready?"":"disabled"}>批准发布</button><button type="button" class="btn secondary review-action" data-decision="RETURNED" ${ready?"":"disabled"}>退回修改</button><button type="button" class="btn secondary review-action" data-decision="NEEDS_CONFIRMATION" ${ready?"":"disabled"}>需进一步确认</button></div></form>
      <section class="medical-section"><h3>客户触达核对</h3><p class="muted">沿用原有安全门禁；扫码预览不会正式发送。</p><div class="profile-actions">${data.care_result?'<button type="button" class="btn secondary" id="mobilePreview">手机扫码预览</button>':""}${data.review_status==="APPROVED"?'<button type="button" class="btn primary" id="sendDelivery">执行模拟触达</button>':""}</div></section>`;
  }
  function bindReview(data){
    document.querySelectorAll(".review-action").forEach(button=>button.onclick=async()=>{
      const error=document.getElementById("reviewError");error.textContent="";document.querySelectorAll(".review-action").forEach(b=>b.disabled=true);
      try{const result=await api(`/api/medical/cases/${encodeURIComponent(data.case_id)}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({decision:button.dataset.decision,comment:document.getElementById("reviewComment").value})});toast("医疗复核结果已保存");await openCase(data.case_id,"review");}
      catch(e){error.textContent=e.message;document.querySelectorAll(".review-action").forEach(b=>b.disabled=false);}
    });
    const preview=document.getElementById("mobilePreview");if(preview)preview.onclick=()=>createMobilePreview(data);
    const send=document.getElementById("sendDelivery");if(send)send.onclick=()=>sendDelivery(data);
  }
  async function createMobilePreview(data){try{const result=await api(`/api/medical/cases/${encodeURIComponent(data.case_id)}/mobile-preview`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expires_in_minutes:30})});showMobilePreview(result);}catch(error){toast(error.message);}}
  function showMobilePreview(data){const backdrop=document.createElement("div");backdrop.className="dialog-backdrop";backdrop.innerHTML=`<section class="dialog mobile-preview-dialog" role="dialog" aria-modal="true"><h2>手机扫码核对客户页面</h2><div class="mobile-preview-body"><div class="mobile-qr-frame"><img src="${esc(data.qr_code_data_url)}" alt="手机预览二维码"></div><div class="mobile-preview-copy"><strong>临时预览链接</strong><p>该页面是当前触达内容的只读快照，不会正式发送。</p><label>预览地址</label><input readonly value="${esc(data.preview_url)}"><div class="mobile-security-note">请勿转发二维码；链接到期或服务重启后失效。</div></div></div><div class="dialog-actions"><button class="btn secondary" id="openPreviewUrl">本机打开</button><button class="btn primary" id="closePreview">关闭</button></div></section>`;document.body.appendChild(backdrop);const close=()=>backdrop.remove();document.getElementById("closePreview").onclick=close;document.getElementById("openPreviewUrl").onclick=()=>window.open(data.preview_url,"_blank","noopener");backdrop.onclick=event=>{if(event.target===backdrop)close();};}
  async function sendDelivery(data){const button=document.getElementById("sendDelivery");if(button)button.disabled=true;try{await api(`/api/medical/cases/${encodeURIComponent(data.case_id)}/delivery/send`,{method:"POST"});toast("模拟触达已完成");await openCase(data.case_id,"review");}catch(error){toast(error.message);if(button)button.disabled=false;}}
  function fieldAtPath(source,path){let value=source;for(const part of path.split(".")){const match=part.match(/^([^\[]+)(?:\[(\d+)\])?$/);if(!match||!value)return null;value=value[match[1]];if(match[2]!=null)value=Array.isArray(value)?value[Number(match[2])]:null;}return value;}
  function sourceRecords(data,path){const pending=(data.pending_field_reviews||[]).find(i=>i.field_path===path);if(pending)return pending.source_references||[];const reviewed=fieldReviewFor(data,path);if(reviewed)return reviewed.source_references||[];const field=fieldAtPath(data.structured_result,path);return field&&field.evidence||[];}
  function openSource(data,path){
    const sources=sourceRecords(data,path);const title=(data.pending_field_reviews||[]).find(i=>i.field_path===path)?.field_label||path;document.getElementById("sourceDialogTitle").textContent="查看来源 · "+title;
    document.getElementById("sourceDialogBody").innerHTML=sources.length?sources.map(source=>{const doc=(data.ocr_documents||[]).find(item=>item.document_id===source.document_id&&String(item.page||"")===String(source.page||""))||(data.ocr_documents||[]).find(item=>item.document_id===source.document_id);return `<section class="source-result"><div class="source-text"><strong>${esc(source.document_id||"来源材料")} · 第 ${esc(source.page||"—")} 页</strong><p>${esc(source.text||"未保存相关 OCR 片段")}</p></div><div class="source-preview">${doc&&doc.preview_available?`<img src="/api/medical/cases/${encodeURIComponent(data.case_id)}/documents/${encodeURIComponent(doc.resource_id)}" alt="${esc(doc.document_id)} 原始材料页面">`:'<div class="empty compact">该案件由旧流程处理，原始页面未保留；当前仅能核对已保存的 OCR 文字。</div>'}</div></section>`;}).join(""):'<div class="empty">该字段没有保存可定位的来源证据</div>';
    document.getElementById("sourceDialog").classList.remove("hidden");
  }
  function openFieldReview(data,button){
    const path=button.dataset.path,action=button.dataset.action,labelText=button.dataset.label||path;const field=fieldAtPath(data.structured_result,path);const pending=(data.pending_field_reviews||[]).find(i=>i.field_path===path);fieldReviewContext={caseId:data.case_id,path,action};document.getElementById("fieldReviewTitle").textContent=action==="CONFIRMED"?"确认字段无误":action==="CORRECTED"?"修改并确认":"标记为暂无法确认";document.getElementById("fieldReviewModel").textContent=`${labelText} · 模型提取值：${pending?pending.model_value:valueOf(field)}`;document.getElementById("fieldReviewValueWrap").classList.toggle("hidden",action!=="CORRECTED");document.getElementById("fieldReviewReasonWrap").classList.toggle("hidden",action==="CONFIRMED");document.getElementById("fieldReviewValue").value=action==="CORRECTED"?String(pending?pending.model_value:valueOf(field)):"";document.getElementById("fieldReviewReason").value="";document.getElementById("fieldReviewError").textContent="";document.getElementById("fieldReviewDialog").classList.remove("hidden");
  }
  function bindStructured(data){document.querySelectorAll(".source-action").forEach(button=>button.onclick=()=>openSource(data,button.dataset.path));document.querySelectorAll(".field-action").forEach(button=>button.onclick=()=>openFieldReview(data,button));}
  function renderTab(data){
    const panel=document.getElementById("tabPanel");
    if(activeTab==="overview")panel.innerHTML=`<div class="detail-grid">${detailItem("案件编号",data.case_id)}${detailItem("客户脱敏信息",data.customer_display_name||"未关联客户")}${detailItem("理赔事件编号",data.claim_event_id)}${detailItem("入院时间",data.admission_date||"未提取")}${detailItem("出院时间",data.discharge_date||"未提取")}${detailItem("主诊断",data.primary_diagnosis||"尚未确认")}${detailItem("材料准备状态",tag(data.structured_result?"APPROVED":"PENDING"),true)}${detailItem("医护关怀状态",tag(data.care_status),true)}${detailItem("当前复核状态",tag(data.review_status),true)}${detailItem("最近更新时间",formatTime(data.updated_at))}</div>`;
    if(activeTab==="materials")panel.innerHTML=data.ocr_documents.length?`<div class="notice warning">页面展示的是上传方已确认完成脱敏的材料版本；系统没有对原图执行二次视觉脱敏。</div>${data.ocr_documents.map(d=>`<article class="medical-item source-document"><div class="section-title"><strong>${esc(d.document_id)} · 第 ${esc(d.page||"—")} 页</strong>${tag(d.preview_available?"AVAILABLE":"UNAVAILABLE")}</div>${d.preview_available?`<img class="document-preview" src="/api/medical/cases/${encodeURIComponent(data.case_id)}/documents/${encodeURIComponent(d.resource_id)}" alt="${esc(d.document_id)} 原始材料页面">`:'<div class="notice warning">该案件由旧流程处理，未保留可访问的原始页面。以下内容是已保存的 OCR 文字，不代表视觉脱敏文件。</div>'}<details class="evidence-details"><summary>查看 OCR 文字</summary><pre>${esc(d.full_text||"未识别到文字")}</pre></details></article>`).join("")}`:'<div class="empty">当前没有已保存的 OCR 材料</div>';
    if(activeTab==="structured"){panel.innerHTML=renderStructured(data);bindStructured(data);}
    if(activeTab==="pain")panel.innerHTML=data.pain_result?renderPain(data):modulePanel("pain",data)+'<div class="empty">痛点分析尚未生成</div>';
    if(activeTab==="care")panel.innerHTML=data.care_result?renderCare(data):modulePanel("care",data)+'<div class="empty">温度指引尚未生成</div>';
    if(activeTab==="quality")panel.innerHTML=renderQuality(data);
    if(activeTab==="recommendation")panel.innerHTML=data.recommendation?renderRecommendation(data):modulePanel("recommendation",data)+'<div class="empty">当前案件尚无已保存的推荐报告</div>';
    if(["pain","care","quality","recommendation"].includes(activeTab))bindModuleControls(data);
    if(activeTab==="review"){panel.innerHTML=renderReview(data);bindReview(data);}
  }

  async function profile(){
    topTitle.textContent="个人信息";
    if(profileMode==="edit"){renderProfileEdit();return;}if(profileMode==="password"){renderPasswordEdit();return;}
    content.innerHTML=`<div class="page-head"><div><h1>个人信息</h1><p class="muted">账号资料来自当前登录身份</p></div></div><section class="card card-pad"><div class="detail-grid">${detailItem("显示名称",currentUser.display_name)}${detailItem("账号 / 工号",currentUser.username)}${detailItem("角色",currentUser.role_name)}${detailItem("账号状态",tag(currentUser.status),true)}${detailItem("手机号",currentUser.phone||"未填写")}${detailItem("邮箱",currentUser.email||"未填写")}</div><div class="profile-actions"><button class="btn primary" id="editProfile">编辑资料</button><button class="btn secondary" id="changePassword">修改密码</button></div></section>`;
    document.getElementById("editProfile").onclick=()=>{profileMode="edit";profile();};document.getElementById("changePassword").onclick=()=>{profileMode="password";profile();};
  }
  function renderProfileEdit(){
    content.innerHTML=`<div class="page-head"><div><h1>编辑个人资料</h1><p class="muted">账号、角色和状态不可自行修改</p></div></div><section class="card card-pad profile-card"><form id="profileForm" class="profile-form"><div class="form-field"><label>显示名称</label><input id="profileDisplayName" class="input" maxlength="100" required value="${esc(currentUser.display_name)}"></div><div class="form-field"><label>手机号</label><input id="profilePhone" class="input" maxlength="50" value="${esc(currentUser.phone||"")}"></div><div class="form-field"><label>邮箱</label><input id="profileEmail" class="input" type="email" maxlength="254" value="${esc(currentUser.email||"")}"></div><p id="profileError" class="form-error"></p><div class="profile-actions"><button id="saveProfile" class="btn primary">保存</button><button id="cancelProfile" type="button" class="btn secondary">取消</button></div></form></section>`;
    document.getElementById("cancelProfile").onclick=()=>{profileMode="view";profile();};document.getElementById("profileForm").onsubmit=async e=>{e.preventDefault();const b=document.getElementById("saveProfile"),er=document.getElementById("profileError");b.disabled=true;b.textContent="保存中…";try{currentUser=await api("/api/auth/me",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({display_name:document.getElementById("profileDisplayName").value,phone:document.getElementById("profilePhone").value,email:document.getElementById("profileEmail").value})});document.getElementById("userName").textContent=currentUser.display_name;profileMode="view";toast("个人资料已更新");profile();}catch(x){er.textContent=x.message;b.disabled=false;b.textContent="保存";}};
  }
  function renderPasswordEdit(){
    content.innerHTML=`<div class="page-head"><div><h1>修改密码</h1></div></div><section class="card card-pad profile-card"><form id="passwordForm" class="profile-form"><div class="form-field"><label>当前密码</label><input id="currentPassword" class="input" type="password" required></div><div class="form-field"><label>新密码</label><input id="newPassword" class="input" type="password" minlength="8" required></div><div class="form-field"><label>确认新密码</label><input id="confirmNewPassword" class="input" type="password" minlength="8" required></div><p id="passwordError" class="form-error"></p><div class="profile-actions"><button id="savePassword" class="btn primary">保存新密码</button><button id="cancelPassword" type="button" class="btn secondary">取消</button></div></form></section>`;
    document.getElementById("cancelPassword").onclick=()=>{profileMode="view";profile();};document.getElementById("passwordForm").onsubmit=async e=>{e.preventDefault();const b=document.getElementById("savePassword"),er=document.getElementById("passwordError");b.disabled=true;b.textContent="修改中…";try{const r=await api("/api/auth/change-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({current_password:document.getElementById("currentPassword").value,new_password:document.getElementById("newPassword").value,confirm_new_password:document.getElementById("confirmNewPassword").value})});profileMode="view";toast(r.message);profile();}catch(x){er.textContent=x.message;b.disabled=false;b.textContent="保存新密码";}};
  }
  function navigate(view){activeView=view;activeCaseId=null;profileMode="view";document.querySelectorAll(".nav-item").forEach(n=>n.classList.toggle("active",n.dataset.view===view));render();}
  document.getElementById("cancelFieldReview").onclick=()=>document.getElementById("fieldReviewDialog").classList.add("hidden");
  document.getElementById("closeSourceDialog").onclick=()=>document.getElementById("sourceDialog").classList.add("hidden");
  document.getElementById("saveFieldReview").onclick=async()=>{if(!fieldReviewContext)return;const button=document.getElementById("saveFieldReview"),error=document.getElementById("fieldReviewError");button.disabled=true;button.textContent="保存中…";error.textContent="";try{await api(`/api/medical/cases/${encodeURIComponent(fieldReviewContext.caseId)}/field-reviews`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({field_path:fieldReviewContext.path,decision:fieldReviewContext.action,reviewed_value:fieldReviewContext.action==="CORRECTED"?document.getElementById("fieldReviewValue").value:null,reason:document.getElementById("fieldReviewReason").value})});document.getElementById("fieldReviewDialog").classList.add("hidden");toast("字段复核结果已保存");await openCase(fieldReviewContext.caseId,"structured");}catch(e){error.textContent=e.message;}finally{button.disabled=false;button.textContent="保存";}};
  async function render(){loading();try{if(activeView==="home")await home();if(activeView==="pending")await listCases(true);if(activeView==="cases")await listCases(false);if(activeView==="profile")await profile();}catch(e){errorState(e);}}
  document.querySelectorAll(".nav-item").forEach(n=>n.onclick=()=>navigate(n.dataset.view));
  document.getElementById("logoutButton").onclick=async()=>{try{await api("/api/auth/logout",{method:"POST"});}finally{location.replace("/login");}};
  (async()=>{try{currentUser=await api("/api/auth/me");if(currentUser.role_code!=="MEDICAL")throw new Error("当前账号无权进入医疗复核工作区");document.getElementById("userName").textContent=currentUser.display_name;await render();}catch(e){if(e.message==="当前账号无权进入医疗复核工作区"){alert(e.message);location.replace("/login");}}})();
})();
