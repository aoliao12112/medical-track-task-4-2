const makeField = function(value, confidence, source, status, normalized, derivation) {
  return {
    value: value,
    normalized_value: normalized == null ? null : normalized,
    confidence: confidence,
    status: status || "extracted",
    evidence: source ? [{ document_id: source.doc, page: source.page, text: source.text, bbox: source.bbox || null }] : [],
    warnings: [],
    derivation: derivation || null
  };
};

const demoDocuments = [
  { id: "discharge-1", name: "出院记录 · 第1页", type: "出院小结", page: 1, text: "入院时间：2025-12-24 14:03；出院时间：2025-12-30 15:00；实际住院天数：6天。入院诊断：呼吸道感染。", ocrConfidence:.98 },
  { id: "discharge-2", name: "出院记录 · 第2页", type: "出院小结", page: 2, text: "出院诊断：呼吸道感染、肺炎、血液化学异常所见、胃肠功能紊乱。出院带药：酪酸梭菌活菌散1袋，口服，每日2次。", ocrConfidence:.97 },
  { id: "cost-1", name: "住院费用清单 · 第1页", type: "费用清单", page: 1, text: "住院天数6天。护理费、材料费、治疗费、检查费。", ocrConfidence:.95 },
  { id: "cost-2", name: "住院费用清单 · 第2页", type: "费用清单", page: 2, text: "药品费及项目明细。合计金额：4749.98元。", ocrConfidence:.96 },
  { id: "settlement-1", name: "医保结算单", type: "医保结算", page: 1, text: "费用总额4749.98元，医保综合支付2044.08元，个人现金支付2705.90元。", ocrConfidence:.99 }
];

function demoCase() {
  const src1 = { doc: "discharge-1", page: 1, text: "入院时间：2025-12-24 14:03；出院时间：2025-12-30 15:00；实际住院天数：6天" };
  const src2 = { doc: "discharge-2", page: 2, text: "出院诊断：呼吸道感染、肺炎、血液化学异常所见、胃肠功能紊乱" };
  const medSrc = { doc: "discharge-2", page: 2, text: "出院带药：酪酸梭菌活菌散1袋，口服，每日2次" };
  const costSrc = { doc: "settlement-1", page: 1, text: "费用总额4749.98元，医保综合支付2044.08元，个人现金支付2705.90元" };
  return {
    case_id: "DEMO-RESP-001",
    encounter_type: "inpatient",
    hospitalization: {
      admission_date: makeField("2025-12-24", .99, src1),
      discharge_date: makeField("2025-12-30", .99, src1),
      length_of_stay_days: makeField(6, .98, src1),
      department: makeField("儿科相关科室", .82, { doc: "discharge-1", page: 1, text: "科室记录不完整" }, "review_required")
    },
    diagnoses: [
      { diagnosis_id: "dx-1", name: makeField("呼吸道感染", .98, src2), code: makeField(null, null, null, "not_present_in_document"), diagnosis_type: "discharge", certainty: "confirmed" },
      { diagnosis_id: "dx-2", name: makeField("肺炎", .97, src2), code: makeField(null, null, null, "not_present_in_document"), diagnosis_type: "discharge", certainty: "confirmed" },
      { diagnosis_id: "dx-3", name: makeField("胃肠功能紊乱", .94, src2), code: makeField(null, null, null, "not_present_in_document"), diagnosis_type: "discharge", certainty: "confirmed" }
    ],
    procedures: [],
    medications: [
      { medication_id: "med-1", name: makeField("酪酸梭菌活菌散", .96, medSrc), dose: makeField("1袋", .91, medSrc), frequency: makeField("每日2次", .96, medSrc), route: makeField("口服", .95, medSrc), duration: makeField(null, null, null, "not_present_in_document"), usage_condition: makeField(null, null, null, "not_present_in_document"), max_daily_dose: makeField(null, null, null, "not_present_in_document"), additional_instructions: makeField(null, null, null, "not_present_in_document") }
    ],
    examinations: [],
    costs: {
      currency: "CNY",
      total_amount: makeField(4749.98, .99, costSrc),
      policy_scope_amount: missingField(),
      outside_policy_amount: missingField(),
      fund_payments: Object.assign({}, emptyFundPayments(), { total_fund_payment: makeField(2044.08, .99, costSrc) }),
      personal_burden: emptyPersonalBurden(),
      payment_channels: Object.assign({}, emptyPaymentChannels(), { cash_payment: makeField(2705.90, .99, costSrc) }),
      ratios: emptyCostRatios(),
      breakdown: [
        { category: makeField("床位与护理", .92, { doc: "cost-1", page: 1, text: "床位费、护理费" }), amount: makeField(780, .86, { doc: "cost-1", page: 1, text: "床位与护理相关费用" }) },
        { category: makeField("检查与检验", .93, { doc: "cost-1", page: 1, text: "检查费、检验费" }), amount: makeField(1746, .83, { doc: "cost-1", page: 1, text: "检查与检验项目" }) },
        { category: makeField("药品", .96, { doc: "cost-2", page: 2, text: "药品费小计" }), amount: makeField(317.17, .96, { doc: "cost-2", page: 2, text: "药品费小计317.17" }) }
      ],
      reconciliation: { status: "not_checked", checked_equations: [], difference: null, warnings: ["材料未拆分个人自付与个人自费"] }
    },
    events: [
      { event_id: "evt-1", event_type: "admission", event_time: "2025-12-24", description: "因发热、咳嗽入院", confidence: .98, evidence: [src1], related_entity_ids: [] },
      { event_id: "evt-2", event_type: "diagnosis", event_time: "2025-12-25", description: "完成相关检查并记录呼吸道感染、肺炎", confidence: .94, evidence: [src2], related_entity_ids: ["dx-1", "dx-2"] },
      { event_id: "evt-3", event_type: "discharge", event_time: "2025-12-30", description: "病情好转出院，携带院外用药", confidence: .97, evidence: [src2], related_entity_ids: ["med-1"] }
    ],
    quality_flags: [
      { code: "DEPARTMENT_REVIEW", severity: "warning", message: "科室名称来源不完整，建议人工核对。", field_path: "hospitalization.department", document_id: "discharge-1" },
      { code: "MEDICATION_DURATION_MISSING", severity: "info", message: "出院带药未提取到明确疗程。", field_path: "medications[0].duration", document_id: "discharge-2" }
    ]
  };
}

const $ = function(id) { return document.getElementById(id); };
let documents = structuredClone(demoDocuments);
let result = demoCase();
let selectedDocId = documents[0].id;
let currentTab = "overview";
let isDemoCase = true;
let backendUrl = localStorage.getItem("medicalClaimsBackend") || "";
let predictionData = null;
let goldData = null;
let painReport = null;
let careReport = null;
let qualityAudit = null;
let workflowCase = null;
let persistedPortfolio = null;
let currentRecommendations = null;
let qualityContext = null;
let qualityCoverageReport = null;
let multiSourceCandidatePools = null;
let qualityV2Catalog = null;
let qualityV2HospitalReport = null;
let qualityV2ClinicianRecommendations = [];
let qualityV2ClinicianProfiles = [];
let qualityV2TreatmentEvaluation = null;
let qualityV2SelectedHospitalId = null;
let qualityV2RequestId = 0;
let qualityV2CurrentCity = "";
let deliveryReadiness = null;
let inboxDeliveries = [];
let deliveryFeedback = [];
let deliveryFollowUps = [];
let activeDeliveryId = null;
let caseGeneration = 0;

function clearDeliveryState() {
  deliveryReadiness = null; inboxDeliveries = []; deliveryFeedback = []; deliveryFollowUps = []; activeDeliveryId = null;
  $("deliveryFeedbackStatus").textContent = '';
  renderDeliveryWorkflow();
}

function resetCaseState(caseId) {
  caseGeneration += 1;
  documents.forEach(function(doc){if(doc.previewUrl)URL.revokeObjectURL(doc.previewUrl);});
  workflowCase=null; documents=[]; selectedDocId=null; result=emptyWorkflowResult(caseId);
  painReport=null; careReport=null; qualityAudit=null; isDemoCase=false;
  qualityV2CurrentCity="";
  if ($("qualityV2CurrentCity")) $("qualityV2CurrentCity").value="";
  $("caseId").value=caseId;
  $("careTopic").value='';
  setCaseStatus('未加载当前案件结果', 'review');
  setPipelineState(0);
  clearCaseQuality(); clearDeliveryState();
  renderAll(); renderPainReport(); renderCareReport(); renderQualityAudit(); renderWorkflow();
}
function assertCaseResponse(payload, expectedId) {
  if (!payload || payload.case_id!==expectedId) throw new Error('后端 case_id mismatch，拒绝展示');
}
function assertCurrentGeneration(generation) {
  if (generation!==caseGeneration) throw new Error('案件已切换，忽略旧请求结果');
}

function clearCaseQuality() {
  persistedPortfolio = null; currentRecommendations = null; qualityContext = null; qualityCoverageReport = null;
  qualityV2HospitalReport = null; qualityV2ClinicianRecommendations = []; qualityV2ClinicianProfiles = [];
  qualityV2TreatmentEvaluation = null; qualityV2SelectedHospitalId = null; qualityV2RequestId += 1;
  renderIntegratedQuality();
  renderQualityV2();
}

async function workflowFetch(path, options) {
  if (!backendUrl) throw new Error("请先连接本地后端");
  const generation = caseGeneration;
  const response = await fetch(backendUrl.replace(/\/$/, "") + path, options);
  const payload = await response.json();
  if (generation !== caseGeneration) throw new Error("案件已切换，忽略旧请求结果");
  if (!response.ok) throw new Error(formatApiDetail(payload.detail || "案件请求失败"));
  return payload;
}

// 仅用于展示；接口、枚举值与保存数据保持原值。
function uiLabel(value) {
  return ({extracted:'已提取',derived:'已计算',review_required:'需要人工复核',missing:'信息缺失',conflict:'信息待核验',insufficient_sample:'样本不足',data_unavailable:'数据不可用',not_required:'无需复核',approved:'已通过',rejected:'已驳回',pending:'待确认',none:'尚未复核', RECEIVED:'材料待上传',PROCESSING:'正在处理',STRUCTURED:'病例已结构化',PAIN_READY:'痛点分析已完成',CARE_READY:'关怀指引已生成',DELIVERY_READY:'可以发送',AUTO_DELIVER:'可自动发送',REVIEW_REQUIRED:'需要人工复核',BLOCKED:'已阻止发送',APPROVED:'已通过',REJECTED:'已驳回',DELIVERED_MOCK:'已模拟触达',FEEDBACK_RECEIVED:'已收到反馈',FOLLOWUP_PENDING:'待处理',RESOLVED:'已完成',OPEN:'待处理',AUTO:'自动发送',HUMAN_APPROVED:'人工确认后发送',PENDING:'待确认',NOT_REQUIRED:'无需复核',NONE:'尚未复核',not_evaluated:'尚未评估',insufficient_evidence:'数据不足，暂无法推荐',available:'数据可用',unavailable:'暂不可用',calculated:'已计算',insufficient_samples:'样本不足',missing_data:'数据缺失',sufficient:'证据充足',partially_sufficient:'证据部分充足',insufficient:'证据不足',READY:'已就绪',NOT_READY:'尚未就绪',NOT_EVALUATED:'尚未评估',overall_service:'整体服务',care_guide:'关怀内容',recommendation:'推荐结果',combined_delivery:'本次发送的全部内容',clear:'内容清楚',not_clear:'内容不够清楚',not_personalized:'不够个性化',cost_question:'费用说明不清楚',recovery_question:'康复建议有疑问',medication_question:'用药内容有疑问',recommendation_question:'推荐结果有疑问',other:'其他',attending_physician:'主治医生',treating_physician:'经治医生',surgeon:'手术医生',reporting_physician:'报告医生',examining_physician:'检查医生',user_upload:'用户上传',authorized_historical:'已授权历史记录',synthetic_test:'工程测试数据',unknown:'来源未确认',mock_web_inbox:'模拟用户收件箱'})[value] || '待核验';
}
function uiReason(value) { return String(value).replaceAll('care_guide.review_required=true','关怀内容需要人工复核').replaceAll('knowledge_review_status','知识内容审核状态').replaceAll('review_required','需要人工复核').replaceAll('confidence','可信度').replaceAll('Care Guide','关怀指引').replaceAll('BLOCKED','已阻止发送').replaceAll('REVIEW_REQUIRED','需要人工复核').replaceAll('APPROVED','已通过').replaceAll('REJECTED','已驳回'); }
function uiStatus(value) {
  const tone = ['BLOCKED','REJECTED'].includes(value) ? 'error' : ['REVIEW_REQUIRED','FOLLOWUP_PENDING','OPEN','insufficient_evidence'].includes(value) ? 'review' : ['not_evaluated','NOT_EVALUATED','unavailable','RECEIVED'].includes(value) ? 'neutral' : 'ready';
  return '<span class="status-pill ' + tone + '">' + escapeHtml(uiLabel(value)) + '</span>';
}
function technicalDetails(value) {
  return '<details class="technical-details"><summary>查看技术详情</summary><pre>' + escapeHtml(typeof value === 'string' ? value : JSON.stringify(value,null,2)) + '</pre></details>';
}

function renderWorkflow() {
  $("workflowSummary").innerHTML = workflowCase ? '<div class="workflow-overall">' + uiStatus(workflowCase.status).replace('材料待上传', '等待上传材料') + '</div><div class="workflow-stages">' + [
    ['病例结构化', workflowCase.has_structured_result],
    ['痛点分析', workflowCase.has_pain_result],
    ['关怀指引', workflowCase.has_care_result]
  ].map(function(stage) {
    return '<div class="workflow-stage"><span>' + stage[0] + '</span><span class="status-pill ' + (stage[1] ? 'ready' : 'neutral') + '">' + (stage[1] ? '已完成' : '尚未开始') + '</span></div>';
  }).join('') + '</div>' : '创建新案件或读取已有案件，即可开始查看处理进展。';
}
function emptyWorkflowResult(caseId) {
  return emptyCase(caseId);
}
async function refreshWorkflow() {
  if (!workflowCase) return;
  const expectedId=workflowCase.case_id;
  const metadata=await workflowFetch("/api/workflow/cases/" + encodeURIComponent(expectedId));
  assertCaseResponse(metadata,expectedId); workflowCase=metadata;
  clearCaseQuality();
  clearDeliveryState();
  renderWorkflow();
}
async function createWorkflowCase() {
  if (!$("workflowConsent").checked) {
    showToast("请先确认已获得材料使用授权");
    return;
  }
  if (!$("workflowDeidentified").checked) {
    showToast("请先确认材料已完成脱敏处理");
    return;
  }
  resetCaseState("");
  try {
    const created = await workflowFetch("/api/workflow/cases", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim_event_id: "claim-demo-" + crypto.randomUUID(), data_source: "user_upload",
        consent_status: $("workflowConsent").checked ? "authorized" : "unknown", deidentification_status: $("workflowDeidentified").checked ? "confirmed" : "unknown" })
    });
    workflowCase = created;
    clearCaseQuality();
    clearDeliveryState();
    localStorage.setItem("medicalClaimsCaseId", created.case_id);
    $("caseId").value = created.case_id;
    result = emptyWorkflowResult(created.case_id);
    painReport = null; careReport = null; isDemoCase = false;
    if (documents.some(function(doc) { return !doc.file; })) documents = [];
    selectedDocId = documents.length ? documents[0].id : null;
    renderAll(); renderPainReport(); renderCareReport();
    await refreshWorkflow();
    showToast("模拟理赔案件已创建");
  } catch (error) { showToast("创建案件失败：" + error.message); }
}
async function restoreWorkflowCase(caseId) {
  resetCaseState(caseId);
  try {
    const metadata = await workflowFetch("/api/workflow/cases/" + encodeURIComponent(caseId));
    const saved = await workflowFetch("/api/workflow/cases/" + encodeURIComponent(caseId) + "/result");
    assertCaseResponse(metadata,caseId); assertCaseResponse(saved,caseId);
    [saved.structured_result,saved.pain_result,saved.care_result].filter(Boolean).forEach(function(item){assertCaseResponse(item,caseId);});
    workflowCase = metadata;
    clearCaseQuality();
    clearDeliveryState();
    localStorage.setItem("medicalClaimsCaseId", metadata.case_id);
    $("caseId").value = metadata.case_id;
    result = saved.structured_result || emptyWorkflowResult(metadata.case_id);
    painReport = saved.pain_result; careReport = saved.care_result; isDemoCase = false;
    documents = (saved.ocr_result || []).map(function(doc) {
      return { id: doc.document_id, name: doc.document_id, type: "已保存OCR", page: doc.page,
        text: doc.full_text, originalOcrText: doc.full_text, ocrBlocks: doc.blocks || [],
        humanCorrected: doc.human_corrected, ocrConfidence:null };
    });
    selectedDocId = documents.length ? documents[0].id : null;
    renderAll(); renderPainReport(); renderCareReport(); renderWorkflow();
    showToast("已从SQLite恢复案件；原始图片未保存");
  } catch (error) { showToast("恢复案件失败：" + error.message); }
}
function requireWorkflowCase() {
  if (!workflowCase || workflowCase.case_id !== $("caseId").value) throw new Error("请先创建案件或读取已有案件编号");
}

let batchCases = [];
let batchRunning = false;
let activeBatchResultIndex = null;
let batchScan = {root_name:null,total_files:0,total_images:0,unsupported_files:[],empty_directories:[],scanned_at:null};

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, function(c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c];
  });
}
function showToast(message) {
  const toast = $("toast");
  toast.textContent = uiReason(message).replace("后端 case_id mismatch，拒绝展示", "返回的案件编号不一致，已拒绝展示");
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(function() { toast.classList.remove("show"); }, 2600);
}
function formatApiDetail(detail) {
  if (Array.isArray(detail)) {
    return detail.map(function(item) {
      const location = Array.isArray(item.loc) ? item.loc.join(".") : "请求";
      return location + "：" + (item.msg || JSON.stringify(item));
    }).join("；");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return detail == null ? "无错误详情" : String(detail);
}
function clampConfidence(n) {
  return n == null ? "—" : (n * 100).toFixed(n >= .995 ? 0 : 1) + "%";
}
function confidenceClass(n) {
  return n != null && n < .9 ? "review" : "";
}
function missingField() {
  return makeField(null, null, null, "not_present_in_document");
}
function emptyFundPayments() {
  return { basic_pool_payment: missingField(), major_illness_payment: missingField(), medical_assistance_payment: missingField(), civil_servant_subsidy: missingField(), supplementary_insurance_payment: missingField(), commercial_insurance_payment: missingField(), other_payment: missingField(), total_fund_payment: missingField() };
}
function emptyPersonalBurden() {
  return { within_policy_self_pay: missingField(), outside_policy_self_expense: missingField(), total_personal_burden: missingField() };
}
function emptyPaymentChannels() {
  return { personal_account_payment: missingField(), family_account_payment: missingField(), cash_payment: missingField() };
}
function emptyCostRatios() {
  return { within_policy_self_pay_ratio: missingField(), outside_policy_self_expense_ratio: missingField(), total_personal_burden_ratio: missingField() };
}
function emptyCosts() {
  return { currency: "CNY", total_amount: missingField(), policy_scope_amount: missingField(), outside_policy_amount: missingField(), fund_payments: emptyFundPayments(), personal_burden: emptyPersonalBurden(), payment_channels: emptyPaymentChannels(), ratios: emptyCostRatios(), breakdown: [], reconciliation: { status: "not_checked", checked_equations: [], difference: null, warnings: [] } };
}
function emptyCase(caseId) {
  return {
    case_id: caseId,
    encounter_type: "unknown",
    hospitalization: { admission_date: missingField(), discharge_date: missingField(), length_of_stay_days: missingField(), department: missingField() },
    diagnoses: [],
    procedures: [],
    medications: [],
    examinations: [],
    costs: emptyCosts(),
    events: [],
    quality_flags: [{ code: "OCR_PENDING", severity: "warning", message: "材料尚未完成OCR识别。连接本地后端或补充OCR文本后继续。", field_path: null, document_id: null }]
  };
}
function classifyFile(name) {
  if (/病案首页|病历首页/i.test(name)) return "病案首页";
  if (/出院|小结/i.test(name)) return "出院小结";
  if (/手术|麻醉/i.test(name)) return "手术记录";
  if (/医嘱|用药|处方/i.test(name)) return "用药记录";
  if (/费用|清单/i.test(name)) return "费用清单";
  if (/结算|医保/i.test(name)) return "医保结算";
  if (/门诊|急诊|病历/i.test(name)) return "门急诊病历";
  if (/检查|检验|报告/i.test(name)) return "检查报告";
  return "待分类材料";
}
function addFiles(fileList) {
  const incoming = Array.from(fileList).map(function(file, index) {
    return {
      id: "upload-" + Date.now() + "-" + index,
      name: file.name,
      type: classifyFile(file.name),
      page: index + 1,
      text: "",
      file: file,
      ocrConfidence:null,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null
    };
  });
  if (!incoming.length) return;
  caseGeneration += 1; qualityAudit=null; clearCaseQuality(); clearDeliveryState();
  documents.forEach(function(x) { if (x.previewUrl) URL.revokeObjectURL(x.previewUrl); });
  documents = incoming;
  selectedDocId = incoming[0].id;
  isDemoCase = false;
  result = emptyCase($("caseId").value || "CASE-" + Date.now());
  painReport = null;
  careReport = null;
  renderPainReport(); renderCareReport(); renderQualityAudit();
  setCaseStatus("等待处理", "review");
  setPipelineState(-1);
  renderAll();
  showToast("已载入 " + incoming.length + " 页材料，等待本地OCR");
  renderPainReport();
  renderCareReport();
}
function setCaseStatus(text, type) {
  $("caseStatus").className = "status-pill " + type;
  $("caseStatus").textContent = text;
}
function setPipelineState(activeIndex, failed) {
  const items = Array.from(document.querySelectorAll("#pipelineSteps li"));
  items.forEach(function(item, index) {
    item.className = index < activeIndex ? "done" : (index === activeIndex ? (failed ? "review" : "active") : "");
  });
  if (activeIndex >= items.length) {
    items.forEach(function(item) { item.className = "done"; });
    if (result.quality_flags && result.quality_flags.length) items[items.length - 1].className = "review";
  }
  const labels = ["正在接收并校验病例材料", "正在识别文档类型与页码", "正在运行OCR并保留坐标", "正在提取关键字段并生成JSON", "正在核对日期、费用与跨文档冲突", "正在生成需要人工处理的复核任务"];
  $("pipelineSummary").textContent = activeIndex >= 0 && activeIndex < labels.length ? labels[activeIndex] : (activeIndex >= items.length ? "处理完成，结果已形成可追溯JSON" : "材料已就绪，等待运行");
}
function fieldCount(data) {
  const costs = data.costs || {};
  const fields = []
    .concat(Object.values(data.hospitalization || {}))
    .concat((data.diagnoses || []).flatMap(function(x) { return [x.name, x.code]; }))
    .concat((data.procedures || []).flatMap(function(x) { return [x.name, x.performed_date, x.body_site]; }))
    .concat((data.medications || []).flatMap(function(x) { return [x.name, x.dose, x.frequency, x.route, x.duration, x.usage_condition, x.max_daily_dose, x.additional_instructions]; }))
    .concat((data.examinations || []).flatMap(function(x) {
      return [x.name, x.performed_date, x.conclusion].concat((x.results || []).flatMap(function(r) { return [r.item_name, r.value, r.unit, r.reference_range]; }));
    }))
    .concat([costs.total_amount, costs.policy_scope_amount, costs.outside_policy_amount])
    .concat(Object.values(costs.fund_payments || {}))
    .concat(Object.values(costs.personal_burden || {}))
    .concat(Object.values(costs.payment_channels || {}))
    .concat(Object.values(costs.ratios || {}));
  return fields.filter(function(x) { return x && (x.status === "extracted" || x.status === "derived" || x.status === "review_required"); }).length;
}
function collectConfidences(data) {
  const found = [];
  const visit = function(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.confidence === "number") found.push(value.confidence);
    Object.values(value).forEach(visit);
  };
  visit(data);
  return found;
}
function fieldCard(label, field, full) {
  const value = field && field.value != null ? field.value : "未提取";
  const source = field && field.evidence ? field.evidence[0] : null;
  return '<article class="field-row ' + (full ? "full" : "") + '" data-doc="' + escapeHtml(source ? source.document_id : "") + '" data-evidence="' + escapeHtml(source ? source.text : "暂无原文证据") + '">' +
    '<div class="field-top"><span class="field-label">' + escapeHtml(label) + '</span><span class="confidence ' + confidenceClass(field ? field.confidence : null) + '">' + clampConfidence(field ? field.confidence : null) + '</span></div>' +
    '<strong class="field-value">' + escapeHtml(String(value)) + '</strong>' +
    '<span class="field-source">' + (source ? escapeHtml(source.document_id + " · 第" + (source.page || 1) + "页") : escapeHtml(field && field.status ? uiLabel(field.status) : "无证据")) + '</span></article>';
}
function moneyField(field) {
  if (!field || field.value == null) return field;
  return Object.assign({}, field, { value: "¥ " + Number(field.value).toFixed(2) });
}
function percentField(field) {
  if (!field || field.value == null) return field;
  return Object.assign({}, field, { value: (Number(field.value) * 100).toFixed(2) + "%" });
}
function emptyMessage(text, detail) {
  return '<div class="field-row full"><span class="field-label">' + escapeHtml(text) + '</span><strong class="field-value">' + escapeHtml(detail || "等待模型处理") + '</strong></div>';
}
function labelDiagnosis(v) {
  return ({ admission: "入院", discharge: "出院", primary: "主要", secondary: "次要", history: "既往" })[v] || "诊断";
}
function labelCertainty(v) {
  return ({ confirmed: "已确认", suspected: "疑似", ruled_out: "已排除" })[v] || "未标明";
}
function renderResults() {
  const content = $("resultContent");
  const costs = result.costs || emptyCosts();
  if (currentTab === "overview") {
    content.innerHTML =
      '<div class="section-title"><h3>住院信息</h3><span>跨文档一致性校验</span></div><div class="field-grid">' +
      fieldCard("入院日期", result.hospitalization.admission_date) + fieldCard("出院日期", result.hospitalization.discharge_date) +
      fieldCard("住院天数", result.hospitalization.length_of_stay_days) + fieldCard("科室", result.hospitalization.department) + '</div>' +
      '<div class="section-title"><h3>核心诊疗信息</h3><span>' + result.diagnoses.length + '项诊断 · ' + result.procedures.length + '项手术 · ' + result.medications.length + '项用药 · ' + (result.examinations || []).length + '项检查</span></div><div class="field-grid">' +
      (result.diagnoses.slice(0, 3).map(function(x) { return fieldCard("诊断 · " + labelDiagnosis(x.diagnosis_type), x.name, true); }).join("") || fieldCard("诊断", missingField(), true)) +
      (result.procedures.slice(0, 1).map(function(x) { return fieldCard("手术/操作", x.name, true); }).join("")) +
      (result.medications.slice(0, 2).map(function(x) { return fieldCard("出院用药", x.name, true); }).join("")) + '</div>' +
      '<div class="section-title"><h3>费用摘要</h3><span>人民币 · 自动核对</span></div><div class="field-grid">' +
      fieldCard("费用总额", moneyField(costs.total_amount)) + fieldCard("基金支付合计", moneyField(costs.fund_payments.total_fund_payment)) +
      fieldCard("个人自付（目录内）", moneyField(costs.personal_burden.within_policy_self_pay)) + fieldCard("个人自费（目录外）", moneyField(costs.personal_burden.outside_policy_self_expense)) + '</div>';
  } else if (currentTab === "diagnoses") {
    content.innerHTML = '<div class="section-title"><h3>诊断列表</h3><span>' + result.diagnoses.length + '项</span></div><div class="field-grid">' +
      (result.diagnoses.map(function(x) { return fieldCard(labelDiagnosis(x.diagnosis_type) + " · " + labelCertainty(x.certainty), x.name, true); }).join("") || emptyMessage("尚未提取诊断")) + '</div>';
  } else if (currentTab === "procedures") {
    content.innerHTML = '<div class="section-title"><h3>手术与操作</h3><span>' + result.procedures.length + '项</span></div><div class="field-grid">' +
      (result.procedures.map(function(x) { return fieldCard("手术/操作名称", x.name, true) + fieldCard("实施日期", x.performed_date) + fieldCard("部位", x.body_site); }).join("") || emptyMessage("当前材料未记录手术", "无手术不等于提取失败；正式结果应区分不适用和材料缺失")) + '</div>';
  } else if (currentTab === "medications") {
    content.innerHTML = '<div class="section-title"><h3>用药明细</h3><span>' + result.medications.length + '项</span></div>' +
      (result.medications.length ? '<div class="table-wrap"><table><thead><tr><th>药品</th><th>剂量</th><th>频次</th><th>规范频次</th><th>途径</th><th>疗程</th><th>最大日剂量</th></tr></thead><tbody>' +
      result.medications.map(function(m) { return '<tr><td>' + escapeHtml(m.name.value || "—") + '</td><td>' + escapeHtml(m.dose.value || "—") + '</td><td>' + escapeHtml(m.frequency.value || "—") + '</td><td>' + escapeHtml(m.frequency.normalized_value || "—") + '</td><td>' + escapeHtml(m.route.value || "—") + '</td><td>' + escapeHtml(m.duration.value || "未记录") + '</td><td>' + escapeHtml(m.max_daily_dose && (m.max_daily_dose.normalized_value || m.max_daily_dose.value) || "—") + '</td></tr>'; }).join("") +
      '</tbody></table></div>' : emptyMessage("尚未提取用药"));
  } else if (currentTab === "examinations") {
    const examinations = result.examinations || [];
    content.innerHTML = '<div class="section-title"><h3>辅助检查</h3><span>' + examinations.length + '项检查 · 检验结果逐项结构化</span></div>' +
      (examinations.length ? examinations.map(function(exam) {
        const rows = exam.results || [];
        return '<section class="exam-block"><div class="section-title compact"><h3>' + escapeHtml(exam.name && exam.name.value ? exam.name.value : "未命名检查") + '</h3><span>' + escapeHtml(({ laboratory: "检验", imaging: "影像", pathology: "病理", functional: "功能检查", other: "其他" })[exam.category] || "其他") + '</span></div>' +
          (rows.length ? '<div class="table-wrap"><table><thead><tr><th>项目</th><th>结果</th><th>单位</th><th>参考范围</th><th>标记</th><th>置信度</th></tr></thead><tbody>' +
          rows.map(function(row) { return '<tr><td>' + escapeHtml(row.item_name && row.item_name.value != null ? row.item_name.value : "—") + '</td><td>' + escapeHtml(row.value && row.value.value != null ? row.value.value : "—") + '</td><td>' + escapeHtml(row.unit && row.unit.value != null ? row.unit.value : "—") + '</td><td>' + escapeHtml(row.reference_range && row.reference_range.value != null ? row.reference_range.value : "—") + '</td><td>' + escapeHtml(({ high: "偏高", low: "偏低", positive: "阳性", negative: "阴性", abnormal: "异常", normal: "正常", unknown: "待判断" })[row.abnormal_flag] || "待判断") + '</td><td>' + clampConfidence(row.value ? row.value.confidence : null) + '</td></tr>'; }).join("") +
          '</tbody></table></div>' : emptyMessage("未提取到检查明细", exam.conclusion && exam.conclusion.value ? exam.conclusion.value : "可查看检查结论和原文证据")) + '</section>';
      }).join("") : emptyMessage("尚未提取辅助检查", "重新运行后，检验、影像和病理结果会显示在这里"));
  } else {
    content.innerHTML = '<div class="section-title"><h3>费用口径</h3><span>金额责任与支付渠道分层展示</span></div><div class="field-grid">' +
      fieldCard("费用总额", moneyField(costs.total_amount)) + fieldCard("医保目录内费用", moneyField(costs.policy_scope_amount)) +
      fieldCard("医保目录外费用", moneyField(costs.outside_policy_amount)) + '</div>' +
      '<div class="section-title"><h3>基金及第三方支付</h3><span>不同支付来源不得混填</span></div><div class="field-grid">' +
      fieldCard("基本医保统筹", moneyField(costs.fund_payments.basic_pool_payment)) + fieldCard("大病保险", moneyField(costs.fund_payments.major_illness_payment)) +
      fieldCard("医疗救助", moneyField(costs.fund_payments.medical_assistance_payment)) + fieldCard("补充医疗保险", moneyField(costs.fund_payments.supplementary_insurance_payment)) +
      fieldCard("商业保险", moneyField(costs.fund_payments.commercial_insurance_payment)) + fieldCard("基金支付合计", moneyField(costs.fund_payments.total_fund_payment)) + '</div>' +
      '<div class="section-title"><h3>个人负担属性</h3><span>自付与自费必须分开</span></div><div class="field-grid">' +
      fieldCard("个人自付（目录内）", moneyField(costs.personal_burden.within_policy_self_pay)) + fieldCard("个人自费（目录外）", moneyField(costs.personal_burden.outside_policy_self_expense)) +
      fieldCard("个人负担合计", moneyField(costs.personal_burden.total_personal_burden)) + fieldCard("个人负担比例", percentField(costs.ratios.total_personal_burden_ratio)) + '</div>' +
      '<div class="section-title"><h3>实际付款渠道</h3><span>不等同于自付/自费属性</span></div><div class="field-grid">' +
      fieldCard("个人账户支付", moneyField(costs.payment_channels.personal_account_payment)) + fieldCard("家庭共济账户", moneyField(costs.payment_channels.family_account_payment)) +
      fieldCard("现金支付", moneyField(costs.payment_channels.cash_payment)) + '</div>' +
      '<div class="section-title"><h3>分类费用</h3><span>来自费用清单</span></div>' +
      (costs.breakdown.length ? '<div class="table-wrap"><table><thead><tr><th>分类</th><th>金额</th><th>置信度</th></tr></thead><tbody>' +
      costs.breakdown.map(function(x) { return '<tr><td>' + escapeHtml(x.category.value) + '</td><td>¥ ' + Number(x.amount.value).toFixed(2) + '</td><td>' + clampConfidence(x.amount.confidence) + '</td></tr>'; }).join("") +
      '</tbody></table></div>' : emptyMessage("尚未提取费用明细"));
  }
  content.querySelectorAll(".field-row").forEach(function(row) {
    row.addEventListener("click", function() { showEvidence(row.dataset.doc, row.dataset.evidence); });
  });
}
function renderDocuments() {
  $("docCount").textContent = documents.length;
  $("documentList").innerHTML = documents.map(function(doc, index) {
    return '<button class="document-item ' + (doc.id === selectedDocId ? "active" : "") + '" data-id="' + escapeHtml(doc.id) + '">' +
      '<span class="doc-icon">' + (doc.previewUrl ? "▧" : "▤") + '</span><span class="doc-copy"><strong>' + escapeHtml(doc.name) + '</strong><span>' + escapeHtml(doc.type) + ' · 第' + (doc.page || index + 1) + '页</span></span>' +
      '<span class="doc-state ' + (doc.text ? "" : "pending") + '">' + (doc.humanCorrected ? "人工修正" : (doc.text ? "OCR " + (doc.ocrConfidence != null ? Math.round(doc.ocrConfidence * 100) + "%" : "已识别") : "待OCR")) + '</span></button>';
  }).join("");
  $("documentList").querySelectorAll(".document-item").forEach(function(button) {
    button.addEventListener("click", function() { selectedDocId = button.dataset.id; renderDocuments(); });
  });
  renderSelectedDocument();
}
function renderSelectedDocument() {
  const doc = documents.find(function(x) { return x.id === selectedDocId; }) || documents[0];
  if (!doc) {
    $("selectedDocName").textContent = "尚未添加材料";
    $("selectedDocType").textContent = "";
    $("ocrText").value = "";
    $("documentPreview").innerHTML = "";
    return;
  }
  $("selectedDocName").textContent = doc.name;
  $("selectedDocType").textContent = doc.type;
  $("ocrText").value = doc.text || "";
  const correctionStatus = $("ocrCorrectionStatus");
  correctionStatus.className = "ocr-note" + (doc.humanCorrected ? " corrected" : "");
  correctionStatus.textContent = doc.humanCorrected ?
    "本页已人工修正；点击“应用修正并重新结构化”后才会更新右侧字段。" :
    (doc.text ? "当前显示后端OCR结果；请重点核对模糊字、数字、单位、日期和否定词。" : "本页尚未完成OCR识别。");
  $("documentPreview").innerHTML = doc.previewUrl ?
    '<img alt="' + escapeHtml(doc.name) + '" src="' + doc.previewUrl + '"><div class="evidence-overlay" id="evidenceOverlay">证据定位</div>' :
    '<div class="paper-preview"><div class="paper-line wide"></div><div class="paper-line"></div><div class="paper-line short"></div><div class="paper-block"></div><div class="paper-line wide"></div><div class="paper-line"></div></div><div class="evidence-overlay" id="evidenceOverlay">证据定位</div>';
}
function showEvidence(docId, text) {
  if (docId && documents.some(function(x) { return x.id === docId; })) selectedDocId = docId;
  renderDocuments();
  const overlay = $("evidenceOverlay");
  overlay.textContent = text || "暂无原文证据";
  overlay.classList.add("visible");
  setTimeout(function() { overlay.classList.remove("visible"); }, 4200);
}
function labelEvent(v) {
  return ({ admission: "入院", diagnosis: "诊断", test: "检查", medication: "用药", procedure: "治疗", discharge: "出院" })[v] || "事件";
}
function renderTimeline() {
  $("timeline").innerHTML = result.events.length ? result.events.map(function(event) {
    return '<article class="timeline-item"><span class="timeline-node"></span><div class="timeline-time">' + escapeHtml(event.event_time || "时间待确认") + '</div><div class="timeline-copy">' + escapeHtml(event.description) + '</div><span class="timeline-tag">' + labelEvent(event.event_type) + ' · ' + clampConfidence(event.confidence) + '</span></article>';
  }).join("") : '<article class="timeline-item"><span class="timeline-node"></span><div class="timeline-time">等待处理</div><div class="timeline-copy">OCR完成后，按日期归并入院、诊断、手术、用药和出院事件。</div></article>';
}
function renderQuality() {
  $("qualityFlags").innerHTML = result.quality_flags.length ? result.quality_flags.map(function(flag) {
    const type = flag.severity === "error" ? "error" : (flag.severity === "info" ? "info" : "");
    return '<article class="quality-item ' + type + '"><span class="quality-icon">' + (flag.severity === "info" ? "i" : "!") + '</span><span class="quality-copy"><strong>' + "材料核验提示" + '</strong><span>' + escapeHtml(flag.message) + '</span></span></article>';
  }).join("") : '<article class="quality-item info"><span class="quality-icon">i</span><span class="quality-copy"><strong>无已报告质量标记</strong><span>未处理的案件不代表检查通过；无标记也不是医学安全保证。</span></span></article>';
}
function renderMetrics() {
  $("metricDocs").textContent = documents.length;
  const count = fieldCount(result);
  $("metricFields").textContent = count;
  $("metricCompleteness").textContent = isDemoCase ? "固定演示样例；不是比赛指标" : "当前非空字段计数；不是完整率";
  const confidences = collectConfidences(result);
  $("metricConfidence").textContent = !isDemoCase && confidences.length ? (confidences.reduce(function(a, b) { return a + b; }, 0) / confidences.length * 100).toFixed(1) : "—";
  $("metricFlags").textContent = result.quality_flags.length;
  $("metricDocDetail").textContent = Array.from(new Set(documents.map(function(x) { return x.type; }))).slice(0, 3).join(" · ") || "尚未添加材料";
}
function renderAll() {
  if (result.case_id !== $("caseId").value) result=emptyWorkflowResult($("caseId").value);
  renderDocuments();
  renderResults();
  renderTimeline();
  renderQuality();
  renderMetrics();
}
function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}
function mergeOcrDocuments(ocrDocuments) {
  const byPage = new Map((ocrDocuments || []).map(function(doc) { return [Number(doc.page), doc]; }));
  documents = documents.map(function(doc, index) {
    const ocr = byPage.get(index + 1);
    if (!ocr) return doc;
    const scores = (ocr.blocks || []).map(function(block) { return block.confidence; }).filter(function(value) { return value != null; });
    const average = scores.length ? scores.reduce(function(total, value) { return total + Number(value); }, 0) / scores.length : null;
    return Object.assign({}, doc, {
      id: ocr.document_id,
      name: ocr.document_id,
      page: ocr.page,
      text: ocr.full_text || "",
      originalOcrText: ocr.full_text || "",
      ocrBlocks: ocr.blocks || [],
      ocrConfidence:average,
      humanCorrected: Boolean(ocr.human_corrected)
    });
  });
  selectedDocId = documents.length ? documents[0].id : null;
}
async function processWithBackend() {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  requireWorkflowCase();
  if (!documents.some(function(doc) { return doc.file; })) {
    if (!documents.length || documents.some(function(doc) { return !doc.text; })) throw new Error("请上传图片或提供完整OCR文本");
    return workflowFetch("/api/cases/restructure-from-ocr", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: workflowCase.case_id, documents: documents.map(function(doc, index) {
        return { document_id: doc.name || doc.id, page: doc.page || index + 1,
          full_text: doc.text, blocks: doc.ocrBlocks || [], human_corrected: Boolean(doc.humanCorrected) };
      }) })
    });
  }
  const form = new FormData();
  form.append("case_id", $("caseId").value || "LOCAL-CASE");
  documents.forEach(function(doc) { if (doc.file) form.append("files", doc.file, doc.name); });
  const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/cases/process-detailed", { method: "POST", body: form });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (error) { payload = null; }
  if (!response.ok) {
    const detail = payload && payload.detail ? formatApiDetail(payload.detail) : (text || "无错误详情");
    throw new Error("后端 " + response.status + "：" + detail);
  }
  if (!payload) throw new Error("后端返回了空响应或非JSON内容");
  if (!payload.case || !Array.isArray(payload.documents)) throw new Error("后端版本过旧或详细OCR响应格式不正确");
  assertCurrentGeneration(generation); assertCaseResponse(payload.case,expectedId);
  mergeOcrDocuments(payload.documents);
  return payload.case;
}
async function restructureFromCorrectedOcr() {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  if (!backendUrl) { showToast("请先连接本地后端"); return; }
  const correctedCount = documents.filter(function(doc) { return doc.humanCorrected; }).length;
  if (!correctedCount) { showToast("请先修改OCR文本并保存本页修正"); return; }
  if (documents.some(function(doc) { return !doc.text; })) { showToast("仍有页面没有OCR文本，不能重新结构化"); return; }
  const button = $("applyOcrCorrectionsButton");
  button.disabled = true;
  button.textContent = "正在重新结构化…";
  setCaseStatus("应用OCR修正中", "processing");
  try {
    requireWorkflowCase();
    const payload = {
      case_id: $("caseId").value || result.case_id,
      documents: documents.map(function(doc, index) {
        return {
          document_id: doc.name || doc.id,
          page: doc.page || index + 1,
          full_text: doc.text || "",
          blocks: doc.ocrBlocks || [],
          human_corrected: Boolean(doc.humanCorrected)
        };
      })
    };
    const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/cases/restructure-from-ocr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const text = await response.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : null; } catch (error) {
    if(generation !== caseGeneration)return; parsed = null; }
    if (!response.ok) throw new Error(parsed && parsed.detail ? formatApiDetail(parsed.detail) : (text || "无错误详情"));
    if (!parsed) throw new Error("后端返回了空响应");
    assertCurrentGeneration(generation); assertCaseResponse(parsed,expectedId);
    result = parsed;
    painReport = null;
    careReport = null;
    documents.forEach(function(doc) {
      if (doc.humanCorrected) doc.originalOcrText = doc.text;
      doc.humanCorrected = false;
    });
    setPipelineState(6);
    const hasWarning = result.quality_flags.some(function(flag) { return flag.severity === "warning" || flag.severity === "error"; });
    setCaseStatus(hasWarning ? "修正已应用，仍需复核" : "修正已应用", hasWarning ? "review" : "ready");
    renderAll();
    renderPainReport();
    renderCareReport();
    const status = $("ocrCorrectionStatus");
    status.className = "ocr-note applied";
    status.textContent = "人工修正已用于重新结构化；第二问和第三问旧结果已清空，请重新运行。";
    showToast("已应用 " + correctedCount + " 页OCR修正并更新病例字段");
  } catch (error) {
    if(generation !== caseGeneration)return;
    setCaseStatus("OCR修正应用失败", "error");
    showToast("重新结构化失败：" + (error.message || "请检查后端"));
  } finally {
    if (generation === caseGeneration) await refreshWorkflow().catch(function(error) { showToast(error.message); });
    button.disabled = false;
    button.textContent = "应用修正并重新结构化";
  }
}
async function runPipeline() {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  const button = $("runButton");
  const uploadedPageCount = documents.filter(function(x) { return x.file; }).length;
  const isLongCase = uploadedPageCount > 8;
  button.disabled = true;
  setCaseStatus(isLongCase ? "长病例分块处理中" : "正在处理", "processing");
  button.textContent = isLongCase ? ("正在分块处理 " + uploadedPageCount + " 页…") : "正在处理…";
  if (isLongCase) showToast("检测到" + uploadedPageCount + "页长病例：系统将自动分块、合并并执行全局校验，请勿刷新页面");
  try {
    if (isDemoCase && !workflowCase) {
      assertCurrentGeneration(generation); result=demoCase();
      showToast('仅展示固定演示样例，未运行OCR或AI');
    } else {
      if (!backendUrl) throw new Error('未连接后端，未执行OCR或AI处理');
      const processed=await processWithBackend();
      assertCurrentGeneration(generation); assertCaseResponse(processed,expectedId);
      result=processed; isDemoCase=false;
    }
    painReport = null;
    careReport = null;
    assertCurrentGeneration(generation);
    setPipelineState(isDemoCase ? -1 : 6);
    const hasWarning = result.quality_flags.some(function(x) { return x.severity === "warning" || x.severity === "error"; });
    setCaseStatus(isDemoCase ? "固定演示样例（未执行AI）" : (hasWarning ? "需要复核" : "结构化完成"), hasWarning ? "review" : "ready");
    renderAll();
    showToast(isDemoCase ? "固定演示样例；未执行OCR或AI" : "真实OCR与结构化已完成；痛点和关怀请分别运行");
  } catch (error) {
    if(generation !== caseGeneration)return;
    setPipelineState(3, true);
    setCaseStatus("处理失败", "error");
    result.quality_flags = [{ code: "PROVIDER_ERROR", severity: "error", message: error.message || "服务调用失败", field_path: null, document_id: null }];
    renderAll();
    showToast("处理失败：" + (error.message || "请检查后端连接"));
  } finally {
    if (generation === caseGeneration) await refreshWorkflow().catch(function(error) { showToast(error.message); });
    button.disabled = false;
    button.innerHTML = '<span class="button-icon">✦</span>运行OCR与结构化';
  }
}
function loadDemo() {
  resetCaseState("DEMO-RESP-001");
  workflowCase = null;
  clearCaseQuality();
  clearDeliveryState();
  localStorage.removeItem("medicalClaimsCaseId");
  renderWorkflow();
  documents.forEach(function(x) { if (x.previewUrl) URL.revokeObjectURL(x.previewUrl); });
  documents = structuredClone(demoDocuments);
  result = demoCase();
  painReport = null;
  careReport = null;
  isDemoCase = true;
  $("caseId").value = result.case_id;
  selectedDocId = documents[0].id;
  setCaseStatus("固定演示样例（未执行AI）", "ready");
  setPipelineState(-1);
  renderAll();
  showToast("已载入固定演示样例；未运行OCR或AI，不作为比赛评测数据");
}
function openJson() {
  $("jsonOutput").textContent = JSON.stringify(result, null, 2);
  $("jsonDialog").showModal();
}
function downloadBlob(data, filename, type) {
  const blob = new Blob([data], { type: type || "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
function downloadJson() {
  downloadBlob(JSON.stringify(result, null, 2), (result.case_id || "claim-case") + ".json");
  showToast("当前案件JSON已导出");
}
function painSeverityLabel(value) {
  return ({ low: "低", medium: "中", high: "高", critical: "紧急" })[value] || value;
}
function painDimensionLabel(value) {
  return ({ clinical: "临床", cost: "费用", experience: "体验" })[value] || value;
}
function renderPainReport() {
  if(painReport && painReport.case_id!==$("caseId").value)painReport=null;
  const summary = painReport && painReport.summary;
  $("painTotal").textContent = summary ? summary.total : "—";
  $("painClinical").textContent = summary ? summary.clinical : "—";
  $("painCost").textContent = summary ? summary.cost : "—";
  $("painExperience").textContent = summary ? summary.experience : "—";
  $("painCaseLabel").textContent = painReport ? ("病例 " + painReport.case_id) : "等待分析";
  $("exportPainButton").disabled = !painReport;
  const warnings = painReport ? (painReport.analysis_warnings || []) : [];
  $("painWarnings").innerHTML = warnings.map(function(warning) {
    return '<div class="pain-warning">' + escapeHtml(uiReason(warning)) + '</div>';
  }).join("");
  const points = painReport ? (painReport.pain_points || []) : [];
  if (!painReport) {
    $("painPointList").innerHTML = '<div class="pain-empty"><strong>尚未分析</strong><span>先在案件工作台完成第一问，再点击“分析当前病例”。</span></div>';
    return;
  }
  if (!points.length) {
    $("painPointList").innerHTML = '<div class="pain-empty"><strong>未发现有充分证据的痛点</strong><span>这不等于不存在健康风险；当前结果仅基于已提交的理赔材料。</span></div>';
    return;
  }
  $("painPointList").innerHTML = points.map(function(point) {
    const evidence = (point.evidence || []).map(function(item) {
      return '<li><strong>' + escapeHtml(item.document_id) + (item.page ? " · 第" + item.page + "页" : "") + '</strong><span>' + escapeHtml(item.text) + '</span></li>';
    }).join("");
    return '<article class="pain-card severity-' + escapeHtml(point.severity) + '">' +
      '<div class="pain-card-head"><div><span class="pain-dimension">' + painDimensionLabel(point.dimension) + '</span><h3>' + escapeHtml(point.title) + '</h3></div><span class="severity-chip ' + escapeHtml(point.severity) + '">' + painSeverityLabel(point.severity) + ' · ' + point.severity_score + '</span></div>' +
      '<p>' + escapeHtml(point.description) + '</p>' +
      '<div class="pain-meta"><span>置信度 ' + (Number(point.confidence) * 100).toFixed(1) + '%</span><span>' + escapeHtml(point.detector && point.detector.method || "unknown") + '</span><span>' + (point.review_required ? "需要复核" : "自动提示") + '</span></div>' +
      '<div class="pain-action"><strong>建议动作</strong><span>' + escapeHtml(point.recommended_action) + '</span></div>' +
      '<details class="pain-evidence"><summary>查看原文证据（' + (point.evidence || []).length + '）</summary><ul>' + evidence + '</ul></details>' +
      '</article>';
  }).join("");
}
async function analyzeCurrentPain() {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  if (!backendUrl) {
    showToast("请先连接本地后端，再运行第二问");
    return;
  }
  const button = $("analyzePainButton");
  button.disabled = true;
  button.textContent = "正在分析…";
  try {
    requireWorkflowCase();
    if (!workflowCase.has_structured_result) throw new Error("请先完成并保存结构化结果");
    const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/cases/analyze-pain-points", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case: result, use_llm: true })
    });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (error) {
    if(generation !== caseGeneration)return; payload = null; }
    if (!response.ok) throw new Error(payload && payload.detail ? formatApiDetail(payload.detail) : (text || "无错误详情"));
    if (!payload) throw new Error("后端返回了空响应");
    assertCurrentGeneration(generation); assertCaseResponse(payload,expectedId);
    painReport = payload;
    careReport = null;
    renderPainReport();
    renderCareReport();
    showToast("第二问痛点分析完成");
  } catch (error) {
    if(generation !== caseGeneration)return;
    showToast("痛点分析失败：" + (error.message || "请检查后端"));
  } finally {
    if (generation === caseGeneration) await refreshWorkflow().catch(function(error) { showToast(error.message); });
    button.disabled = false;
    button.textContent = "分析当前病例";
  }
}
function exportPainReport() {
  if (!painReport) return;
  downloadBlob(JSON.stringify(painReport, null, 2), (painReport.case_id || "case") + ".pain-points.json");
  showToast("痛点识别JSON已导出");
}
function careStatusLabel(value) {
  return ({ passed: "通过", review: "待复核", blocked: "已拦截" })[value] || value;
}
function renderCareReport() {
  if(careReport && careReport.case_id!==$("caseId").value)careReport=null;
  const report = careReport;
  const guide = report && report.guide;
  $("careDisease").textContent = guide ? guide.disease_topic : "—";
  $("careCaseLabel").textContent = report ? ("病例 " + report.case_id) : "等待生成";
  $("careMethod").textContent = report ? (report.generation_method === "llm" ? "DeepSeek" : "安全降级") : "—";
  $("careCoverage").textContent = report ? (Number(report.evidence_coverage) * 100).toFixed(1) : "—";
  $("careReview").textContent = report ? (report.review_required ? "待复核" : "已通过") : "—";
  $("careKnowledgeStatus").textContent = report ? (report.knowledge_review_status === "reviewed" ? "知识卡已审核" : "知识卡待医学审核") : "知识卡待匹配";
  $("careEngineVersion").textContent = report ? "已生成" : "尚未生成";
  $("exportCareButton").disabled = !report;
  $("careWarnings").innerHTML = report ? (report.warnings || []).map(function(warning) {
    return '<div class="pain-warning">' + escapeHtml(warning) + '</div>';
  }).join("") : "";
  if (!guide) {
    $("careGuideContent").innerHTML = '<div class="pain-empty"><strong>尚未生成</strong><span>请先完成第一问和第二问，再生成个性化温度指引。</span></div>';
    $("careSafetyList").innerHTML = '<div class="pain-empty compact"><strong>等待检查</strong><span>生成后显示医学边界、知识审核和证据状态。</span></div>';
    return;
  }
  function sectionCard(section, kind) {
    const refs = (section.evidence_fields || []).map(function(path) { return '<span>' + escapeHtml(path) + '</span>'; }).join("");
    return '<article class="care-section ' + (kind || "") + '"><h3>' + escapeHtml(section.title) + '</h3><p>' + escapeHtml(section.content) + '</p>' + (refs ? '<details class="technical-details"><summary>查看病例依据</summary><div class="care-refs">' + refs + '</div></details>' : '<div class="care-refs review"><strong>需要复核</strong><span>本段没有病例字段引用</span></div>') + '</article>';
  }
  function actionList(title, items) {
    return '<article class="care-section"><h3>' + escapeHtml(title) + '</h3><ol class="care-action-list">' + (items || []).map(function(item) {
      return '<li><strong>' + escapeHtml(item.action) + '</strong><span>' + escapeHtml(item.timing) + ' · ' + escapeHtml(item.reason) + '</span></li>';
    }).join("") + '</ol></article>';
  }
  $("careGuideContent").innerHTML =
    sectionCard(guide.opening, "opening") +
    '<div class="care-grid">' + sectionCard(guide.diagnosis_explanation) + sectionCard(guide.treatment_review) + sectionCard(guide.cost_explanation) + sectionCard(guide.psychological_support) + '</div>' +
    '<div class="care-grid">' + actionList("康复行动清单", guide.recovery_plan) + actionList("下次就医建议", guide.follow_up_plan) + '</div>' +
    '<article class="care-section alert"><h3>需要及时关注的情况</h3><ul>' + (guide.red_flags || []).map(function(item) { return '<li>' + escapeHtml(item) + '</li>'; }).join("") + '</ul></article>' +
    '<article class="care-limitations"><strong>使用边界</strong><ul>' + (guide.limitations || []).map(function(item) { return '<li>' + escapeHtml(item) + '</li>'; }).join("") + '</ul></article>';
  $("careSafetyList").innerHTML = (report.safety_checks || []).map(function(check) {
    return '<article class="care-safety ' + escapeHtml(check.status) + '"><div><strong>' + "内容安全检查" + '</strong><span>' + escapeHtml(uiReason(check.message)) + '</span></div><em>' + careStatusLabel(check.status) + '</em></article>';
  }).join("");
}
async function generateCurrentCare() {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  if (!backendUrl) { showToast("请先连接本地后端，再生成第三问"); return; }
  if (!painReport) { showToast("请先到痛点洞察完成第二问，不能跳过关键输入"); return; }
  const button = $("generateCareButton");
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    requireWorkflowCase();
    const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/cases/generate-care-guide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case: result, pain_report: painReport, disease_topic: $("careTopic").value.trim() || null, use_llm: true })
    });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (error) {
    if(generation !== caseGeneration)return; payload = null; }
    if (!response.ok) throw new Error(payload && payload.detail ? formatApiDetail(payload.detail) : (text || "无错误详情"));
    if (!payload) throw new Error("后端返回了空响应");
    assertCurrentGeneration(generation); assertCaseResponse(payload,expectedId);
    careReport = payload;
    renderCareReport();
    showToast(payload.review_required ? "指引已生成，必须完成医学复核" : "第三问温度指引已生成");
  } catch (error) {
    if(generation !== caseGeneration)return;
    showToast("温度指引生成失败：" + (error.message || "请检查后端"));
  } finally {
    if (generation === caseGeneration) await refreshWorkflow().catch(function(error) { showToast(error.message); });
    button.disabled = false;
    button.textContent = "生成温度指引";
  }
}
function exportCareReport() {
  if (!careReport) return;
  downloadBlob(JSON.stringify(careReport, null, 2), (careReport.case_id || "case") + ".care-guide.json");
  showToast("温度指引JSON已导出");
}
function qualityDiseaseLabel(value) {
  return ({
    unavailable: "尚未确认",
    pneumonia: "肺炎",
    influenza: "流感",
    hypertension: "高血压",
    coronary_heart_disease: "冠心病",
    diabetes: "糖尿病",
    cholecystitis: "胆囊炎",
    fracture: "骨折",
    lung_nodule: "肺结节",
    appendicitis: "阑尾炎",
    cataract: "白内障",
    urinary_calculus: "泌尿系结石"
  })[value] || value;
}

function qualityV2Text(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback || "暂无足够公开证据";
  return String(value);
}

function qualityV2FieldValue(field) {
  if (!field || !["extracted", "derived"].includes(field.status)) return null;
  return field.normalized_value !== null && field.normalized_value !== undefined ? field.normalized_value : field.value;
}

function qualityV2DiagnosisNames() {
  return (result && result.diagnoses || []).map(function(item) { return qualityV2FieldValue(item.name); }).filter(Boolean);
}

function qualityV2LocationFromCase() {
  const patient = result && (result.patient_location || result.patient || result.demographics) || {};
  const city = patient.city || patient.current_city || result && result.patient_city;
  const province = patient.province || patient.current_province || result && result.patient_province;
  return {city:city || null, province:province || null};
}

function qualityV2DiseaseKey(name) {
  const text = String(name || "").trim();
  if (text.includes("肺炎")) return "pneumonia";
  if (text.includes("流感")) return "influenza";
  if (text.includes("高血压")) return "hypertension";
  if (text.includes("冠心病")) return "coronary_heart_disease";
  if (text.includes("糖尿病")) return "diabetes";
  if (text.includes("胆囊炎")) return "cholecystitis";
  if (text.includes("骨折")) return "fracture";
  if (text.includes("肺结节")) return "lung_nodule";
  if (text.includes("阑尾炎")) return "appendicitis";
  if (text.includes("白内障")) return "cataract";
  if (text.includes("泌尿系结石") || text.includes("尿路结石")) return "urinary_calculus";
  const normalized = text.replace(/[\s/\\]+/g, "-").replace(/[^\w\u3400-\u9fff-]/g, "").toLowerCase();
  return normalized ? "diagnosis:" + normalized : null;
}

function qualityV2DiseaseInfoFromCase() {
  const upstreamTopic = careReport && result && careReport.case_id === result.case_id && careReport.guide && careReport.guide.disease_topic;
  if (upstreamTopic) {
    const upstreamLabel = String(upstreamTopic).trim();
    const upstreamGroup = qualityV2DiseaseKey(upstreamLabel);
    if (upstreamGroup) return {group:upstreamGroup, label:upstreamLabel, source:"care_guide.disease_topic"};
  }
  const diagnoses = (result && result.diagnoses || []).filter(function(item) {
    const field = item && item.name;
    const confidence = Number(field && field.confidence || 0);
    return qualityV2FieldValue(field) && confidence >= 0.7 && !["suspected", "ruled_out"].includes(item.certainty) && item.diagnosis_type !== "history";
  }).sort(function(a, b) {
    const priority = {primary:3, discharge:2, admission:1, secondary:0, unknown:0};
    return (priority[b.diagnosis_type] || 0) - (priority[a.diagnosis_type] || 0);
  });
  if (!diagnoses.length) return null;
  const label = String(qualityV2FieldValue(diagnoses[0].name)).trim();
  const group = qualityV2DiseaseKey(label);
  return group ? {group:group, label:label, source:"structured_case_fallback"} : null;
}

function qualityV2DiseaseFromCase() {
  const info = qualityV2DiseaseInfoFromCase();
  return info && info.group;
}

function qualityV2ComplexityFromCase() {
  const points = painReport && painReport.case_id === result.case_id ? (painReport.pain_points || []) : [];
  if (points.some(function(item) { return ["critical", "high"].includes(item.severity); })) return "high";
  if (points.some(function(item) { return item.severity === "medium"; }) || qualityV2DiagnosisNames().length >= 3 || (result.procedures || []).length) return "medium";
  return result && result.case_id && (result.diagnoses || []).length ? "low" : "unknown";
}

function qualityV2ComplexityLabel(value) {
  return ({low:"常规",medium:"需要综合评估",high:"较复杂",unknown:"尚未评估"})[value] || "尚未评估";
}

function qualityV2StatusLabel(value) {
  return ({evaluated:"已评价",insufficient_evidence:"证据不足",not_applicable:"当前不适用",needs_review:"建议人工复核"})[value] || "尚未评价";
}

function qualityV2StatusTone(value) {
  return value === "evaluated" ? "ready" : value === "needs_review" ? "review" : "neutral";
}

function qualityV2EvidenceStatusLabel(value) {
  return ({
    verified:"已核验",
    partially_verified:"部分核验",
    pending_verification:"待核验",
    contradicted:"存在事实冲突",
    "已核验":"已核验",
    "部分核验":"部分核验",
    "公开资料有限":"待核验"
  })[value] || "待核验";
}

function qualityV2Context() {
  const mode = $("qualityV2ContextMode").value;
  if (mode === "demo") {
    const parts = $("qualityV2DemoPreset").value.split("|");
    return {
      mode:"demo", case_id:"DEMO-CONTEXT-" + parts[0], disease_group:parts[0], disease_label:qualityDiseaseLabel(parts[0]), province:parts[1], city:parts[2],
      nearby_major_cities:[], complexity:"unknown", comorbidities:[], preferred_hospital_types:[], needs_upgrade_referral:false
    };
  }
  const hasCurrentCase = Boolean(workflowCase && workflowCase.has_structured_result && result && result.case_id === workflowCase.case_id);
  const diseaseInfo = hasCurrentCase ? qualityV2DiseaseInfoFromCase() : null;
  const disease = diseaseInfo && diseaseInfo.group;
  const names = hasCurrentCase ? qualityV2DiagnosisNames() : [];
  const savedLocation = hasCurrentCase ? qualityV2LocationFromCase() : {city:null, province:null};
  const diseaseWord = diseaseInfo && diseaseInfo.label;
  return {
    mode:"current",
    case_id:workflowCase ? workflowCase.case_id : (result && result.case_id || ""),
    disease_group:disease,
    disease_label:diseaseInfo && diseaseInfo.label,
    province:savedLocation.province,
    city:savedLocation.city || qualityV2CurrentCity || null,
    nearby_major_cities:[],
    complexity:hasCurrentCase ? qualityV2ComplexityFromCase() : "unknown",
    comorbidities:names.filter(function(name) { return !diseaseWord || !String(name).includes(diseaseWord); }),
    preferred_hospital_types:[],
    needs_upgrade_referral:false
  };
}

function qualityV2CurrentProfile(hospitalId) {
  const discovered = qualityV2HospitalReport && qualityV2HospitalReport.candidate_profiles || [];
  const profile = discovered.find(function(item) { return item.hospital_id === hospitalId; });
  if (profile) return profile;
  const context = qualityV2Context();
  const disease = qualityV2Catalog && qualityV2Catalog.diseases.find(function(item) { return item.disease_group === context.disease_group; });
  return disease && disease.hospitals.find(function(item) { return item.hospital_id === hospitalId; });
}

function renderQualityV2Context() {
  const context = qualityV2Context();
  const demo = context.mode === "demo";
  $("qualityV2DemoPresetLabel").hidden = !demo;
  $("qualityV2CurrentCityLabel").hidden = demo;
  const savedLocation = demo ? {city:null} : qualityV2LocationFromCase();
  $("qualityV2CurrentCity").disabled = Boolean(savedLocation.city);
  if (savedLocation.city) $("qualityV2CurrentCity").value = savedLocation.city;
  $("qualityV2ContextBadge").textContent = demo ? "演示数据" : "当前病例";
  $("qualityV2ContextBadge").className = "care-badge" + (demo ? " quality-v2-demo-note" : "");
  $("qualityV2ContextNote").textContent = demo ? "演示上下文仅用于临时调用 V2 推荐规则，不写入当前案件或 SQLite；退出后恢复真实病例。" : "城市仅用于本次推荐情境，不修改原始病例事实。";
  $("qualityV2Disease").textContent = context.disease_group ? (context.disease_label || qualityDiseaseLabel(context.disease_group)) : "当前病种尚未确认";
  $("qualityV2Location").textContent = context.city || "尚未提供患者所在地";
  $("qualityV2Complexity").textContent = demo ? "演示未指定" : qualityV2ComplexityLabel(context.complexity);
  $("qualityV2Comorbidities").textContent = context.comorbidities.length ? context.comorbidities.join("、") : "未见已结构化合并症";
  $("qualityV2Strategy").textContent = qualityV2HospitalReport ? qualityV2Text(qualityV2HospitalReport.strategy, "等待计算") : "等待计算";
  const messages = [];
  if (!demo && !workflowCase) messages.push("请先在案件工作台创建或读取当前案件。");
  else if (!demo && !workflowCase.has_structured_result) messages.push("当前案件尚未完成结构化，无法建立真实推荐上下文。");
  else {
    if (!context.disease_group) messages.push("当前病例尚未可靠识别明确病种。");
    if (!context.city) messages.push("尚未提供患者所在地，当前推荐不计算完整地理优先级。请补充当前推荐地区。");
  }
  $("qualityV2Status").innerHTML = messages.map(function(message) { return '<div class="pain-warning">' + escapeHtml(message) + '</div>'; }).join("");
}

function qualityV2EvidenceHtml(profile, reason) {
  const sources = Object.fromEntries((profile && profile.sources || []).map(function(source) { return [source.source_id, source]; }));
  const sourceIds = reason && reason.source_ids || [];
  const fields = reason && reason.evidence_fields || [];
  const requestedRecordIds = reason && reason.evidence_record_ids || [];
  const records = (profile && profile.evidence_records || []).filter(function(record) {
    if (requestedRecordIds.length) return requestedRecordIds.includes(record.evidence_id);
    return sourceIds.includes(record.source_id) && (!fields.length || fields.some(function(field) {
      return record.evidence_field === field || record.evidence_field.split('.')[0] === field.split('.')[0];
    }));
  });
  const rawEvidenceStatus = reason && reason.evidence_status || (records.length && records.every(function(record) { return record.verified !== false; }) ? "verified" : "pending_verification");
  const evidenceStatus = qualityV2EvidenceStatusLabel(rawEvidenceStatus);
  const recordHtml = records.map(function(record) {
    const link = record.source_url ? '<a href="' + escapeHtml(record.source_url) + '" target="_blank" rel="noreferrer">链接</a>' : '暂未提供';
    const confidence = Number(record.evidence_confidence);
    const confidenceText = Number.isFinite(confidence) ? '（可信度 ' + Math.round(confidence * 100) + '%）' : '';
    return '<article class="quality-v2-evidence-record">' +
      '<p><strong>证据来源：</strong>' + escapeHtml(record.source_name || "公开资料") + '</p>' +
      '<p><strong>证据摘要：</strong>' + escapeHtml(record.evidence_text || record.supported_claim || "暂无摘要") + '</p>' +
      '<p><strong>查看原始页面：</strong>' + link + '</p>' +
      '<p><strong>核验状态：</strong>' + escapeHtml(record.verified === false ? "待核验" : evidenceStatus) + ' ' + escapeHtml(confidenceText) + '</p>' +
      '</article>';
  }).join('');
  const suggestedName = reason && reason.suggested_source_name;
  const suggestedUrl = reason && reason.suggested_source_url;
  const suggestedSummary = reason && reason.suggested_evidence_summary;
  const suggestedHtml = !records.length && (suggestedName || suggestedUrl || suggestedSummary) ?
    '<article class="quality-v2-evidence-record pending"><p><strong>建议核验来源：</strong>' + escapeHtml(suggestedName || "来源名称待确认") + '</p>' +
    (suggestedSummary ? '<p><strong>建议核验内容：</strong>' + escapeHtml(suggestedSummary) + '</p>' : '') +
    '<p><strong>候选来源链接：</strong>' + (suggestedUrl ? '<a href="' + escapeHtml(suggestedUrl) + '" target="_blank" rel="noreferrer">链接</a>' : '暂未提供') + '</p>' +
    '<p><strong>当前状态：</strong>待核验</p><p class="muted">候选来源尚未完成后台核验，不作为已核验证据。</p></article>' : '';
  const unboundSources = sourceIds.filter(function(sourceId) {
    return !records.some(function(record) { return record.source_id === sourceId; }) && sources[sourceId];
  }).map(function(sourceId) {
    const source = sources[sourceId];
    const link = source.url ? '<a href="' + escapeHtml(source.url) + '" target="_blank" rel="noreferrer">链接</a>' : '暂未提供';
    return '<article class="quality-v2-evidence-record pending"><p><strong>关联来源：</strong>' + escapeHtml(source.title) + '</p><p><strong>查看原始页面：</strong>' + link + '</p><p><strong>当前状态：</strong>' + escapeHtml(evidenceStatus) + '</p><p class="muted">该来源尚未形成当前理由的独立证据摘要。</p></article>';
  }).join('');
  const emptyHtml = !recordHtml && !suggestedHtml && !unboundSources ?
    '<article class="quality-v2-evidence-record pending"><p><strong>当前状态：</strong>' + escapeHtml(evidenceStatus) + '</p><p class="muted">公开证据暂未完成核验。</p></article>' : '';
  const technicalPayload = [];
  if (fields.length) technicalPayload.push("evidence_fields: " + fields.join(", "));
  if (sourceIds.length) technicalPayload.push("source_ids: " + sourceIds.join(", "));
  if (requestedRecordIds.length) technicalPayload.push("evidence_record_ids: " + requestedRecordIds.join(", "));
  const technicalHtml = technicalPayload.length ? '<details class="technical-details quality-v2-evidence-technical"><summary>技术详情</summary><pre>' + escapeHtml(technicalPayload.join("\n")) + '</pre></details>' : '';
  return '<details class="quality-v2-evidence"><summary>查看依据</summary><div class="quality-v2-evidence-list">' + recordHtml + suggestedHtml + unboundSources + emptyHtml + '</div>' + technicalHtml + '</details>';
}

function qualityV2DimensionScoresHtml(items) {
  if (!(items || []).length) return '';
  return '<div class="quality-v2-score-grid">' + items.map(function(item) {
    const confidence = Math.round(Number(item.evidence_confidence || 0) * 100);
    return '<article class="quality-v2-score-item"><div><strong>' + escapeHtml(qualityV2Text(item.label, "评分维度")) + '</strong><span>' + Number(item.score || 0).toFixed(1) + ' 分 · 权重 ' + Math.round(Number(item.weight || 0) * 100) + '%</span></div><p>' + escapeHtml(qualityV2Text(item.reasoning, "评分说明待补充")) + '</p><small>' + escapeHtml(qualityV2EvidenceStatusLabel(item.evidence_status)) + ' · 证据可信度 ' + confidence + '%</small></article>';
  }).join('') + '</div>';
}

function renderQualityV2Hospitals() {
  const report = qualityV2HospitalReport;
  if (!report) {
    $("qualityV2Hospitals").innerHTML = '<div class="pain-empty compact"><strong>等待生成</strong><span>读取当前病例并补充推荐地区后，可生成医院推荐。</span></div>';
    return;
  }
  if (!(report.recommendations || []).length) {
    const warning = (report.warnings || []).join("；") || "当前没有可展示候选；请确认当前病例病种，或稍后重试资源发现。";
    $("qualityV2Hospitals").innerHTML = '<div class="pain-empty compact"><strong>当前没有可展示的医院推荐</strong><span>' + escapeHtml(warning) + '</span></div>';
    return;
  }
  const coverageNotice = (report.warnings || []).length ? '<div class="pain-warning"><strong>当前证据覆盖说明</strong><span>' + escapeHtml(report.warnings.join("；")) + '</span></div>' : '';
  $("qualityV2Hospitals").innerHTML = coverageNotice + report.recommendations.map(function(item) {
    const profile = qualityV2CurrentProfile(item.hospital_id);
    const foundation = profile && profile.institution_foundation || {};
    const specialty = profile && profile.disease_specialty || {};
    const history = profile && profile.historical_evidence || {};
    const reasons = (item.reasons || []).map(function(reason) {
      return '<li><strong>' + escapeHtml(qualityV2Text(reason.title, "推荐依据")) + '</strong><span>' + escapeHtml(qualityV2Text(reason.explanation, "依据说明待补充")) + '</span>' + qualityV2EvidenceHtml(profile, reason) + '</li>';
    }).join('');
    return '<article class="quality-v2-hospital-card"><div class="quality-v2-card-head"><div><h3>' + escapeHtml(item.rank + ' · ' + item.hospital_name) + '</h3><p>' + escapeHtml([foundation.city, foundation.hospital_level, foundation.hospital_type].filter(Boolean).join(' · ') || '机构基础信息待补充') + '</p><span class="status-pill ' + (item.evidence_status === 'verified' ? 'ready' : item.evidence_status === 'contradicted' ? 'review' : 'neutral') + '">' + escapeHtml(qualityV2EvidenceStatusLabel(item.evidence_status)) + '</span></div><div class="quality-v2-score"><strong>' + Number(item.recommendation_score).toFixed(1) + '</strong><span>当前推荐分</span></div></div>' +
      '<div class="quality-v2-meta"><span>可信度 ' + (Number(item.recommendation_confidence) * 100).toFixed(0) + '%</span><span>' + escapeHtml(qualityV2Text(item.accessibility && item.accessibility.accessibility_level, "地理未知")) + '</span><span>相关科室 ' + escapeHtml((specialty.related_departments || []).join('、') || '待补充') + '</span>' +
      (history.historical_case_count ? '<span>历史病例 ' + history.historical_case_count + ' 例</span>' : '<span>暂无可关联历史病例</span>') + '</div>' +
      '<strong>固定五维评分</strong>' + qualityV2DimensionScoresHtml(item.dimension_scores) +
      '<strong>为什么适合当前患者</strong><ol class="quality-v2-reasons">' + reasons + '</ol>' +
      '<div class="quality-v2-limitations"><strong>当前限制</strong><br>' + escapeHtml((item.limitations || []).join('；') || '暂无额外限制说明') + '</div>' +
      '<div class="quality-v2-actions"><button class="btn secondary small" type="button" data-quality-profile="' + escapeHtml(item.hospital_id) + '">查看完整画像</button></div></article>';
  }).join('');
}

function qualityV2AssessmentHtml(title, assessment, detailHtml, extraClass) {
  const current = assessment || {status:"insufficient_evidence", reasoning:"暂无足够公开证据", limitations:[]};
  const score = current.status === "evaluated" && current.score !== null && current.score !== undefined ? '<p>能力证据评分：' + Number(current.score).toFixed(1) + ' · 置信度 ' + (Number(current.confidence || 0) * 100).toFixed(0) + '%</p>' : '';
  return '<article class="quality-v2-dimension ' + (extraClass || '') + '"><h3>' + escapeHtml(title) + '</h3><span class="status-pill ' + qualityV2StatusTone(current.status) + '">' + escapeHtml(qualityV2StatusLabel(current.status)) + '</span>' + (detailHtml || '') + score + '<p>' + escapeHtml(qualityV2Text(current.reasoning, "暂无足够公开证据")) + '</p>' + ((current.limitations || []).length ? '<p><strong>限制：</strong>' + escapeHtml(current.limitations.join('；')) + '</p>' : '') + '</article>';
}

function renderQualityV2HospitalProfile() {
  const recommendation = qualityV2HospitalReport && (qualityV2HospitalReport.recommendations || []).find(function(item) { return item.hospital_id === qualityV2SelectedHospitalId; });
  const profile = qualityV2CurrentProfile(qualityV2SelectedHospitalId);
  if (!profile || !recommendation) {
    $("qualityV2ProfileTitle").textContent = "选择医院查看能力画像";
    $("qualityV2HospitalProfile").innerHTML = '<div class="pain-empty compact"><strong>尚未选择医院</strong><span>推荐结果生成后，可查看六个维度及当前推荐情境。</span></div>';
    return;
  }
  const foundation = profile.institution_foundation;
  const specialty = profile.disease_specialty;
  const resources = profile.medical_resources;
  const regional = profile.regional_service;
  const feedback = profile.patient_service_feedback;
  const history = profile.historical_evidence;
  const accessibility = recommendation.accessibility || {};
  $("qualityV2ProfileTitle").textContent = foundation.hospital_name;
  $("qualityV2HospitalProfile").innerHTML =
    qualityV2AssessmentHtml("机构基础能力", foundation.assessment, '<p>等级：' + escapeHtml(qualityV2Text(foundation.hospital_level)) + '</p><p>类型：' + escapeHtml(qualityV2Text(foundation.hospital_type)) + '</p><p>教学属性：' + escapeHtml(foundation.teaching_hospital === true ? '是' : foundation.teaching_hospital === false ? '否' : '暂无足够公开证据') + '</p><p>区域角色：' + escapeHtml(qualityV2Text(foundation.institution_role)) + '</p>' + qualityV2EvidenceHtml(profile, foundation.assessment)) +
    qualityV2AssessmentHtml("病种专科能力", specialty.assessment, '<p>对应科室：' + escapeHtml((specialty.related_departments || []).join('、') || '暂无足够公开证据') + '</p><p>重点专科：' + escapeHtml((specialty.key_specialties || []).join('、') || '暂无足够公开证据') + '</p><p>能力说明：' + escapeHtml(qualityV2Text(specialty.specialty_capability_description)) + '</p><p>匹配等级：' + escapeHtml(qualityV2Text(specialty.specialty_match_level)) + '</p>' + qualityV2EvidenceHtml(profile, specialty.assessment)) +
    qualityV2AssessmentHtml("医疗资源能力", resources.assessment, '<p>关键专家：' + escapeHtml((resources.key_experts || []).join('、') || '暂无足够公开证据') + '</p><p>重症能力：' + escapeHtml(qualityV2Text(resources.critical_care_capability)) + '</p><p>多学科能力：' + escapeHtml(qualityV2Text(resources.multidisciplinary_capability)) + '</p><p>技术/设备：' + escapeHtml((resources.key_equipment_or_technology || []).join('、') || qualityV2Text(resources.surgery_or_intervention_capability)) + '</p>' + qualityV2EvidenceHtml(profile, resources.assessment)) +
    qualityV2AssessmentHtml("区域服务能力", regional.assessment, '<p>本地服务角色：' + escapeHtml(qualityV2Text(regional.local_service_role)) + '</p><p>区域转诊角色：' + escapeHtml(qualityV2Text(regional.regional_referral_role)) + '</p><p>复杂病例承接：' + escapeHtml(qualityV2Text(regional.complex_case_capability)) + '</p>' + qualityV2EvidenceHtml(profile, regional.assessment)) +
    qualityV2AssessmentHtml("患者服务与反馈", feedback.assessment, feedback.feedback_count ? '<p>可关联真实反馈：' + feedback.feedback_count + ' 例 · ' + escapeHtml(qualityV2Text(feedback.overall_feedback_level)) + '</p>' : '<p>暂无足够患者服务反馈数据</p>') +
    qualityV2AssessmentHtml("历史案例支撑", history.assessment, history.historical_case_count ? '<p>历史病例：' + history.historical_case_count + ' 例 · 同病种 ' + history.disease_specific_case_count + ' 例</p><p>' + escapeHtml(history.history_summary) + '</p><p>可观察字段：' + escapeHtml((history.supported_metrics || []).join('、') || '暂无') + '</p>' : '<p>暂无可关联历史理赔案例</p>') +
    qualityV2AssessmentHtml("当前推荐情境", accessibility.assessment, '<p>患者所在地：' + escapeHtml(qualityV2Text(accessibility.patient_city, "尚未提供")) + '</p><p>医院所在地：' + escapeHtml(qualityV2Text(accessibility.hospital_city)) + '</p><p>地理层级：' + escapeHtml(qualityV2Text(accessibility.accessibility_level, "未知")) + '</p><p>当前策略：' + escapeHtml(qualityV2Text(qualityV2HospitalReport.strategy, "等待后端计算")) + '</p>', 'context');
}

function renderQualityV2Clinicians() {
  if (!qualityV2ClinicianRecommendations.length) {
    $("qualityV2Clinicians").innerHTML = '<div class="pain-empty compact"><strong>当前缺少足够可追溯医生资料</strong><span>系统不会根据医院等级或医生职称生成假医生推荐；医院能力推荐仍可正常使用。</span></div>';
    return;
  }
  $("qualityV2Clinicians").innerHTML = qualityV2ClinicianRecommendations.map(function(item) {
    const profile = qualityV2ClinicianProfiles.find(function(row) { return row.clinician_id === item.clinician_id; }) || {};
    const basic = profile.basic_qualification || {};
    const disease = profile.disease_expertise || {};
    const treatment = profile.treatment_expertise || {};
    const history = profile.historical_evidence || {};
    const detail = profile.clinician_id ? '<details class="quality-v2-evidence"><summary>查看医生完整画像</summary><div class="quality-v2-profile-grid">' +
      qualityV2AssessmentHtml("基础资质", basic.assessment, '<p>医院：' + escapeHtml(qualityV2Text(basic.hospital_name)) + '</p><p>科室：' + escapeHtml(qualityV2Text(basic.department)) + '</p><p>职称：' + escapeHtml(qualityV2Text(basic.title)) + '</p>' + qualityV2EvidenceHtml(profile, basic.assessment)) +
      qualityV2AssessmentHtml("病种专长", disease.assessment, '<p>' + escapeHtml((disease.disease_expertise || []).join('、') || '暂无足够公开证据') + '</p>' + qualityV2EvidenceHtml(profile, disease.assessment)) +
      qualityV2AssessmentHtml("技术 / 治疗专长", treatment.assessment, '<p>' + escapeHtml((treatment.treatment_expertise || []).concat(treatment.surgery_expertise || []).join('、') || '暂无足够公开证据') + '</p>' + qualityV2EvidenceHtml(profile, treatment.assessment)) +
      qualityV2AssessmentHtml("所属平台能力", profile.platform_capability && profile.platform_capability.assessment, '<p>' + escapeHtml(qualityV2Text(profile.platform_capability && profile.platform_capability.department_strength)) + '</p>' + qualityV2EvidenceHtml(profile, profile.platform_capability && profile.platform_capability.assessment)) +
      qualityV2AssessmentHtml("当前病例匹配", profile.current_case_match && profile.current_case_match.assessment, '<p>' + escapeHtml(qualityV2Text(profile.current_case_match && profile.current_case_match.overall_case_match)) + '</p>' + qualityV2EvidenceHtml(profile, profile.current_case_match && profile.current_case_match.assessment)) +
      qualityV2AssessmentHtml("历史案例支撑", history.assessment, history.historical_case_count ? '<p>可关联历史病例 ' + history.historical_case_count + ' 例</p>' : '<p>暂无可关联历史案例</p>') + '</div></details>' : '';
    return '<article class="quality-v2-clinician-card"><div class="quality-v2-card-head"><div><h3>' + escapeHtml(item.clinician_name) + '</h3><p>' + escapeHtml([basic.hospital_name, basic.department, basic.title].filter(Boolean).join(' · ') || '医生基础资料待补充') + '</p><span class="status-pill ' + (item.evidence_status === 'verified' ? 'ready' : item.evidence_status === 'contradicted' ? 'review' : 'neutral') + '">' + escapeHtml(qualityV2EvidenceStatusLabel(item.evidence_status)) + '</span></div><div class="quality-v2-score"><strong>' + Number(item.recommendation_score).toFixed(1) + '</strong><span>当前匹配分</span></div></div>' +
      '<div class="quality-v2-meta"><span>病种专长 ' + escapeHtml((disease.disease_expertise || []).join('、') || '见推荐依据') + '</span><span>技术专长 ' + escapeHtml((treatment.treatment_expertise || []).concat(treatment.surgery_expertise || []).join('、') || '见推荐依据') + '</span><span>' + (history.historical_case_count ? '历史案例 ' + history.historical_case_count + ' 例' : '暂无可关联历史案例') + '</span></div>' +
      '<strong>固定四维评分</strong>' + qualityV2DimensionScoresHtml(item.dimension_scores) +
      '<strong>为什么适合当前患者</strong><ol class="quality-v2-reasons">' + (item.reasons || []).map(function(reason) { return '<li><strong>' + escapeHtml(reason.title) + '</strong><span>' + escapeHtml(reason.explanation) + '</span>' + qualityV2EvidenceHtml(profile, reason) + '</li>'; }).join('') + '</ol>' +
      '<div class="quality-v2-limitations"><strong>当前限制</strong><br>' + escapeHtml((item.limitations || []).join('；') || '暂无额外限制说明') + '</div>' + detail + '</article>';
  }).join('');
}

function qualityV2TreatmentSummary(evaluation) {
  const dimensions = [evaluation.treatment_diagnosis_match,evaluation.medication_rationality,evaluation.procedure_rationality,evaluation.treatment_completeness,evaluation.discharge_continuity,evaluation.risk_management];
  const reviews = dimensions.filter(function(item) { return item.status === "needs_review"; }).length;
  const missing = dimensions.filter(function(item) { return item.status === "insufficient_evidence"; }).length;
  const evaluated = dimensions.filter(function(item) { return item.status === "evaluated"; }).length;
  if (reviews || missing) return "目前记录可以帮助我们了解这次治疗的大致过程，已有 " + evaluated + " 项可以评价；另有 " + (reviews + missing) + " 项仍需结合完整病历和医生意见进一步确认。";
  if (evaluated) return "从现有记录看，本次治疗过程已有较完整的可评价信息。您可以结合下方依据，在复诊时与医生确认后续安排。";
  return "目前记录中的关键信息还不完整，建议结合完整病历和医生意见进一步确认。";
}

function renderQualityV2Treatment() {
  const evaluation = qualityV2TreatmentEvaluation;
  if (!evaluation) {
    const demo = $("qualityV2ContextMode").value === "demo";
    $("qualityV2Treatment").innerHTML = '<div class="pain-empty compact"><strong>' + (demo ? '演示上下文不生成治疗评价' : '等待评价') + '</strong><span>' + (demo ? '演示模式没有虚构治疗记录；切回当前病例后读取真实结构化材料。' : '当前病例完成结构化后，可查看六维治疗过程评价。') + '</span></div>';
    return;
  }
  const dimensions = [
    ["诊断与治疗匹配",evaluation.treatment_diagnosis_match], ["用药合理性",evaluation.medication_rationality],
    ["操作 / 手术合理性",evaluation.procedure_rationality], ["治疗完整性",evaluation.treatment_completeness],
    ["出院连续照护",evaluation.discharge_continuity], ["风险管理",evaluation.risk_management]
  ];
  const history = evaluation.historical_reference || {};
  $("qualityV2Treatment").innerHTML = '<article class="quality-v2-treatment-head"><h3>' + escapeHtml(qualityV2Text(evaluation.pattern && evaluation.pattern.readable_pattern_name, "当前病例治疗过程")) + '</h3><p>' + escapeHtml(qualityV2Text(evaluation.pattern && evaluation.pattern.treatment_description, "现有材料尚不足以形成完整治疗过程描述")) + '</p><div class="quality-v2-meta"><span>证据覆盖 ' + (Number(evaluation.evidence_coverage || 0) * 100).toFixed(0) + '%</span><span>评价置信度 ' + (Number(evaluation.overall_confidence || 0) * 100).toFixed(0) + '%</span></div></article>' +
    '<div class="quality-v2-treatment-grid">' + dimensions.map(function(row) {
      const item = row[1] || {status:"insufficient_evidence",reasoning:"证据不足",limitations:[]};
      const score = item.status === "evaluated" && item.score !== null && item.score !== undefined ? ' · ' + Number(item.score).toFixed(1) + '分' : '';
      return '<article class="quality-v2-treatment-item ' + qualityV2StatusTone(item.status) + '"><h3>' + escapeHtml(row[0]) + '</h3><span class="status-pill ' + qualityV2StatusTone(item.status) + '">' + escapeHtml(qualityV2StatusLabel(item.status)) + score + '</span><p>' + escapeHtml(qualityV2Text(item.reasoning, "当前证据不足")) + '</p>' + ((item.evidence_fields || []).length ? '<details class="quality-v2-evidence"><summary>查看依据</summary><p>依据来自当前病例的结构化医疗记录。</p><details class="technical-details quality-v2-evidence-technical"><summary>技术详情</summary><pre>' + escapeHtml("evidence_fields: " + item.evidence_fields.join(", ")) + '</pre></details></details>' : '') + ((item.limitations || []).length ? '<p><strong>当前限制：</strong>' + escapeHtml(item.limitations.join('；')) + '</p>' : '') + (item.status === "needs_review" ? '<p><strong>该项目建议医疗专业人员复核。</strong></p>' : '') + '</article>';
    }).join('') + '</div>' +
    '<article class="quality-v2-patient-summary"><strong>给患者的一句话总结</strong><p>' + escapeHtml(qualityV2TreatmentSummary(evaluation)) + '</p></article>' +
    '<article class="quality-v2-history"><h3>相似历史治疗模式参考</h3>' + (history.available ? '<p>相似历史病例：' + history.similar_historical_case_count + ' 例</p><p>平均费用：' + escapeHtml(qualityV2Text(history.average_fee, "暂无")) + ' · 平均住院日：' + escapeHtml(qualityV2Text(history.average_length_of_stay, "暂无")) + '</p><p>常见痛点：' + escapeHtml((history.common_pain_points || []).join('、') || '暂无') + '</p><p>治疗规范性观察：' + escapeHtml(qualityV2Text(history.treatment_standardization_observation)) + '</p><p>再入院观察：' + escapeHtml(qualityV2Text(history.readmission_observation)) + '</p>' : '<p>暂无足够相似历史案例，当前评价主要基于本次病例材料。</p>') + '<p><strong>以下为历史案例参考，不代表当前患者治疗效果预测。</strong></p></article>';
}

function renderQualityV2() {
  renderQualityV2Context();
  renderQualityV2Hospitals();
  renderQualityV2HospitalProfile();
  renderQualityV2Clinicians();
  renderQualityV2Treatment();
}

async function qualityV2Fetch(path, options) {
  if (!backendUrl) throw new Error("请先连接本地后端");
  const response = await fetch(backendUrl.replace(/\/$/, "") + path, options);
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (error) { payload = null; }
  if (!response.ok) throw new Error(formatApiDetail(payload && payload.detail || text || "V2 请求失败"));
  if (payload === null) throw new Error("后端返回了空响应");
  return payload;
}

function qualityV2ErrorMessage(error) {
  const message = String(error && error.message || "");
  if (/Failed to fetch|NetworkError|fetch failed/i.test(message)) return "无法连接本地后端，请检查服务是否已启动。";
  if (/Unexpected token|JSON/i.test(message)) return "后端返回内容无法读取，请检查服务版本。";
  return message || "V2 推荐请求失败，请检查本地后端。";
}

async function loadQualityV2() {
  const requestId = ++qualityV2RequestId;
  const generation = caseGeneration;
  const context = qualityV2Context();
  const button = $("refreshQualityV2Button");
  button.disabled = true; button.textContent = "正在生成…";
  qualityV2HospitalReport = null; qualityV2ClinicianRecommendations = []; qualityV2TreatmentEvaluation = null; qualityV2SelectedHospitalId = null;
  renderQualityV2();
  $("qualityV2Status").innerHTML = '<div class="pain-warning"><strong>正在解析病种所需专科</strong><span>正在根据当前病例与推荐地区准备医疗资源发现条件。</span></div>';
  try {
    if (context.mode === "current") {
      if (!workflowCase || !workflowCase.has_structured_result || result.case_id !== workflowCase.case_id) throw new Error("请先完成当前案件的病例结构化");
      if (!context.disease_group) throw new Error("当前病例尚未可靠识别明确病种");
    }
    if (!context.city) throw new Error("尚未提供患者所在地，请先补充当前推荐地区");
    const publicContext = Object.assign({}, context); delete publicContext.mode;
    $("qualityV2Status").innerHTML = '<div class="pain-warning"><strong>正在发现并核验医疗资源</strong><span>正在为当前病种发现并核验权威医疗资源；搜索结果只用于定位，能力证据取自权威原始页面。</span></div>';
    qualityV2HospitalReport = await qualityV2Fetch("/api/quality/v2/recommendations/hospitals", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({context:publicContext,case:context.mode === "current" ? result : null,top_k:5})});
    if (requestId !== qualityV2RequestId || generation !== caseGeneration) return;
    qualityV2ClinicianProfiles = qualityV2HospitalReport.candidate_clinician_profiles || [];
    $("qualityV2Status").innerHTML = '<div class="pain-warning"><strong>正在计算推荐并匹配医生</strong><span>医院能力证据已返回，正在按当前病例优先级整理医院与医生结果。</span></div>';
    const allowedHospitalIds = (qualityV2HospitalReport.recommendations || []).map(function(item) { return item.hospital_id; });
    qualityV2ClinicianRecommendations = await qualityV2Fetch("/api/quality/v2/recommendations/clinicians", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({disease_group:publicContext.disease_group,profiles:qualityV2ClinicianProfiles,allowed_hospital_ids:allowedHospitalIds,recommendation_intent:qualityV2HospitalReport.recommendation_intent || null,top_k:5})});
    if (context.mode === "current") {
      qualityV2TreatmentEvaluation = await qualityV2Fetch("/api/quality/v2/treatment-evaluation", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({case:result,disease_group:publicContext.disease_group,pain_report:painReport && painReport.case_id === result.case_id ? painReport : null,historical_reference:null})});
    }
    if (requestId !== qualityV2RequestId || generation !== caseGeneration) return;
    qualityV2SelectedHospitalId = qualityV2HospitalReport.recommendations.length ? qualityV2HospitalReport.recommendations[0].hospital_id : null;
    renderQualityV2();
    showToast(context.mode === "demo" ? "演示上下文已按真实 V2 规则计算" : "第四问 V2 推荐已生成");
  } catch (error) {
    if (requestId !== qualityV2RequestId || generation !== caseGeneration) return;
    const userMessage = qualityV2ErrorMessage(error);
    renderQualityV2();
    $("qualityV2Status").innerHTML += '<div class="pain-warning">' + escapeHtml(userMessage) + '</div>';
    showToast("第四问生成失败：" + userMessage);
  } finally {
    if (requestId === qualityV2RequestId) {
      button.disabled = false;
      button.textContent = qualityV2HospitalReport ? "刷新推荐与证据" : "生成当前推荐";
    }
  }
}

function qualityEntityLabel(value) {
  return ({institution: "医院", clinician: "医生", treatment_plan: "治疗方案"})[value] || value;
}

function renderDeliveryWorkflow() {
  const caseId = workflowCase && workflowCase.case_id;
  const readiness = deliveryReadiness && deliveryReadiness.case_id === caseId ? deliveryReadiness : null;
  $("deliveryCaseLabel").textContent = caseId ? '当前案件：' + caseId + ' · ' + uiLabel(workflowCase.status) : '尚未读取持久化案件';
  $("deliveryGateResult").innerHTML = readiness ? '<h3>发送安全检查 ' + uiStatus(readiness.decision) + '</h3><p>复核结果：' + escapeHtml(uiLabel(readiness.review_status)) + ' · 本次包含关怀内容' + (readiness.recommendation_included ? '与推荐结果' : '，暂不包含推荐结果') + '</p><h4>判断依据</h4><ul>' + readiness.reasons.map(function(reason){return '<li>' + escapeHtml(uiReason(reason)) + '</li>';}).join('') + '</ul>' + (readiness.recommendation_unavailable_reason ? '<p>' + escapeHtml(readiness.recommendation_unavailable_reason) + '</p>' : '') + (readiness.already_delivered ? '<p>这份内容已在模拟收件箱，无需重复发送。</p>' : '') + technicalDetails(readiness) : '<p>尚未检查。生成关怀指引后，可检查是否适合发送。</p>';
  $("sendDeliveryButton").disabled = !readiness || !readiness.can_send || readiness.already_delivered;
  $("deliveryReviewPanel").hidden = !readiness || readiness.decision !== 'REVIEW_REQUIRED';
  $("approveDeliveryButton").disabled = !readiness || readiness.already_delivered;
  $("rejectDeliveryButton").disabled = !readiness || readiness.already_delivered;
  $("deliveryReviewHistory").innerHTML = readiness ? readiness.reviews.map(function(review) {
    return '<p>' + escapeHtml(review.created_at + ' · ' + review.reviewer + ' · ' + uiLabel(review.target_type) + ' · ' + uiLabel(review.decision) + ' · ' + review.note) + '</p>';
  }).join('') : '';
  const deliveries = inboxDeliveries.filter(function(delivery) {return delivery.case_id === caseId;});
  if (!deliveries.some(function(delivery) {return delivery.delivery_id === activeDeliveryId;})) activeDeliveryId = deliveries.length ? deliveries[0].delivery_id : null;
  $("mockInboxList").innerHTML = deliveries.length ? deliveries.map(function(delivery) {
    return '<button class="btn secondary small inbox-delivery" data-delivery="' + escapeHtml(delivery.delivery_id) + '">' + escapeHtml(uiLabel(delivery.delivery_mode) + ' · ' + delivery.created_at) + '</button>';
  }).join(' ') : '<p>尚无模拟用户触达记录</p>';
  $("mockInboxList").querySelectorAll('.inbox-delivery').forEach(function(button) {button.addEventListener('click',function(){activeDeliveryId=button.dataset.delivery;renderDeliveryWorkflow();});});
  const selected = deliveries.find(function(delivery) {return delivery.delivery_id === activeDeliveryId;});
  $("submitDeliveryFeedbackButton").disabled = !selected;
  const option = $("deliveryFeedbackTarget").querySelector('option[value="recommendation"]');
  if (option) option.disabled = !selected || !selected.recommendation_included;
  if ((!selected || !selected.recommendation_included) && $("deliveryFeedbackTarget").value === 'recommendation') $("deliveryFeedbackTarget").value = 'overall_service';
  $("mockInboxContent").innerHTML = selected ? renderFrozenDelivery(selected) : '';
  $("deliveryFeedbackHistory").innerHTML = deliveryFeedback.filter(function(item){return item.case_id===caseId;}).map(function(item){return '<p>' +
    escapeHtml(item.created_at + ' · ' + uiLabel(item.target_type) + ' · 评分：' + item.rating + ' · ' + (item.helpful ? '有帮助' : '没有帮助') + ' · ' + uiLabel(item.category) + ' · ' + item.comment) + '</p>';}).join('');
}
function renderFrozenDelivery(delivery) {
  const content=delivery.content;
  const guide=content.care_guide.guide;
  function section(value){return '<article class="care-section"><h3>' + escapeHtml(value.title) + '</h3><p>' + escapeHtml(value.content) + '</p></article>';}
  function actions(title,items){return '<article class="care-section"><h3>' + title + '</h3><ol>' + items.map(function(item){return '<li>' + escapeHtml(item.action + ' · ' + item.timing + ' · ' + item.reason) + '</li>';}).join('') + '</ol></article>';}
  let html='<h3>模拟用户收件箱 <span class="status-pill neutral">演示环境</span></h3><p>' + escapeHtml(uiLabel(delivery.delivery_mode) + ' · ' + delivery.created_at) + '</p>' + technicalDetails({delivery_id:delivery.delivery_id,channel:delivery.channel,data_source:content.data_source,risk_gate:delivery.risk_gate_result}) + section(guide.opening) + section(guide.diagnosis_explanation) + section(guide.treatment_review) + section(guide.cost_explanation) +
    actions('康复计划',guide.recovery_plan) + section(guide.psychological_support) + actions('下次就医建议',guide.follow_up_plan) +
    '<p>需要关注：' + escapeHtml(guide.red_flags.join('；')) + '</p><p>使用边界：' + escapeHtml(guide.limitations.join('；')) + '</p>';
  if (content.recommendation_unavailable_reason) html+='<p>' + escapeHtml(content.recommendation_unavailable_reason) + '</p>';
  html+=(content.recommendation||[]).map(function(report){return '<h3>' + escapeHtml(qualityEntityLabel(report.entity_type)) + '</h3><p>' + escapeHtml(report.warnings.join('；')) + '</p>' + report.recommendations.map(function(item){
    return '<article class="care-section"><strong>' + escapeHtml(item.entity_id) + '</strong><p>病种：' + escapeHtml(qualityDiseaseLabel(report.disease_group)) + ' · 综合评分：' + item.composite_score.toFixed(1) +
      ' · 可信度：' + (item.confidence*100).toFixed(1) + '% · 样本数量：' + item.sample_size + '</p><ol>' + item.reasons.map(function(reason){return '<li>' + escapeHtml(reason.title + '：' + reason.explanation) +
      technicalDetails(reason.evidence_fields) + '</li>';}).join('') + '</ol></article>';
  }).join('');}).join('');
  return html;
}
async function loadDeliveryWorkflow() {
  try {
    requireWorkflowCase();
    const caseId=workflowCase.case_id;
    const base='/api/workflow/cases/'+encodeURIComponent(caseId);
    const rows=await Promise.all([workflowFetch(base),workflowFetch(base+'/deliveries'),workflowFetch(base+'/feedback'),workflowFetch(base+'/follow-ups')]);
    if (!workflowCase || workflowCase.case_id!==caseId) return;
    assertCaseResponse(rows[0],caseId); rows.slice(1).forEach(function(items){items.forEach(function(item){assertCaseResponse(item,caseId);});});
    workflowCase=rows[0]; inboxDeliveries=rows[1]; deliveryFeedback=rows[2]; deliveryFollowUps=rows[3];
    renderWorkflow(); renderDeliveryWorkflow();
  } catch(error){showToast('闭环读取失败：'+error.message);}
}
async function prepareCurrentDelivery() {
  const generation=caseGeneration;
  try {
    requireWorkflowCase();
    const caseId=workflowCase.case_id;
    const prepared=await workflowFetch('/api/workflow/cases/'+encodeURIComponent(caseId)+'/delivery/prepare',{method:'POST'});
    if (!workflowCase || workflowCase.case_id!==caseId) return;
    assertCaseResponse(prepared,caseId); prepared.reviews.forEach(function(review){assertCaseResponse(review,caseId);}); deliveryReadiness=prepared;
    await loadDeliveryWorkflow();
  } catch(error){if(generation!==caseGeneration)return;deliveryReadiness=null;renderDeliveryWorkflow();showToast('准备触达失败：'+error.message);}
}
async function sendCurrentDelivery() {
  const generation=caseGeneration;
  const button=$("sendDeliveryButton");button.disabled=true;
  try {
    requireWorkflowCase();
    const caseId=workflowCase.case_id;
    if (!deliveryReadiness || deliveryReadiness.case_id!==caseId) throw new Error('请先准备触达');
    const sent=await workflowFetch('/api/workflow/cases/'+encodeURIComponent(caseId)+'/delivery/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content_fingerprint:deliveryReadiness.content_fingerprint})});
    if (!workflowCase || workflowCase.case_id!==caseId) return;
    assertCaseResponse(sent,caseId); activeDeliveryId=sent.delivery_id;
    await prepareCurrentDelivery();
    showToast('已模拟触达 · '+uiLabel(sent.delivery_mode));
  } catch(error){if(generation!==caseGeneration)return;showToast('触达被拒绝：'+error.message); await prepareCurrentDelivery();}
  finally {renderDeliveryWorkflow();}
}
async function submitDeliveryReview(decision) {
  try {
    requireWorkflowCase();
    const caseId=workflowCase.case_id;
    if (!deliveryReadiness || deliveryReadiness.case_id!==caseId || deliveryReadiness.decision!=='REVIEW_REQUIRED') throw new Error('当前内容不接受人工批准');
    const reviewer=$("deliveryReviewer").value.trim();if(!reviewer)throw new Error('请填写实际审核者标识');
    await workflowFetch('/api/workflow/cases/'+encodeURIComponent(caseId)+'/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      reviewer:reviewer,decision:decision,target_type:$("deliveryReviewTarget").value,note:$("deliveryReviewNote").value,content_fingerprint:deliveryReadiness.content_fingerprint})});
    if (!workflowCase || workflowCase.case_id!==caseId) return;
    await prepareCurrentDelivery();showToast('已保存人工复核：'+uiLabel(decision));
  } catch(error){showToast('复核保存失败：'+error.message);}
}
async function submitCurrentDeliveryFeedback() {
  const button=$("submitDeliveryFeedbackButton");button.disabled=true;
  try {
    requireWorkflowCase();const caseId=workflowCase.case_id;
    if(!activeDeliveryId)throw new Error('请选择已触达记录');
    const result=await workflowFetch('/api/deliveries/'+encodeURIComponent(activeDeliveryId)+'/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      target_type:$("deliveryFeedbackTarget").value,rating:Number($("deliveryFeedbackRating").value),helpful:$("deliveryFeedbackHelpful").value==='true',category:$("deliveryFeedbackCategory").value,comment:$("deliveryFeedbackComment").value})});
    if (!workflowCase || workflowCase.case_id!==caseId) return;
    assertCaseResponse(result.feedback,caseId); if(result.follow_up)assertCaseResponse(result.follow_up,caseId);
    await loadDeliveryWorkflow();$("deliveryFeedbackStatus").textContent='已收到反馈'+(result.follow_up?' · 已创建后续处理事项':' · 无需后续处理');
    showToast('已收到反馈');
  } catch(error){showToast('反馈保存失败：'+error.message);}
  finally {renderDeliveryWorkflow();}
}
async function resolveDeliveryFollowUp(taskId) {
  try {
    const resolution=$("resolution-"+taskId).value.trim();if(!resolution)throw new Error('请填写实际处理结果');
    await workflowFetch('/api/follow-ups/'+encodeURIComponent(taskId)+'/resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resolution:resolution})});
    await loadDeliveryWorkflow();showToast('后续处理已完成');
  } catch(error){showToast('待办解决失败：'+error.message);}
}
function qualityAvailabilityLabel(value) {
  return ({available: "数据可用", insufficient_evidence: "数据不足，暂无法推荐", not_evaluated: "尚未评估"})[value] || value;
}

const recommendationReasonLabels = {
  case_incomplete: "当前病例尚未完成结构化、痛点分析和温度指引",
  disease_unconfirmed: "当前病种尚未确认",
  provider_unconfirmed: "当前医疗机构信息尚未确认",
  history_not_ready: "尚未构建可用于推荐的历史病例画像",
  historical_sample_insufficient: "历史病例样本不足",
  no_comparable_institution: "当前病种无可比较机构",
  clinician_identity_insufficient: "医生身份信息不足",
  clinician_sample_insufficient: "医生历史病例样本不足",
  treatment_sample_insufficient: "治疗方案样本不足",
  recommendation_evidence_insufficient: "推荐证据不足",
  non_real_data: "当前数据包含工程测试或来源未确认记录，不能用于真实推荐",
  not_evaluated: "该类推荐尚未评估"
};

function recommendationReasonKey(rawReason, entityType) {
  const reason = String(rawReason || "");
  if (/尚未完成前三问|尚未完成.*温度指引/.test(reason)) return "case_incomplete";
  if (/Disease group unavailable|无法可靠确定当前病种|病种.*未确认/i.test(reason)) return "disease_unconfirmed";
  if (/Current provider context unconfirmed|医院来源上下文|机构信息.*未确认/i.test(reason)) return "provider_unconfirmed";
  if (/Not Evaluated|尚未构建.*portfolio|未构建.*历史数据/i.test(reason)) return "history_not_ready";
  if (/synthetic_test|未知来源|工程验证|不得用于真实医疗推荐/i.test(reason)) return "non_real_data";
  if (/没有满足样本量、可计算维度和置信度门槛|推荐证据不足/.test(reason)) return "recommendation_evidence_insufficient";
  const historical = reason.match(/Insufficient historical data[：:]\s*(\d+)个同病种候选/i);
  if (historical) {
    const candidateCount = Number(historical[1]);
    if (entityType === "institution") return candidateCount === 0 ? "no_comparable_institution" : "historical_sample_insufficient";
    if (entityType === "clinician") return candidateCount === 0 ? "clinician_identity_insufficient" : "clinician_sample_insufficient";
    if (entityType === "treatment_plan") return "treatment_sample_insufficient";
  }
  if (/samples=|calculated dimensions=|minimum\s*\d+|confidence=/i.test(reason)) return "recommendation_evidence_insufficient";
  return null;
}

function recommendationReasons(entry) {
  const report = entry && entry.report ? entry.report : {};
  const entityType = report.entity_type;
  const keys = [];
  (report.warnings || []).forEach(function(rawReason) {
    const key = recommendationReasonKey(rawReason, entityType);
    if (key && !keys.includes(key)) keys.push(key);
  });
  if (!keys.length && entry && entry.status !== "available") {
    if (entry.status === "not_evaluated") keys.push("not_evaluated");
    else if (entityType === "clinician") keys.push("clinician_identity_insufficient");
    else if (entityType === "treatment_plan") keys.push("treatment_sample_insufficient");
    else keys.push("recommendation_evidence_insufficient");
  }
  return keys;
}

function recommendationReasonList(keys) {
  return '<ul class="recommendation-reasons">' + keys.map(function(key) {
    return '<li>' + escapeHtml(recommendationReasonLabels[key] || recommendationReasonLabels.recommendation_evidence_insufficient) + '</li>';
  }).join('') + '</ul>';
}

function renderRecommendationItem(report, item) {
  return '<article class="care-section"><h3>' + escapeHtml(item.entity_id) + '</h3><p>病种：' + escapeHtml(qualityDiseaseLabel(report.disease_group)) +
    ' · 综合评分：' + item.composite_score.toFixed(1) + ' · 可信度：' + (item.confidence * 100).toFixed(1) + '% · 样本数量：' + item.sample_size +
    '</p><strong>推荐理由</strong><ol>' + item.reasons.map(function(reason) {
      return '<li>' + escapeHtml(reason.title) + '<p>' + escapeHtml(reason.explanation) + '</p>' + technicalDetails(reason.evidence_fields) + '</li>';
    }).join('') + '</ol><small>' + escapeHtml(item.limitations.join('；')) + '</small></article>';
}

function renderRecommendationResults(query) {
  if (!query) return '<p>尚未评估：读取当前案件后，可查看医院、医生与治疗方案推荐。</p>';
  const entityTypes = ["institution", "clinician", "treatment_plan"];
  const entries = entityTypes.map(function(entityType) {
    return (query.reports || []).find(function(item) { return item.report && item.report.entity_type === entityType; }) || {
      status: "not_evaluated", report: {entity_type: entityType, warnings: [], recommendations: []}
    };
  });
  const reasonKeys = entries.map(recommendationReasons);
  const allUnavailable = entries.every(function(entry) { return entry.status !== "available"; });
  const sharedKeys = allUnavailable ? reasonKeys[0].filter(function(key) {
    return reasonKeys.slice(1).every(function(keys) { return keys.includes(key); });
  }) : [];
  let html = '';
  if (sharedKeys.length) {
    html += '<section class="recommendation-status-card"><div><span class="status-pill review">暂不可用</span><h3>当前暂无法生成推荐</h3></div><strong>原因</strong>' + recommendationReasonList(sharedKeys) +
      '<p>完成病例结构化和机构信息确认，并补充满足要求的同病种证据后，系统将继续生成医院、医生和治疗方案推荐。</p></section>';
  }
  html += '<div class="recommendation-module-grid">' + entries.map(function(entry, index) {
    const report = entry.report;
    const title = qualityEntityLabel(report.entity_type) + '推荐';
    if (entry.status !== "available") {
      const specificKeys = reasonKeys[index].filter(function(key) { return !sharedKeys.includes(key); });
      return '<article class="recommendation-module unavailable"><div class="recommendation-module-head"><h3>' + escapeHtml(title) + '</h3><span class="status-pill neutral">暂不可用</span></div>' +
        (specificKeys.length ? recommendationReasonList(specificKeys) : '<p class="muted">请先完成上方所列条件。</p>') + '</article>';
    }
    const recommendations = report.recommendations || [];
    return '<article class="recommendation-module available"><div class="recommendation-module-head"><h3>' + escapeHtml(title) + '</h3><span class="status-pill ready">可查看</span></div>' +
      (recommendations.length ? recommendations.map(function(item) { return renderRecommendationItem(report, item); }).join('') : recommendationReasonList(["recommendation_evidence_insufficient"])) + '</article>';
  }).join('') + '</div>';
  return html + '<details class="technical-details recommendation-debug"><summary>查看技术详情</summary><pre>' + escapeHtml(JSON.stringify(query.reports || [], null, 2)) + '</pre></details>';
}
function renderMultiSourceCandidatePools() {
  const selectedDisease = $("candidateDiseaseSelect").value;
  const pool = multiSourceCandidatePools && multiSourceCandidatePools.diseases.find(function(item) { return item.disease_group === selectedDisease; });
  if (!pool) {
    $("candidatePoolSummary").textContent = "连接本地后端后读取候选池";
    $("candidatePoolResults").innerHTML = '<p>尚未读取多源医院画像。</p>';
    return;
  }
  $("candidatePoolSummary").textContent = qualityDiseaseLabel(pool.disease_group) + "：涉及医院 " + pool.hospital_total +
    " 家 · 证据充足 " + pool.sufficient_count + " 家 · 部分充足 " + pool.partially_sufficient_count +
    " 家 · 证据不足 " + pool.insufficient_count + " 家 · 当前只形成候选池，不生成排名或最终推荐";
  $("candidatePoolResults").innerHTML = pool.candidates.length ? pool.candidates.map(function(candidate) {
    const profile = candidate.profile;
    const sourceMap = Object.fromEntries(profile.sources.map(function(source) { return [source.source_id, source]; }));
    const usableEvidence = profile.public_evidence.filter(function(item) { return item.usable_for_recommendation; });
    const evidenceHtml = usableEvidence.map(function(item) {
      const value = Array.isArray(item.value) ? item.value.join('、') : item.value;
      const links = item.source_ids.map(function(sourceId) {
        const source = sourceMap[sourceId];
        return source && source.url ? '<a href="' + escapeHtml(source.url) + '" target="_blank" rel="noreferrer">' + escapeHtml(source.title) + '</a>' : escapeHtml(sourceId);
      }).join('、');
      return '<li><strong>' + escapeHtml(item.label) + '</strong>：' + escapeHtml(value || '未披露') + '<br><small>来源：' + links + '</small></li>';
    }).join('');
    const history = profile.historical_claims;
    return '<article class="care-section"><div class="panel-header compact"><div><h3>' + escapeHtml(profile.standard_name) + '</h3><p>' +
      escapeHtml([profile.region, profile.hospital_type, profile.hospital_grade].filter(Boolean).join(' · ') || '基础属性未完整公开') +
      '</p></div><span class="status-pill ready">' + escapeHtml(uiLabel(candidate.evidence_sufficiency)) + '</span></div>' +
      '<p><strong>相关科室：</strong>' + escapeHtml(profile.related_departments.join('、') || '未披露') + '</p>' +
      '<p><strong>重点专科：</strong>' + escapeHtml(profile.national_key_specialties.concat(profile.provincial_key_specialties).join('、') || '未披露') + '</p>' +
      '<p><strong>可验证专科能力：</strong>' + escapeHtml(profile.specialist_capability || '未披露') + '</p>' +
      '<p><strong>历史病例参考：</strong>官方脱敏病例 ' + history.official_deidentified_case_count + ' 例 · ' + escapeHtml(history.note) + '</p>' +
      '<details><summary>查看证据</summary><ul>' + evidenceHtml + '</ul><p>查询日期：' + escapeHtml(profile.queried_at) + '</p></details></article>';
  }).join('') : '<p>当前没有医院达到候选池证据门槛。</p>';
}
function renderIntegratedQuality() {
  const activeId = workflowCase && workflowCase.case_id;
  const query = currentRecommendations && currentRecommendations.case_id === activeId ? currentRecommendations : null;
  $("qualityCurrentCase").textContent = activeId ? "案件编号：" + activeId + " · 病种：" + (query ? qualityDiseaseLabel(query.disease_group) : "尚未评估") : "尚未读取当前案件（演示不会进入真实推荐）";
  const snapshot = persistedPortfolio;
  const dataset = snapshot && snapshot.dataset;
  $("qualityDatasetStatus").textContent = snapshot ? qualityAvailabilityLabel(snapshot.status) +
    " · 已接纳病例数：" + dataset.accepted_cases + " · 未纳入病例数：" + dataset.excluded_cases +
    " · 数据来源：" + Object.keys(dataset.data_sources).map(uiLabel).join("、") +
    (dataset.contains_non_real_data ? " · 包含测试或未知来源数据，不能作为真实推荐/KR4证据" : " · 来源为调用方声明，仍需核验") : "尚未读取或生成品质画像";
  const disease = query && query.disease_group;
  const portfolio = snapshot && snapshot.portfolio;
  const profiles = portfolio && disease && disease !== "unavailable" ? portfolio.profiles.filter(function(p) { return p.disease_group === disease; }) : [];
  $("qualityProfileResults").innerHTML = ["institution", "clinician", "treatment_plan"].map(function(type) {
    const candidates = profiles.filter(function(p) { return p.entity_type === type; });
    let html = '<article class="profile-group"><h3>' + escapeHtml(qualityEntityLabel(type)) + '</h3>';
    if (!portfolio) return html + '<p>尚未生成对应品质画像。</p></article>';
    if (!candidates.length) return html + '<p>当前数据不足，暂无法生成可靠画像。</p></article>';
    return html + candidates.map(function(profile) {
      return '<article class="care-section"><h3>' + escapeHtml(profile.entity_id) + ' · ' + escapeHtml(qualityDiseaseLabel(profile.disease_group)) + '</h3>' +
        '<p>样本数量：' + profile.sample_size + ' · 综合情况：' + (profile.composite_score == null ? '暂不可用' : profile.composite_score.toFixed(1)) +
        ' · 可信度：' + (profile.composite_confidence * 100).toFixed(1) + '%</p>' +
        '<ul>' + profile.metrics.map(function(metric) {
          const score = metric.status === "calculated" && metric.score != null ? metric.score.toFixed(1) : '暂不可用（' + uiLabel(metric.status) + '）';
          return '<li><strong>' + escapeHtml(metric.label) + ': ' + escapeHtml(score) + '</strong> · 样本数量：' + metric.sample_size +
            technicalDetails(metric.calculation) + '<small>' + escapeHtml((metric.limitations || []).join('；')) + '</small></li>';
        }).join('') + '</ul><p>' + escapeHtml((profile.warnings || []).join('；')) + '</p></article>';
    }).join('') + '</article>';
  }).join('');
  $("qualityRecommendationResults").innerHTML = renderRecommendationResults(query);
  $("qualityKr4Status").textContent = qualityCoverageReport ? "推荐覆盖指标：" + uiLabel(qualityCoverageReport.status) + ' · 病种覆盖：' + qualityCoverageReport.disease_count + '/3 · ' +
    qualityCoverageReport.diseases.map(function(d) { return qualityDiseaseLabel(d.disease_group) + '：医院 ' + d.institution_count + '/5，合格医院 ' + d.qualified_institution_count + '/5，可用维度 ' + d.available_dimension_count + '/5'; }).join('；') : '推荐覆盖指标：尚未评估';
  renderMultiSourceCandidatePools();
  renderProviderCandidates();
}
function renderProviderCandidates() {
  const data = qualityContext;
  if (!data || !workflowCase || data.case_id !== workflowCase.case_id) { $("qualityProviderCandidates").textContent = '先读取当前案件'; return; }
  const confirmed = data.saved_context && data.saved_context.provider;
  const context = confirmed ? confirmed.context : data.candidates;
  if (!context) { $("qualityProviderCandidates").textContent = '没有可用来源候选；可导入已有审计确认的安全上下文。'; return; }
  const institution = context.institution;
  const hospitalReady = institution && !institution.review_required && institution.confidence >= .8 && institution.sources.length;
  let html = institution ? '<label><input type="checkbox" id="confirmQualityInstitution" ' + (confirmed && confirmed.institution_confirmed ? 'checked ' : '') + (!hospitalReady ? 'disabled ' : '') +
    '> 确认医院伪匿名来源 ' + escapeHtml(institution.institution_id) + ' · 可信度 ' + (institution.confidence * 100).toFixed(1) + '%</label>' : '<p>暂无可核验的医院来源</p>';
  html += '<p>仅确认已经核对的来源；存在医院冲突时不能在这里直接放行。报告/检查医生不进入经治医生画像。</p>';
  html += context.clinicians.map(function(clinician) {
    const eligible = clinician.ranking_eligible && !clinician.review_required && clinician.confidence >= .8 && ["attending_physician", "treating_physician", "surgeon"].includes(clinician.role);
    return '<label><input type="checkbox" class="confirm-quality-clinician" value="' + escapeHtml(clinician.clinician_id) + '" ' +
      (confirmed && confirmed.confirmed_clinician_ids.includes(clinician.clinician_id) ? 'checked ' : '') + (!eligible ? 'disabled ' : '') + '> ' +
      escapeHtml(clinician.clinician_id + ' · ' + uiLabel(clinician.role)) + (eligible ? '' : ' · 不进入医生画像') + '</label><br>';
  }).join('');
  $("qualityProviderCandidates").innerHTML = html;
}
async function loadIntegratedQuality() {
  if (!backendUrl) { showToast('请先连接本地后端'); return; }
  try {
    const activeId = workflowCase && workflowCase.case_id;
    const results = await Promise.all([
      workflowFetch('/api/quality/portfolio'), workflowFetch('/api/quality/coverage'),
      activeId ? workflowFetch('/api/workflow/cases/' + encodeURIComponent(activeId) + '/recommendations') : Promise.resolve(null),
      activeId ? workflowFetch('/api/workflow/cases/' + encodeURIComponent(activeId) + '/quality-context') : Promise.resolve(null),
      workflowFetch('/api/quality/multisource-candidate-pools')
    ]);
    if ((workflowCase && workflowCase.case_id) !== activeId) return;
    if(results[2])assertCaseResponse(results[2],activeId); if(results[3])assertCaseResponse(results[3],activeId);
    persistedPortfolio = results[0]; qualityCoverageReport = results[1]; currentRecommendations = results[2]; qualityContext = results[3]; multiSourceCandidatePools = results[4];
    renderIntegratedQuality();
  } catch (error) { showToast('画像/推荐读取失败：' + error.message); }
}
async function buildIntegratedPortfolio() {
  const button = $("buildPortfolioButton"); button.disabled = true;
  try {
    await workflowFetch('/api/quality/profiles/build', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({use_saved_records:true,minimum_sample_size:3})});
    await loadIntegratedQuality(); showToast('已使用SQLite实际记录构建画像；缺数据维度不会补分');
  } catch (error) { showToast('画像构建失败：' + error.message); }
  finally { button.disabled = false; }
}
async function saveCurrentProviderContext(payload) {
  try {
    requireWorkflowCase();
    const activeId = workflowCase.case_id;
    if (!payload) {
      if (!qualityContext || qualityContext.case_id !== activeId) throw new Error('请先读取当前来源上下文');
      const stored = qualityContext.saved_context;
      const context = stored ? stored.provider.context : qualityContext.candidates;
      if (!context) throw new Error('没有可确认来源，请导入已确认上下文');
      payload = Object.assign({}, stored || {}, {provider:{context:context,institution_confirmed:Boolean($("confirmQualityInstitution") && $("confirmQualityInstitution").checked),
        confirmed_clinician_ids:Array.from(document.querySelectorAll('.confirm-quality-clinician:checked')).map(function(input) {return input.value;})}});
    }
    const assembly = await workflowFetch('/api/workflow/cases/' + encodeURIComponent(activeId) + '/quality-context', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    await loadIntegratedQuality(); showToast(assembly.institution_id === 'unavailable' ? '医院未确认，未进入画像比较' : '来源已保存，请构建历史画像');
  } catch (error) { showToast('来源保存失败：' + error.message); }
}
function renderQualityAudit() {
  const audit = qualityAudit;
  const institution = audit && audit.institution_coverage;
  const clinician = audit && audit.clinician_coverage;
  const requirement = audit && audit.requirement_check;
  const scope = audit && audit.scope;
  $("qualityHospitals").textContent = institution ? institution.unique_institutions : "—";
  $("qualityConflicts").textContent = institution ? institution.review_required_cases : "—";
  $("qualityClinicians").textContent = clinician ? clinician.ranking_eligible_clinicians : "—";
  $("qualityCoverage").textContent = requirement ? (requirement.meets_three_diseases_by_five_institutions ? "候选达标" : "未达标") : "—";
  $("qualityInstitutionRate").textContent = institution ? ("案件识别率 " + (Number(institution.rate) * 100).toFixed(1) + "%") : "等待读取审计";
  $("qualityCoverageHint").textContent = requirement ? ((requirement.diseases_meeting_five_institutions || []).map(qualityDiseaseLabel).join("、") || "暂无病种达到5家") : "候选覆盖待确认";
  $("qualityAuditScope").textContent = scope ? (scope.case_count + "案 · " + scope.image_count + "页") : "尚未载入";
  $("exportQualityButton").disabled = !audit;

  const warnings = [];
  if (audit) {
    if (institution.review_required_cases) warnings.push(institution.review_required_cases + "个案件存在多机构候选，必须先完成主机构人工复核。候选数量不能直接当正式覆盖。 ");
    if (scope.empty_ocr_images) warnings.push(scope.empty_ocr_images + "张图片未识别到文字，相关案件需对照原图抽查。 ");
    warnings.push("本审计只描述候选覆盖，不代表下方已保存画像或KR4达标；正式指标以已组装病例、可计算维度和覆盖报告为准。 ");
  }
  $("qualityWarnings").innerHTML = warnings.map(function(warning) { return '<div class="pain-warning">' + escapeHtml(warning) + '</div>'; }).join("");

  const coverage = audit ? audit.disease_institution_coverage || {} : {};
  const rows = Object.keys(coverage).map(function(key) {
    return { key: key, cases: coverage[key].case_count || 0, institutions: coverage[key].institution_count || 0, meets: Boolean(coverage[key].meets_five_institutions) };
  }).sort(function(a, b) { return b.institutions - a.institutions || b.cases - a.cases; });
  $("qualityCoverageRows").innerHTML = rows.length ? rows.map(function(row) {
    return '<tr><td><strong>' + escapeHtml(qualityDiseaseLabel(row.key)) + '</strong></td><td>' + row.cases + '</td><td>' + row.institutions + '</td><td><span class="table-status ' + (row.meets ? "done" : "waiting") + '">' + (row.meets ? "候选达标" : "不足5家") + '</span></td></tr>';
  }).join("") : '<tr><td colspan="4">连接本地后端后载入审计</td></tr>';

  const dimensions = [
    { name: "费用效率", state: audit ? "partial" : "unavailable", tag: audit ? "待结构化" : "未载入", note: "需同病种总费用/住院日，并进行严重度风险调整。" },
    { name: "用药合理性", state: audit ? "partial" : "unavailable", tag: audit ? "待第二问" : "未载入", note: "只使用有原文证据的用药复核结果，不能把未报错当满分。" },
    { name: "治疗记录规范性", state: audit ? "partial" : "unavailable", tag: "代理指标", note: "可先评估记录完整度；临床规范性仍需指南规则或专家金标准。" },
    { name: "30日再入院控制", state: "unavailable", tag: "不可用", note: "案件号不是稳定患者标识，现阶段无法跨就诊关联。" },
    { name: "患者反馈", state: "unavailable", tag: "不可用", note: "病例图片不含反馈，禁止由大模型生成分数。" },
    { name: "数据质量", state: audit ? "ready" : "unavailable", tag: audit ? "已审计" : "未载入", note: audit ? ((scope.unique_image_contents || scope.image_count) + "个唯一页面内容，OCR空页" + (scope.empty_ocr_images || 0) + "张。") : "等待机构覆盖审计。" }
  ];
  $("qualityDimensionList").innerHTML = dimensions.map(function(item) {
    return '<article class="quality-dimension ' + item.state + '"><div><strong>' + escapeHtml(item.name) + '</strong><span>' + escapeHtml(item.note) + '</span></div><small>' + escapeHtml(item.tag) + '</small></article>';
  }).join("");
}
async function loadQualityAudit(silent) {
  const generation = caseGeneration;
  const expectedId = $("caseId").value;
  if (!backendUrl) {
    if (!silent) showToast("请先连接本地后端，再载入第四问真实审计");
    renderQualityAudit();
    return;
  }
  const button = $("loadQualityButton");
  button.disabled = true;
  button.textContent = "正在载入…";
  try {
    const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/quality/audit-summary");
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch (error) {
    if(generation !== caseGeneration)return; payload = null; }
    if (!response.ok) throw new Error(payload && payload.detail ? formatApiDetail(payload.detail) : (text || "无错误详情"));
    if (!payload) throw new Error("后端返回了空响应");
    assertCurrentGeneration(generation);
    qualityAudit = payload;
    renderQualityAudit();
    if (!silent) showToast("第四问真实覆盖审计已载入");
  } catch (error) {
    if(generation !== caseGeneration)return;
    if (!silent) showToast("审计载入失败：" + (error.message || "请检查后端"));
  } finally {
    button.disabled = false;
    button.textContent = "载入真实审计";
  }
}
function exportQualityAudit() {
  if (!qualityAudit) return;
  downloadBlob(JSON.stringify(qualityAudit, null, 2), "provider-coverage-report.json");
  showToast("第四问安全审计JSON已导出");
}
function switchView(name) {
  document.querySelectorAll(".view-tab").forEach(function(tab) { tab.classList.toggle("active", tab.dataset.view === name); });
  document.querySelectorAll("[data-view-panel]").forEach(function(panel) { panel.classList.toggle("active", panel.dataset.viewPanel === name); });
  const showCaseActions = name === "case";
  $("jsonButton").style.display = showCaseActions ? "" : "none";
  $("exportButton").style.display = showCaseActions ? "" : "none";
  const url = new URL(window.location.href);
  url.searchParams.set("view", name);
  history.replaceState(null, "", url);
  if (name === "pain") renderPainReport();
  if (name === "care") renderCareReport();
  if (name === 'delivery') {renderDeliveryWorkflow();if(backendUrl&&workflowCase)loadDeliveryWorkflow();}
  if (name === "quality") {
    renderQualityV2();
  }
}
function batchStatusLabel(value) {
  return ({waiting:"等待处理",processing:"处理中",completed:"已完成",review:"需要复核",failed:"失败"})[value] || value;
}
function batchStatusClass(value) {
  return ({completed:"done",review:"review",failed:"failed",processing:"processing",waiting:"waiting"})[value] || "waiting";
}
function batchCompletedCount() {
  return batchCases.filter(function(item){return item.status === "completed";}).length;
}
function batchReviewCount() {
  return batchCases.filter(function(item){return item.status === "review";}).length;
}
function batchFailedCount() {
  return batchCases.filter(function(item){return item.status === "failed";}).length;
}
function batchProcessedCount() {
  return batchCases.filter(function(item){return ["completed","review","failed"].includes(item.status);}).length;
}
function batchProcessingCount() {
  return batchCases.filter(function(item){return item.status === "processing";}).length;
}
function batchWaitingCount() {
  return batchCases.filter(function(item){return item.status === "waiting";}).length;
}
function defaultBatchName() {
  const now = new Date();
  function pad(value){return String(value).padStart(2,"0");}
  return "批次-" + now.getFullYear() + pad(now.getMonth()+1) + pad(now.getDate()) + "-" + pad(now.getHours()) + pad(now.getMinutes());
}
function renderBatchScan() {
  if (!batchScan.scanned_at) {
    $("batchScanSummary").textContent = "尚未选择目录";
    $("batchScanIssues").innerHTML = "";
    return;
  }
  $("batchScanSummary").textContent = "可处理病例 " + batchCases.length + " 个 · 图片 " + batchScan.total_images + " 张 · 不支持文件 " + batchScan.unsupported_files.length + " 个 · 无可处理图片目录 " + batchScan.empty_directories.length + " 个";
  const sections = [];
  if (batchScan.unsupported_files.length) sections.push('<details><summary>查看不支持的文件（' + batchScan.unsupported_files.length + '）</summary><div>' + batchScan.unsupported_files.map(escapeHtml).join("<br>") + '</div></details>');
  if (batchScan.empty_directories.length) sections.push('<details><summary>查看无可处理图片的目录（' + batchScan.empty_directories.length + '）</summary><div>' + batchScan.empty_directories.map(escapeHtml).join("<br>") + '</div></details>');
  sections.push('<span>浏览器不会返回完全空的文件夹；这里的“无可处理图片目录”指仅含不支持文件的病例目录。</span>');
  $("batchScanIssues").innerHTML = sections.join("");
}
function renderBatch() {
  const total = batchCases.length;
  const completed = batchCompletedCount();
  const review = batchReviewCount();
  const failed = batchFailedCount();
  const processed = batchProcessedCount();
  const processing = batchProcessingCount();
  const waiting = batchWaitingCount();
  $("batchTotal").textContent = total;
  $("batchCompleted").textContent = completed;
  $("batchReview").textContent = review;
  $("batchFailed").textContent = failed;
  $("batchProcessing").textContent = processing;
  $("batchWaiting").textContent = waiting;
  $("batchProgressBar").style.width = (total ? (processed / total * 100) : 0).toFixed(1) + "%";
  if (!total) {
    $("batchTitle").textContent = "等待选择病例目录";
    $("batchProgressText").textContent = "尚未开始";
  } else if (batchRunning) {
    $("batchTitle").textContent = "正在运行真实批量工作流";
    $("batchProgressText").textContent = "已处理 " + processed + " / " + total;
  } else if (processed === total) {
    $("batchTitle").textContent = "本批次处理结束";
    $("batchProgressText").textContent = "完成 " + completed + " · 复核 " + review + " · 失败 " + failed;
  } else {
    $("batchTitle").textContent = "已读取 " + total + " 个病例目录";
    $("batchProgressText").textContent = "等待开始";
  }
  if (!total) {
    $("batchRows").innerHTML = '<tr><td colspan="7">请选择包含病例图片的目录。</td></tr>';
  } else {
    $("batchRows").innerHTML = batchCases.map(function(item,index) {
      const canView = Boolean(item.saved_result || item.structured_result || item.pain_report || item.care_guide || item.error);
      const reasonText = item.error ? '<span class="batch-error">' + escapeHtml(item.error) + '</span>' : ((item.review_reasons || []).length ? '<span class="batch-review-reason">' + item.review_reasons.map(escapeHtml).join("；") + '</span>' : "—");
      const actions = [];
      if (canView) actions.push('<button class="btn secondary small" data-batch-result="' + index + '">查看结果</button>');
      if (item.case_id) actions.push('<button class="btn secondary small" data-batch-open="' + index + '">在案例工作台中打开</button>');
      if (item.status === "failed") actions.push('<button class="btn primary small" data-batch-retry="' + index + '">重试本病例</button>');
      return '<tr><td><strong>' + escapeHtml(item.source_name) + '</strong></td>' +
        '<td>' + item.file_names.length + ' 张</td>' +
        '<td>' + escapeHtml(item.case_id || "待创建") + '</td>' +
        '<td><span class="table-status ' + batchStatusClass(item.status) + '">' + batchStatusLabel(item.status) + '</span></td>' +
        '<td>' + escapeHtml(item.stage) + '</td>' +
        '<td>' + reasonText + '</td>' +
        '<td><div class="batch-result-actions">' + (actions.join("") || "—") + '</div></td></tr>';
    }).join("");
  }
  const hasWaiting = batchCases.some(function(item){return item.status === "waiting";});
  const batchConfirmed = $("batchConsent").checked && $("batchDeidentified").checked;
  $("runBatchButton").disabled = batchRunning || !backendUrl || !hasWaiting || !batchConfirmed || !$("batchName").value.trim();
  $("runBatchButton").textContent = batchRunning ? "正在处理…" : "开始批量处理";
  $("folderInput").disabled = batchRunning;
  $("exportBatchButton").disabled = batchRunning || processed === 0;
  renderBatchScan();
}
function isBatchImage(file) {
  return Boolean(file) && ((file.type || "").startsWith("image/") || /\.(jpe?g|png|bmp|webp)$/i.test(file.name || ""));
}
function loadBatchFolder(fileList) {
  if (batchRunning) return;
  const selected = Array.from(fileList || []);
  const paths = selected.map(function(file){return String(file.webkitRelativePath || file.name || "病例").split(/[\\/]/).filter(Boolean);});
  const containsCaseSubfolders = paths.some(function(parts){return parts.length >= 3;});
  const groups = new Map();
  selected.forEach(function(file,index) {
    const parts = paths[index];
    const sourceName = containsCaseSubfolders ? (parts.length >= 3 ? parts[1] : ((parts[0] || "所选目录") + "（根目录）")) : (parts[0] || "所选病例");
    if (!groups.has(sourceName)) groups.set(sourceName, []);
    groups.get(sourceName).push(file);
  });
  const unsupported = [];
  const emptyDirectories = [];
  const validGroups = [];
  groups.forEach(function(files,sourceName) {
    const images = files.filter(isBatchImage);
    files.filter(function(file){return !isBatchImage(file);}).forEach(function(file){unsupported.push(file.webkitRelativePath || file.name);});
    if (images.length) validGroups.push([sourceName,images]);
    else emptyDirectories.push(sourceName);
  });
  batchCases = validGroups.map(function(entry,index) {
    const sourceName = entry[0], files = entry[1];
    return {
      batch_item_id: "batch-item-" + String(index + 1).padStart(3,"0"),
      source_name: sourceName,
      files: files,
      file_names: files.map(function(file){return file.name;}),
      status: "waiting",
      stage: "等待处理",
      case_id: null,
      workflow_case: null,
      structured_result: null,
      pain_report: null,
      care_guide: null,
      saved_result: null,
      review_reasons: [],
      error: null,
      started_at: null,
      finished_at: null
    };
  });
  batchScan = {
    root_name: paths.length && paths[0].length ? paths[0][0] : null,
    total_files: selected.length,
    total_images: batchCases.reduce(function(total,item){return total + item.files.length;},0),
    unsupported_files: unsupported,
    empty_directories: emptyDirectories,
    scanned_at: new Date().toISOString()
  };
  if (!$("batchName").value.trim()) $("batchName").value = defaultBatchName();
  renderBatch();
  showToast(batchCases.length ? ("扫描完成：" + batchCases.length + " 个病例、" + batchScan.total_images + " 张图片") : "未找到可处理的 JPG、PNG、BMP 或 WebP 图片");
}
async function batchRequest(path, options) {
  if (!backendUrl) throw new Error("请先连接本地后端");
  const response = await fetch(backendUrl.replace(/\/$/, "") + path, options);
  let text = "", payload = null;
  if (typeof response.text === "function") {
    text = await response.text();
    try { payload = text ? JSON.parse(text) : null; } catch (error) { payload = null; }
  } else if (typeof response.json === "function") {
    payload = await response.json();
  }
  if (!response.ok) throw new Error(payload && payload.detail ? formatApiDetail(payload.detail) : (text || "后端请求失败"));
  if (payload == null) throw new Error("后端返回了空响应或非 JSON 内容");
  return payload;
}
function hasReviewField(value) {
  if (!value || typeof value !== "object") return false;
  if (value.status === "review_required" || value.status === "conflict") return true;
  return Object.keys(value).some(function(key){return hasReviewField(value[key]);});
}
function batchReviewReasons(item) {
  const reasons = [];
  const structured = item.structured_result || {};
  if ((structured.quality_flags || []).some(function(flag){return flag.severity === "warning" || flag.severity === "error";})) reasons.push("结构化质量标记需要核对");
  if (hasReviewField(structured)) reasons.push("结构化字段需要核对");
  if ((item.pain_report && item.pain_report.pain_points || []).some(function(point){return point.review_required;})) reasons.push("痛点结论需要核对");
  if ((item.pain_report && item.pain_report.analysis_warnings || []).length) reasons.push("痛点分析存在降级或警告");
  if (item.care_guide && item.care_guide.review_required) reasons.push("温度指引需要核对");
  if ((item.care_guide && item.care_guide.safety_checks || []).some(function(check){return check.status === "review" || check.status === "blocked";})) reasons.push("温度指引安全检查需要核对");
  return Array.from(new Set(reasons));
}
async function processBatchCase(item,index) {
  item.status = "processing";
  item.error = null;
  item.started_at = item.started_at || new Date().toISOString();
  item.finished_at = null;
  item.review_reasons = [];
  renderBatch();
  if (!item.case_id) {
    item.stage = "创建案件";
    renderBatch();
    const created = await batchRequest("/api/workflow/cases", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({claim_event_id:("batch:" + $("batchName").value.trim() + ":" + Date.now() + ":" + (index + 1)).slice(0,200), data_source:"user_upload", consent_status:"authorized", deidentification_status:"confirmed"})
    });
    if (!created.case_id) throw new Error("创建案件后未返回案件编号");
    item.case_id = created.case_id;
    item.workflow_case = created;
  }
  if (!item.structured_result) {
    item.stage = "OCR/结构化";
    renderBatch();
    const form = new FormData();
    form.append("case_id", item.case_id);
    item.files.forEach(function(file){form.append("files", file, file.name);});
    const detailed = await batchRequest("/api/cases/process-detailed", {method:"POST",body:form});
    if (!detailed.case || detailed.case.case_id !== item.case_id) throw new Error("结构化结果案件编号不一致");
    item.structured_result = detailed.case;
  }
  if (!item.pain_report) {
    item.stage = "痛点分析";
    renderBatch();
    item.pain_report = await batchRequest("/api/cases/analyze-pain-points", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({case:item.structured_result,use_llm:true})
    });
    if (item.pain_report.case_id !== item.case_id) throw new Error("痛点结果案件编号不一致");
  }
  if (!item.care_guide) {
    item.stage = "温度指引";
    renderBatch();
    item.care_guide = await batchRequest("/api/cases/generate-care-guide", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({case:item.structured_result,pain_report:item.pain_report,disease_topic:null,use_llm:true})
    });
    if (item.care_guide.case_id !== item.case_id) throw new Error("温度指引案件编号不一致");
  }
  item.stage = "持久化重读";
  renderBatch();
  item.saved_result = await batchRequest("/api/workflow/cases/" + encodeURIComponent(item.case_id) + "/result");
  if (item.saved_result.case_id !== item.case_id) throw new Error("持久化结果案件编号不一致");
  item.workflow_case = await batchRequest("/api/workflow/cases/" + encodeURIComponent(item.case_id));
  if (item.workflow_case.case_id !== item.case_id) throw new Error("案件状态编号不一致");
  item.review_reasons = batchReviewReasons(item);
  item.status = item.review_reasons.length ? "review" : "completed";
  item.stage = "持久化重读";
  item.finished_at = new Date().toISOString();
  renderBatch();
}
function syncBatchSavedResult(item) {
  const saved = item.saved_result || {};
  item.structured_result = item.structured_result || saved.structured_result || null;
  item.pain_report = item.pain_report || saved.pain_result || null;
  item.care_guide = item.care_guide || saved.care_result || null;
}
async function markBatchFailure(item,error) {
  item.status = "failed";
  item.error = error.message || "未知错误";
  item.finished_at = new Date().toISOString();
  if (item.case_id) {
    try { item.saved_result = await batchRequest("/api/workflow/cases/" + encodeURIComponent(item.case_id) + "/result"); syncBatchSavedResult(item); } catch (ignored) {}
    try { item.workflow_case = await batchRequest("/api/workflow/cases/" + encodeURIComponent(item.case_id)); } catch (ignored) {}
  }
  renderBatch();
}
function validateBatchStart() {
  if (!backendUrl) {showToast("请先连接本地后端");return false;}
  if (!$("batchName").value.trim()) {showToast("请填写批次名称");return false;}
  if (!$("batchConsent").checked) {showToast("请先确认本批次材料均已获得使用授权");return false;}
  if (!$("batchDeidentified").checked) {showToast("请先确认本批次材料均已完成脱敏处理");return false;}
  if (!batchCases.length) {showToast("请先选择包含多个病例子文件夹的总目录");return false;}
  return true;
}
async function runBatch() {
  if (batchRunning) return;
  if (!validateBatchStart()) return;
  const targets = batchCases.map(function(item,index){return {item:item,index:index};}).filter(function(entry){return entry.item.status === "waiting";});
  if (!targets.length) {showToast("当前批次没有等待处理的病例；失败病例请单独重试");return;}
  batchRunning = true;
  renderBatch();
  for (const target of targets) {
    try {
      await processBatchCase(target.item,target.index);
    } catch (error) {
      await markBatchFailure(target.item,error);
    }
  }
  batchRunning = false;
  renderBatch();
  showToast("批量处理结束：完成 " + batchCompletedCount() + "，待复核 " + batchReviewCount() + "，失败 " + batchFailedCount());
}
async function retryBatchCase(index) {
  if (batchRunning || !validateBatchStart()) return;
  const item = batchCases[index];
  if (!item || item.status !== "failed") return;
  const originalCaseId = item.case_id;
  batchRunning = true;
  renderBatch();
  try {
    await processBatchCase(item,index);
    if (originalCaseId && item.case_id !== originalCaseId) throw new Error("重试产生了新的案件编号，已拒绝结果");
    showToast("病例 " + item.source_name + " 重试完成");
  } catch (error) {
    await markBatchFailure(item,error);
    showToast("病例 " + item.source_name + " 重试失败：" + item.error);
  } finally {
    batchRunning = false;
    renderBatch();
  }
}
function batchCaseExport(item) {
  return {
    batch_item_id:item.batch_item_id, source_name:item.source_name, image_count:item.file_names.length, file_names:item.file_names,
    case_id:item.case_id, status:item.status, stage:item.stage, started_at:item.started_at,
    finished_at:item.finished_at, review_reasons:item.review_reasons, error:item.error,
    workflow_case:item.workflow_case, result:item.saved_result || {
      case_id:item.case_id, structured_result:item.structured_result,
      pain_result:item.pain_report, care_result:item.care_guide
    }
  };
}
function batchExportPayload() {
  return {
    export_type:"real_batch_workflow_results", generated_at:new Date().toISOString(),
    batch:{name:$("batchName").value.trim(),root_name:batchScan.root_name,consent_status:$("batchConsent").checked?"authorized":"unknown",deidentification_status:$("batchDeidentified").checked?"confirmed":"unknown",scan:batchScan},
    total:batchCases.length, completed:batchCompletedCount(), review_required:batchReviewCount(), failed:batchFailedCount(),
    cases:batchCases.filter(function(item){return item.status !== "waiting" && item.status !== "processing";}).map(batchCaseExport)
  };
}
function exportBatch() {
  const payload = batchExportPayload();
  if (!payload.cases.length) {showToast("尚无真实批量处理结果可导出");return;}
  downloadBlob(JSON.stringify(payload,null,2),"batch-workflow-results.json");
  showToast("已导出 " + payload.cases.length + " 个真实处理结果");
}
function openBatchResult(index) {
  const item = batchCases[index];
  if (!item) return;
  activeBatchResultIndex = index;
  $("batchResultTitle").textContent = item.source_name + (item.case_id ? " · " + item.case_id : "");
  $("batchResultOutput").textContent = JSON.stringify(batchCaseExport(item),null,2);
  $("batchResultDialog").showModal();
}
function downloadBatchCase() {
  const item = batchCases[activeBatchResultIndex];
  if (!item) return;
  downloadBlob(JSON.stringify(batchCaseExport(item),null,2),(item.case_id || item.batch_item_id) + ".batch-result.json");
}
async function openBatchInWorkbench(index) {
  const item = batchCases[index];
  if (!item || !item.case_id) {showToast("该病例尚未创建案件");return;}
  switchView("case");
  await restoreWorkflowCase(item.case_id);
}
async function runEvaluation() {
  if (!predictionData || !goldData) {showToast("请先导入预测结果和专家金标准");return;}
  const evaluatedPrediction=predictionData, evaluatedGold=goldData;
  ["evalPrecision","evalRecall","evalF1","evalMissing"].forEach(function(id){$(id).textContent='—';});
  try {
    const evaluated=await workflowFetch('/api/evaluation/structured',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prediction:predictionData,gold:goldData})});
    if(predictionData!==evaluatedPrediction || goldData!==evaluatedGold)return;
    if(evaluated.evaluation_status==='NOT_EVALUABLE')throw new Error('Gold无可评测字段，不能展示比赛指标');
    $("evalPrecision").textContent=(evaluated.micro_precision*100).toFixed(1);
    $("evalRecall").textContent=(evaluated.micro_recall*100).toFixed(1);
    $("evalF1").textContent=(evaluated.micro_f1*100).toFixed(1);
    $("evalMissing").textContent=evaluated.missing_rate==null?'—':(evaluated.missing_rate*100).toFixed(1);
    $("evaluationDetails").textContent=JSON.stringify(evaluated,null,2);
    showToast('评测完成：Python '+evaluated.evaluator_version+'；数据来源为导入的预测与gold，不证明正式数据授权或达标');
  } catch(error){$("evaluationDetails").textContent='评测未完成：'+error.message;showToast(error.message);}
}
async function readJsonFile(file, target) {
  try {
    const data = JSON.parse(await file.text());
    if (target === "prediction") {
      predictionData = data;
      $("predictionName").textContent = file.name;
    } else {
      goldData = data;
      $("goldName").textContent = file.name;
    }
    ["evalPrecision","evalRecall","evalF1","evalMissing"].forEach(function(id){$(id).textContent="—";});
    $("evaluationDetails").textContent="新输入尚未评测";
    showToast("已导入 " + file.name);
  } catch (error) {
    showToast("JSON格式错误，请检查文件");
  }
}
function updateProviderUi(connected, detail) {
  const dot = document.querySelector("#providerButton .provider-dot");
  dot.className = "provider-dot " + (connected ? "" : "demo");
  $("providerLabel").textContent = connected ? "本地服务已连接" : "演示模式";
  $("connectionStatus").innerHTML = '<span class="provider-dot ' + (connected ? "" : "demo") + '"></span><div><strong>' + (connected ? "后端连接正常" : "当前使用演示模式") + '</strong><small>' + escapeHtml(detail || (connected ? "RapidOCR与DeepSeek配置由后端管理" : "仍可运行内置案例和手工OCR文本基线")) + '</small></div>';
}
async function checkBackend() {
  const candidate = $("backendUrl").value.trim().replace(/\/$/, "");
  if (!candidate) {
    updateProviderUi(false, "请先填写本地后端地址");
    return false;
  }
  $("checkBackendButton").disabled = true;
  try {
    const response = await fetch(candidate + "/api/health");
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    if (data.examinations_schema !== true || data.cost_semantics_schema !== true || data.medication_instruction_schema !== true || data.ocr_review_workflow !== true) {
      updateProviderUi(false, "后端版本过旧：请停止旧进程并重启服务");
      return false;
    }
    updateProviderUi(true, "OCR：" + (data.ocr || "ready") + " · LLM：" + (data.llm || "ready") + " · 版本：" + (data.revision || "unknown"));
    return true;
  } catch (error) {
    updateProviderUi(false, "连接失败：" + error.message);
    return false;
  } finally {
    $("checkBackendButton").disabled = false;
  }
}
function saveBackend() {
  backendUrl = $("backendUrl").value.trim().replace(/\/$/, "");
  if (backendUrl) localStorage.setItem("medicalClaimsBackend", backendUrl);
  else localStorage.removeItem("medicalClaimsBackend");
  $("providerDialog").close();
  updateProviderUi(Boolean(backendUrl), backendUrl ? "已保存地址，运行前请检测服务状态" : "");
  renderBatch();
  showToast(backendUrl ? "本地后端地址已保存" : "已切换为演示模式");
  const rememberedCase = localStorage.getItem("medicalClaimsCaseId");
  if (backendUrl && rememberedCase) restoreWorkflowCase(rememberedCase);
}
function clearBackend() {
  backendUrl = "";
  $("backendUrl").value = "";
  localStorage.removeItem("medicalClaimsBackend");
  updateProviderUi(false);
  renderBatch();
  showToast("已清除后端配置");
}

$("fileInput").addEventListener("change", function(e) { addFiles(e.target.files); });
$("dropZone").addEventListener("click", function() { $("fileInput").click(); });
$("dropZone").addEventListener("keydown", function(e) { if (e.key === "Enter" || e.key === " ") $("fileInput").click(); });
["dragenter", "dragover"].forEach(function(name) { $("dropZone").addEventListener(name, function(e) { e.preventDefault(); $("dropZone").classList.add("dragging"); }); });
["dragleave", "drop"].forEach(function(name) { $("dropZone").addEventListener(name, function(e) { e.preventDefault(); $("dropZone").classList.remove("dragging"); }); });
$("dropZone").addEventListener("drop", function(e) { addFiles(e.dataTransfer.files); });
$("saveOcrButton").addEventListener("click", function() {
  const doc = documents.find(function(x) { return x.id === selectedDocId; });
  if (doc) {
    const revisedText = $("ocrText").value.trim();
    doc.text = revisedText;
    doc.humanCorrected = revisedText !== (doc.originalOcrText == null ? "" : doc.originalOcrText.trim());
  }
  painReport = null;
  careReport = null;
  setCaseStatus("OCR修正待应用", "review");
  renderDocuments();
  renderPainReport();
  renderCareReport();
  showToast(doc && doc.humanCorrected ? "本页修正已保存；还需点击重新结构化" : "本页文本与原OCR一致");
});
$("applyOcrCorrectionsButton").addEventListener("click", restructureFromCorrectedOcr);
$("runButton").addEventListener("click", runPipeline);
$("demoButton").addEventListener("click", loadDemo);
$("jsonButton").addEventListener("click", openJson);
$("exportButton").addEventListener("click", downloadJson);
$("downloadJsonButton").addEventListener("click", downloadJson);
$("closeDialog").addEventListener("click", function() { $("jsonDialog").close(); });
$("copyJsonButton").addEventListener("click", async function() { await navigator.clipboard.writeText(JSON.stringify(result, null, 2)); showToast("JSON已复制"); });
$("caseId").addEventListener("change", function() { resetCaseState($("caseId").value.trim()); });
$("createWorkflowButton").addEventListener("click", createWorkflowCase);
$("reloadWorkflowButton").addEventListener("click", function() { restoreWorkflowCase($("caseId").value.trim()); });
document.querySelectorAll(".tab").forEach(function(tab) { tab.addEventListener("click", function() { document.querySelectorAll(".tab").forEach(function(x) { x.classList.remove("active"); }); tab.classList.add("active"); currentTab = tab.dataset.tab; renderResults(); }); });
document.querySelectorAll(".view-tab").forEach(function(tab) { tab.addEventListener("click", function() { switchView(tab.dataset.view); }); });
$("folderInput").addEventListener("change", function(e) { loadBatchFolder(e.target.files); });
$("runBatchButton").addEventListener("click", runBatch);
$("exportBatchButton").addEventListener("click", exportBatch);
$("batchConsent").addEventListener("change", renderBatch);
$("batchDeidentified").addEventListener("change", renderBatch);
$("batchName").addEventListener("input", renderBatch);
$("batchRows").addEventListener("click", function(event) {
  const resultButton = event.target.closest ? event.target.closest("[data-batch-result]") : null;
  const openButton = event.target.closest ? event.target.closest("[data-batch-open]") : null;
  const retryButton = event.target.closest ? event.target.closest("[data-batch-retry]") : null;
  if (resultButton) openBatchResult(Number(resultButton.dataset.batchResult));
  else if (openButton) openBatchInWorkbench(Number(openButton.dataset.batchOpen));
  else if (retryButton) retryBatchCase(Number(retryButton.dataset.batchRetry));
});
$("closeBatchResultDialog").addEventListener("click", function() { $("batchResultDialog").close(); });
$("downloadBatchCaseButton").addEventListener("click", downloadBatchCase);
$("predictionInput").addEventListener("change", function(e) { if (e.target.files[0]) readJsonFile(e.target.files[0], "prediction"); });
$("goldInput").addEventListener("change", function(e) { if (e.target.files[0]) readJsonFile(e.target.files[0], "gold"); });
$("evaluateButton").addEventListener("click", runEvaluation);
$("analyzePainButton").addEventListener("click", analyzeCurrentPain);
$("exportPainButton").addEventListener("click", exportPainReport);
$("generateCareButton").addEventListener("click", generateCurrentCare);
$("exportCareButton").addEventListener("click", exportCareReport);
$("refreshQualityV2Button").addEventListener("click", loadQualityV2);
$("qualityV2ContextMode").addEventListener("change", function() {
  qualityV2RequestId += 1; qualityV2HospitalReport = null; qualityV2ClinicianRecommendations = [];
  qualityV2TreatmentEvaluation = null; qualityV2SelectedHospitalId = null; renderQualityV2();
});
$("qualityV2DemoPreset").addEventListener("change", function() {
  qualityV2RequestId += 1; qualityV2HospitalReport = null; qualityV2ClinicianRecommendations = [];
  qualityV2TreatmentEvaluation = null; qualityV2SelectedHospitalId = null; renderQualityV2();
});
$("qualityV2CurrentCity").addEventListener("input", function() {
  qualityV2CurrentCity = $("qualityV2CurrentCity").value.trim();
  qualityV2RequestId += 1; qualityV2HospitalReport = null; qualityV2ClinicianRecommendations = [];
  qualityV2TreatmentEvaluation = null; qualityV2SelectedHospitalId = null; renderQualityV2();
});
$("qualityV2Hospitals").addEventListener("click", function(event) {
  const button = event.target.closest ? event.target.closest("[data-quality-profile]") : null;
  if (!button) return;
  qualityV2SelectedHospitalId = button.dataset.qualityProfile;
  renderQualityV2HospitalProfile();
  $("qualityV2HospitalProfile").scrollIntoView({behavior:"smooth",block:"start"});
});
$("loadQualityButton").addEventListener("click", function() { loadQualityAudit(false); });
$("exportQualityButton").addEventListener("click", exportQualityAudit);
$("reloadDeliveryButton").addEventListener('click',loadDeliveryWorkflow);
$("prepareDeliveryButton").addEventListener('click',prepareCurrentDelivery);
$("sendDeliveryButton").addEventListener('click',sendCurrentDelivery);
$("approveDeliveryButton").addEventListener('click',function(){submitDeliveryReview('APPROVED');});
$("rejectDeliveryButton").addEventListener('click',function(){submitDeliveryReview('REJECTED');});
$("submitDeliveryFeedbackButton").addEventListener('click',submitCurrentDeliveryFeedback);
$("refreshProfilesButton").addEventListener("click", loadIntegratedQuality);
$("buildPortfolioButton").addEventListener("click", buildIntegratedPortfolio);
$("candidateDiseaseSelect").addEventListener("change", renderMultiSourceCandidatePools);
$("saveProviderContextButton").addEventListener("click", function() { saveCurrentProviderContext(); });
$("providerContextInput").addEventListener("change", async function(event) {
  const file = event.target.files[0]; if (!file) return;
  try { await saveCurrentProviderContext(JSON.parse(await file.text())); } catch (error) { showToast('上下文JSON读取失败：' + error.message); }
  event.target.value = '';
});
$("historicalProfilesInput").addEventListener("change", async function(event) {
  const file = event.target.files[0]; if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const response = await workflowFetch('/api/quality/records/import', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Array.isArray(payload) ? {records:payload} : payload)});
    await loadIntegratedQuality(); showToast('已保存历史数据，当前可用病例 ' + response.dataset.accepted_cases + '；请构建画像');
  } catch (error) { showToast('历史数据导入失败：' + error.message); }
  event.target.value = '';
});
$("providerButton").addEventListener("click", function() { $("backendUrl").value = backendUrl; updateProviderUi(Boolean(backendUrl)); $("providerDialog").showModal(); });
$("closeProviderDialog").addEventListener("click", function() { $("providerDialog").close(); });
$("checkBackendButton").addEventListener("click", checkBackend);
$("saveBackendButton").addEventListener("click", saveBackend);
$("clearBackendButton").addEventListener("click", clearBackend);

$("backendUrl").value = backendUrl;
$("batchName").value = defaultBatchName();
updateProviderUi(Boolean(backendUrl), backendUrl ? "已保存地址，运行前请检测服务状态" : "");
setPipelineState(-1);
renderBatch();
renderAll();
renderPainReport();
renderCareReport();
renderQualityAudit();
renderIntegratedQuality();
renderQualityV2();
renderDeliveryWorkflow();
renderWorkflow();
const savedWorkflowId = localStorage.getItem("medicalClaimsCaseId");
if (backendUrl && savedWorkflowId) restoreWorkflowCase(savedWorkflowId);
const initialView = new URLSearchParams(window.location.search).get("view");
if (["case", "pain", "care", "quality", "delivery", "batch", "evaluation"].includes(initialView)) switchView(initialView);
