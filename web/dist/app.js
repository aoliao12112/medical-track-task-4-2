const makeField = function(value, confidence, source, status, normalized) {
  return {
    value: value,
    normalized_value: normalized == null ? null : normalized,
    confidence: confidence,
    status: status || "extracted",
    evidence: source ? [{ document_id: source.doc, page: source.page, text: source.text, bbox: source.bbox || null }] : [],
    warnings: []
  };
};

const demoDocuments = [
  { id: "discharge-1", name: "出院记录 · 第1页", type: "出院小结", page: 1, text: "入院时间：2025-12-24 14:03；出院时间：2025-12-30 15:00；实际住院天数：6天。入院诊断：呼吸道感染。", ocrConfidence: .98 },
  { id: "discharge-2", name: "出院记录 · 第2页", type: "出院小结", page: 2, text: "出院诊断：呼吸道感染、肺炎、血液化学异常所见、胃肠功能紊乱。出院带药：酪酸梭菌活菌散1袋，口服，每日2次。", ocrConfidence: .97 },
  { id: "cost-1", name: "住院费用清单 · 第1页", type: "费用清单", page: 1, text: "住院天数6天。护理费、材料费、治疗费、检查费。", ocrConfidence: .95 },
  { id: "cost-2", name: "住院费用清单 · 第2页", type: "费用清单", page: 2, text: "药品费及项目明细。合计金额：4749.98元。", ocrConfidence: .96 },
  { id: "settlement-1", name: "医保结算单", type: "医保结算", page: 1, text: "费用总额4749.98元，医保综合支付2044.08元，个人现金支付2705.90元。", ocrConfidence: .99 }
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
      { medication_id: "med-1", name: makeField("酪酸梭菌活菌散", .96, medSrc), dose: makeField("1袋", .91, medSrc), frequency: makeField("每日2次", .96, medSrc), route: makeField("口服", .95, medSrc), duration: makeField(null, null, null, "not_present_in_document"), usage_condition: makeField(null, null, null, "not_present_in_document") }
    ],
    costs: {
      currency: "CNY",
      total_amount: makeField(4749.98, .99, costSrc),
      insurance_payment: makeField(2044.08, .99, costSrc),
      self_payment: makeField(2705.90, .99, costSrc),
      self_pay_ratio: makeField(.5697, .98, costSrc),
      breakdown: [
        { category: makeField("床位与护理", .92, { doc: "cost-1", page: 1, text: "床位费、护理费" }), amount: makeField(780, .86, { doc: "cost-1", page: 1, text: "床位与护理相关费用" }) },
        { category: makeField("检查与检验", .93, { doc: "cost-1", page: 1, text: "检查费、检验费" }), amount: makeField(1746, .83, { doc: "cost-1", page: 1, text: "检查与检验项目" }) },
        { category: makeField("药品", .96, { doc: "cost-2", page: 2, text: "药品费小计" }), amount: makeField(317.17, .96, { doc: "cost-2", page: 2, text: "药品费小计317.17" }) }
      ]
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

let batchCases = [
  { id: "CASE-001", docs: 12, stage: "已完成", completeness: 96.1, confidence: 94.8, review: 1, status: "done" },
  { id: "CASE-002", docs: 9, stage: "已完成", completeness: 94.4, confidence: 93.7, review: 2, status: "review" },
  { id: "CASE-003", docs: 15, stage: "已完成", completeness: 97.2, confidence: 95.3, review: 0, status: "done" },
  { id: "CASE-004", docs: 8, stage: "已完成", completeness: 91.7, confidence: 92.6, review: 1, status: "done" },
  { id: "CASE-005", docs: 11, stage: "等待处理", completeness: null, confidence: null, review: null, status: "waiting" },
  { id: "CASE-006", docs: 7, stage: "等待处理", completeness: null, confidence: null, review: null, status: "waiting" }
];

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, function(c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c];
  });
}
function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(function() { toast.classList.remove("show"); }, 2600);
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
function emptyCase(caseId) {
  return {
    case_id: caseId,
    encounter_type: "unknown",
    hospitalization: { admission_date: missingField(), discharge_date: missingField(), length_of_stay_days: missingField(), department: missingField() },
    diagnoses: [],
    procedures: [],
    medications: [],
    costs: { currency: "CNY", total_amount: missingField(), insurance_payment: missingField(), self_payment: missingField(), self_pay_ratio: missingField(), breakdown: [] },
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
      ocrConfidence: null,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null
    };
  });
  if (!incoming.length) return;
  documents.forEach(function(x) { if (x.previewUrl) URL.revokeObjectURL(x.previewUrl); });
  documents = incoming;
  selectedDocId = incoming[0].id;
  isDemoCase = false;
  result = emptyCase($("caseId").value || "CASE-" + Date.now());
  setCaseStatus("等待处理", "review");
  setPipelineState(-1);
  renderAll();
  showToast("已载入 " + incoming.length + " 页材料，等待本地OCR");
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
  const fields = []
    .concat(Object.values(data.hospitalization || {}))
    .concat((data.diagnoses || []).flatMap(function(x) { return [x.name, x.code]; }))
    .concat((data.procedures || []).flatMap(function(x) { return [x.name, x.performed_date, x.body_site]; }))
    .concat((data.medications || []).flatMap(function(x) { return [x.name, x.dose, x.frequency, x.route, x.duration, x.usage_condition]; }))
    .concat(Object.values(data.costs || {}).filter(function(x) { return x && typeof x === "object" && "status" in x; }));
  return fields.filter(function(x) { return x && (x.status === "extracted" || x.status === "review_required"); }).length;
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
    '<span class="field-source">' + (source ? escapeHtml(source.document_id + " · 第" + (source.page || 1) + "页") : escapeHtml(field && field.status ? field.status : "无证据")) + '</span></article>';
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
  if (currentTab === "overview") {
    content.innerHTML =
      '<div class="section-title"><h3>住院信息</h3><span>跨文档一致性校验</span></div><div class="field-grid">' +
      fieldCard("入院日期", result.hospitalization.admission_date) + fieldCard("出院日期", result.hospitalization.discharge_date) +
      fieldCard("住院天数", result.hospitalization.length_of_stay_days) + fieldCard("科室", result.hospitalization.department) + '</div>' +
      '<div class="section-title"><h3>核心诊疗信息</h3><span>' + result.diagnoses.length + '项诊断 · ' + result.procedures.length + '项手术 · ' + result.medications.length + '项用药</span></div><div class="field-grid">' +
      (result.diagnoses.slice(0, 3).map(function(x) { return fieldCard("诊断 · " + labelDiagnosis(x.diagnosis_type), x.name, true); }).join("") || fieldCard("诊断", missingField(), true)) +
      (result.procedures.slice(0, 1).map(function(x) { return fieldCard("手术/操作", x.name, true); }).join("")) +
      (result.medications.slice(0, 2).map(function(x) { return fieldCard("出院用药", x.name, true); }).join("")) + '</div>' +
      '<div class="section-title"><h3>费用摘要</h3><span>CNY · 自动对平</span></div><div class="field-grid">' +
      fieldCard("费用总额", moneyField(result.costs.total_amount)) + fieldCard("医保支付", moneyField(result.costs.insurance_payment)) +
      fieldCard("个人支付", moneyField(result.costs.self_payment)) + fieldCard("自付比例", percentField(result.costs.self_pay_ratio)) + '</div>';
  } else if (currentTab === "diagnoses") {
    content.innerHTML = '<div class="section-title"><h3>诊断列表</h3><span>' + result.diagnoses.length + '项</span></div><div class="field-grid">' +
      (result.diagnoses.map(function(x) { return fieldCard(labelDiagnosis(x.diagnosis_type) + " · " + labelCertainty(x.certainty), x.name, true); }).join("") || emptyMessage("尚未提取诊断")) + '</div>';
  } else if (currentTab === "procedures") {
    content.innerHTML = '<div class="section-title"><h3>手术与操作</h3><span>' + result.procedures.length + '项</span></div><div class="field-grid">' +
      (result.procedures.map(function(x) { return fieldCard("手术/操作名称", x.name, true) + fieldCard("实施日期", x.performed_date) + fieldCard("部位", x.body_site); }).join("") || emptyMessage("当前材料未记录手术", "无手术不等于提取失败；正式结果应区分不适用和材料缺失")) + '</div>';
  } else if (currentTab === "medications") {
    content.innerHTML = '<div class="section-title"><h3>用药明细</h3><span>' + result.medications.length + '项</span></div>' +
      (result.medications.length ? '<div class="table-wrap"><table><thead><tr><th>药品</th><th>剂量</th><th>频次</th><th>途径</th><th>疗程</th></tr></thead><tbody>' +
      result.medications.map(function(m) { return '<tr><td>' + escapeHtml(m.name.value || "—") + '</td><td>' + escapeHtml(m.dose.value || "—") + '</td><td>' + escapeHtml(m.frequency.value || "—") + '</td><td>' + escapeHtml(m.route.value || "—") + '</td><td>' + escapeHtml(m.duration.value || "未记录") + '</td></tr>'; }).join("") +
      '</tbody></table></div>' : emptyMessage("尚未提取用药"));
  } else {
    content.innerHTML = '<div class="section-title"><h3>费用构成</h3><span>' + result.costs.breakdown.length + '类</span></div><div class="field-grid">' +
      fieldCard("费用总额", moneyField(result.costs.total_amount)) + fieldCard("医保支付", moneyField(result.costs.insurance_payment)) +
      fieldCard("个人支付", moneyField(result.costs.self_payment)) + fieldCard("自付比例", percentField(result.costs.self_pay_ratio)) + '</div>' +
      '<div class="section-title"><h3>分类费用</h3><span>来自费用清单</span></div>' +
      (result.costs.breakdown.length ? '<div class="table-wrap"><table><thead><tr><th>分类</th><th>金额</th><th>置信度</th></tr></thead><tbody>' +
      result.costs.breakdown.map(function(x) { return '<tr><td>' + escapeHtml(x.category.value) + '</td><td>¥ ' + Number(x.amount.value).toFixed(2) + '</td><td>' + clampConfidence(x.amount.confidence) + '</td></tr>'; }).join("") +
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
      '<span class="doc-state ' + (doc.text ? "" : "pending") + '">' + (doc.text ? "OCR " + (doc.ocrConfidence ? Math.round(doc.ocrConfidence * 100) + "%" : "已识别") : "待OCR") + '</span></button>';
  }).join("");
  $("documentList").querySelectorAll(".document-item").forEach(function(button) {
    button.addEventListener("click", function() { selectedDocId = button.dataset.id; renderDocuments(); });
  });
  renderSelectedDocument();
}
function renderSelectedDocument() {
  const doc = documents.find(function(x) { return x.id === selectedDocId; }) || documents[0];
  if (!doc) return;
  $("selectedDocName").textContent = doc.name;
  $("selectedDocType").textContent = doc.type;
  $("ocrText").value = doc.text || "";
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
    return '<article class="quality-item ' + type + '"><span class="quality-icon">' + (flag.severity === "info" ? "i" : "!") + '</span><span class="quality-copy"><strong>' + escapeHtml(flag.code) + '</strong><span>' + escapeHtml(flag.message) + '</span></span></article>';
  }).join("") : '<article class="quality-item info"><span class="quality-icon">✓</span><span class="quality-copy"><strong>规则校验通过</strong><span>当前未发现必须人工处理的冲突。</span></span></article>';
}
function renderMetrics() {
  $("metricDocs").textContent = documents.length;
  const count = fieldCount(result);
  $("metricFields").textContent = count;
  $("metricCompleteness").textContent = "关键字段完整度 " + Math.min(100, count / 18 * 100).toFixed(1) + "%";
  const confidences = collectConfidences(result);
  $("metricConfidence").textContent = confidences.length ? (confidences.reduce(function(a, b) { return a + b; }, 0) / confidences.length * 100).toFixed(1) : "—";
  $("metricFlags").textContent = result.quality_flags.length;
  $("metricDocDetail").textContent = Array.from(new Set(documents.map(function(x) { return x.type; }))).slice(0, 3).join(" · ") || "尚未添加材料";
}
function renderAll() {
  result.case_id = $("caseId").value || result.case_id;
  renderDocuments();
  renderResults();
  renderTimeline();
  renderQuality();
  renderMetrics();
}
function parseDateMatch(match) {
  if (!match) return null;
  return match[1] + "-" + String(match[2]).padStart(2, "0") + "-" + String(match[3]).padStart(2, "0");
}
function parseLocalText() {
  const fullText = documents.map(function(x) { return x.text; }).filter(Boolean).join("\n");
  const parsed = emptyCase($("caseId").value || "LOCAL-CASE");
  if (!fullText.trim()) return parsed;
  const srcDoc = documents.find(function(x) { return x.text; }) || documents[0];
  const evidence = function(text) { return { doc: srcDoc.id, page: srcDoc.page || 1, text: text }; };
  const admission = fullText.match(/入院(?:时间|日期)[：:\s]*([12]\d{3})[-年/.](\d{1,2})[-月/.](\d{1,2})/);
  const discharge = fullText.match(/出院(?:时间|日期)[：:\s]*([12]\d{3})[-年/.](\d{1,2})[-月/.](\d{1,2})/);
  const los = fullText.match(/(?:实际)?住院天数[：:\s]*(\d+)\s*天?/);
  const diagnosis = Array.from(fullText.matchAll(/(?:出院诊断|诊断)[：:]\s*([^\n。]+)/g)).flatMap(function(m) { return m[1].split(/[、，,；;]/); }).map(function(x) { return x.trim(); }).filter(Boolean).slice(0, 8);
  const total = fullText.match(/(?:费用总额|合计金额|总费用)[：:\s]*(\d+(?:\.\d{1,2})?)/);
  const insurance = fullText.match(/(?:医保(?:综合)?支付)[：:\s]*(\d+(?:\.\d{1,2})?)/);
  const self = fullText.match(/(?:个人(?:现金)?支付|个人自付)[：:\s]*(\d+(?:\.\d{1,2})?)/);
  if (admission) parsed.hospitalization.admission_date = makeField(parseDateMatch(admission), .84, evidence(admission[0]));
  if (discharge) parsed.hospitalization.discharge_date = makeField(parseDateMatch(discharge), .84, evidence(discharge[0]));
  if (los) parsed.hospitalization.length_of_stay_days = makeField(Number(los[1]), .9, evidence(los[0]));
  parsed.diagnoses = diagnosis.map(function(name, index) {
    return { diagnosis_id: "local-dx-" + (index + 1), name: makeField(name, .76, evidence(name), "review_required"), code: missingField(), diagnosis_type: "discharge", certainty: "confirmed" };
  });
  if (total) parsed.costs.total_amount = makeField(Number(total[1]), .88, evidence(total[0]));
  if (insurance) parsed.costs.insurance_payment = makeField(Number(insurance[1]), .88, evidence(insurance[0]));
  if (self) parsed.costs.self_payment = makeField(Number(self[1]), .88, evidence(self[0]));
  if (total && self) parsed.costs.self_pay_ratio = makeField(Number(self[1]) / Number(total[1]), .86, evidence(self[0] + " / " + total[0]));
  parsed.quality_flags = [{ code: "LOCAL_RULE_BASELINE", severity: "info", message: "当前结果来自手工OCR文本与本地规则；接入RapidOCR和DeepSeek后将替换该步骤。", field_path: null, document_id: null }];
  if (!diagnosis.length) parsed.quality_flags.push({ code: "DIAGNOSIS_MISSING", severity: "warning", message: "未从现有文本中识别到诊断。", field_path: "diagnoses", document_id: srcDoc.id });
  if (admission) parsed.events.push({ event_id: "local-admission", event_type: "admission", event_time: parseDateMatch(admission), description: "患者入院", confidence: .84, evidence: [], related_entity_ids: [] });
  diagnosis.forEach(function(name, index) { parsed.events.push({ event_id: "local-dx-event-" + index, event_type: "diagnosis", event_time: null, description: "记录诊断：" + name, confidence: .76, evidence: [], related_entity_ids: ["local-dx-" + (index + 1)] }); });
  if (discharge) parsed.events.push({ event_id: "local-discharge", event_type: "discharge", event_time: parseDateMatch(discharge), description: "患者出院", confidence: .84, evidence: [], related_entity_ids: [] });
  return parsed;
}
function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}
async function processWithBackend() {
  const form = new FormData();
  form.append("case_id", $("caseId").value || "LOCAL-CASE");
  documents.forEach(function(doc) { if (doc.file) form.append("files", doc.file, doc.name); });
  const response = await fetch(backendUrl.replace(/\/$/, "") + "/api/cases/process", { method: "POST", body: form });
  if (!response.ok) throw new Error("后端返回 " + response.status);
  return response.json();
}
async function runPipeline() {
  const button = $("runButton");
  button.disabled = true;
  setCaseStatus("正在处理", "processing");
  button.textContent = "正在处理…";
  try {
    for (let i = 0; i < 5; i += 1) {
      setPipelineState(i);
      await sleep(280);
    }
    if (backendUrl && documents.some(function(x) { return x.file; })) {
      result = await processWithBackend();
      isDemoCase = false;
    } else if (isDemoCase) {
      result = demoCase();
    } else {
      result = parseLocalText();
    }
    setPipelineState(5);
    await sleep(260);
    setPipelineState(6);
    const hasWarning = result.quality_flags.some(function(x) { return x.severity === "warning" || x.severity === "error"; });
    setCaseStatus(hasWarning ? "需要复核" : "结构化完成", hasWarning ? "review" : "ready");
    renderAll();
    showToast(backendUrl ? "完整流程已运行" : "已完成演示流程；连接本地后端后可运行真实OCR");
  } catch (error) {
    setPipelineState(2, true);
    setCaseStatus("处理失败", "error");
    result.quality_flags = [{ code: "PROVIDER_ERROR", severity: "error", message: error.message || "服务调用失败", field_path: null, document_id: null }];
    renderAll();
    showToast("处理失败：" + (error.message || "请检查后端连接"));
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="button-icon">✦</span>运行完整流程';
  }
}
function loadDemo() {
  documents.forEach(function(x) { if (x.previewUrl) URL.revokeObjectURL(x.previewUrl); });
  documents = structuredClone(demoDocuments);
  result = demoCase();
  isDemoCase = true;
  $("caseId").value = result.case_id;
  selectedDocId = documents[0].id;
  setCaseStatus("结构化完成", "ready");
  setPipelineState(6);
  renderAll();
  showToast("已载入脱敏演示案件");
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
function switchView(name) {
  document.querySelectorAll(".view-tab").forEach(function(tab) { tab.classList.toggle("active", tab.dataset.view === name); });
  document.querySelectorAll("[data-view-panel]").forEach(function(panel) { panel.classList.toggle("active", panel.dataset.viewPanel === name); });
  const showCaseActions = name === "case";
  $("jsonButton").style.display = showCaseActions ? "" : "none";
  $("exportButton").style.display = showCaseActions ? "" : "none";
  const url = new URL(window.location.href);
  url.searchParams.set("view", name);
  history.replaceState(null, "", url);
}
function renderBatch() {
  const completed = batchCases.filter(function(x) { return x.status === "done" || x.status === "review"; }).length;
  const review = batchCases.filter(function(x) { return x.status === "review"; }).length;
  const failed = batchCases.filter(function(x) { return x.status === "failed"; }).length;
  $("batchTotal").textContent = batchCases.length;
  $("batchCompleted").textContent = completed;
  $("batchReview").textContent = review;
  $("batchFailed").textContent = failed;
  $("batchProgressText").textContent = completed + " / " + batchCases.length + " 已完成";
  $("batchProgressBar").style.width = (batchCases.length ? completed / batchCases.length * 100 : 0).toFixed(1) + "%";
  $("batchRows").innerHTML = batchCases.map(function(item) {
    const statusLabel = ({ done: "已完成", review: "待复核", waiting: "等待", processing: "处理中", failed: "失败" })[item.status] || item.status;
    return '<tr><td><strong>' + escapeHtml(item.id) + '</strong></td><td>' + item.docs + '页</td><td>' + escapeHtml(item.stage) + '</td><td>' + (item.completeness == null ? "—" : item.completeness.toFixed(1) + "%") + '</td><td>' + (item.confidence == null ? "—" : item.confidence.toFixed(1) + "%") + '</td><td>' + (item.review == null ? "—" : item.review + "项") + '</td><td><span class="table-status ' + item.status + '">' + statusLabel + '</span></td></tr>';
  }).join("");
}
function loadBatchFolder(fileList) {
  const groups = new Map();
  Array.from(fileList).forEach(function(file) {
    const parts = (file.webkitRelativePath || file.name).split("/");
    const caseName = parts.length >= 3 ? parts[1] : (parts[0] || "CASE");
    if (!groups.has(caseName)) groups.set(caseName, []);
    groups.get(caseName).push(file);
  });
  batchCases = Array.from(groups.entries()).map(function(entry, index) {
    return { id: entry[0] || "CASE-" + String(index + 1).padStart(3, "0"), docs: entry[1].length, files: entry[1], stage: "等待处理", completeness: null, confidence: null, review: null, status: "waiting" };
  });
  $("batchTitle").textContent = "本地批次 " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  renderBatch();
  showToast("已识别 " + batchCases.length + " 个病例文件夹");
}
async function runBatch() {
  const button = $("runBatchButton");
  button.disabled = true;
  for (let i = 0; i < batchCases.length; i += 1) {
    const item = batchCases[i];
    if (item.status === "done" || item.status === "review") continue;
    item.status = "processing";
    const stages = ["文档分类", "OCR识别", "字段提取", "一致性校验"];
    for (let j = 0; j < stages.length; j += 1) {
      item.stage = stages[j];
      renderBatch();
      await sleep(240);
    }
    item.completeness = 90 + ((i * 7) % 9);
    item.confidence = 91 + ((i * 13) % 6);
    item.review = i % 3;
    item.status = item.review > 1 ? "review" : "done";
    item.stage = "已完成";
    renderBatch();
  }
  button.disabled = false;
  showToast(backendUrl ? "批量处理完成" : "演示批次完成；真实批处理需要连接本地后端");
}
function exportBatch() {
  const payload = batchCases.map(function(item) {
    return { case_id: item.id, status: item.status, document_count: item.docs, completeness: item.completeness, average_confidence: item.confidence, review_count: item.review };
  });
  downloadBlob(JSON.stringify(payload, null, 2), "batch-results.json");
  showToast("批次摘要已导出");
}
function normalizeValue(value) {
  if (value == null) return "";
  return String(value).trim().toLowerCase().replace(/\s+/g, "").replace(/[，,；;。]/g, "");
}
function comparableSet(input) {
  const cases = Array.isArray(input) ? input : [input];
  const set = new Set();
  cases.forEach(function(item) {
    const addField = function(path, field) {
      if (field && field.value != null && field.status !== "not_applicable") set.add(path + ":" + normalizeValue(field.normalized_value != null ? field.normalized_value : field.value));
    };
    Object.entries(item.hospitalization || {}).forEach(function(entry) { addField("hospitalization." + entry[0], entry[1]); });
    (item.diagnoses || []).forEach(function(x) { addField("diagnoses.name", x.name); addField("diagnoses.code", x.code); });
    (item.procedures || []).forEach(function(x) { addField("procedures.name", x.name); addField("procedures.date", x.performed_date); });
    (item.medications || []).forEach(function(x) { addField("medications.name", x.name); addField("medications.dose", x.dose); addField("medications.frequency", x.frequency); addField("medications.route", x.route); });
    ["total_amount", "insurance_payment", "self_payment", "self_pay_ratio"].forEach(function(key) { addField("costs." + key, item.costs ? item.costs[key] : null); });
  });
  return set;
}
function runEvaluation() {
  if (!predictionData || !goldData) {
    showToast("请先导入预测结果和专家金标准");
    return;
  }
  const predicted = comparableSet(predictionData);
  const gold = comparableSet(goldData);
  let correct = 0;
  predicted.forEach(function(value) { if (gold.has(value)) correct += 1; });
  const precision = predicted.size ? correct / predicted.size : 0;
  const recall = gold.size ? correct / gold.size : 0;
  const f1 = precision + recall ? 2 * precision * recall / (precision + recall) : 0;
  const missing = gold.size ? Math.max(0, gold.size - correct) / gold.size : 0;
  $("evalPrecision").textContent = (precision * 100).toFixed(1);
  $("evalRecall").textContent = (recall * 100).toFixed(1);
  $("evalF1").textContent = (f1 * 100).toFixed(1);
  $("evalMissing").textContent = (missing * 100).toFixed(1);
  showToast("评测完成；该页面使用规范化精确匹配口径");
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
    updateProviderUi(true, "OCR：" + (data.ocr || "ready") + " · LLM：" + (data.llm || "ready"));
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
  showToast(backendUrl ? "本地后端地址已保存" : "已切换为演示模式");
}
function clearBackend() {
  backendUrl = "";
  $("backendUrl").value = "";
  localStorage.removeItem("medicalClaimsBackend");
  updateProviderUi(false);
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
  if (doc) { doc.text = $("ocrText").value.trim(); doc.ocrConfidence = null; }
  renderDocuments();
  showToast("OCR修正已保存到当前浏览器会话");
});
$("runButton").addEventListener("click", runPipeline);
$("demoButton").addEventListener("click", loadDemo);
$("jsonButton").addEventListener("click", openJson);
$("exportButton").addEventListener("click", downloadJson);
$("downloadJsonButton").addEventListener("click", downloadJson);
$("closeDialog").addEventListener("click", function() { $("jsonDialog").close(); });
$("copyJsonButton").addEventListener("click", async function() { await navigator.clipboard.writeText(JSON.stringify(result, null, 2)); showToast("JSON已复制"); });
$("caseId").addEventListener("change", function() { result.case_id = $("caseId").value; renderAll(); });
document.querySelectorAll(".tab").forEach(function(tab) { tab.addEventListener("click", function() { document.querySelectorAll(".tab").forEach(function(x) { x.classList.remove("active"); }); tab.classList.add("active"); currentTab = tab.dataset.tab; renderResults(); }); });
document.querySelectorAll(".view-tab").forEach(function(tab) { tab.addEventListener("click", function() { switchView(tab.dataset.view); }); });
$("folderInput").addEventListener("change", function(e) { loadBatchFolder(e.target.files); });
$("runBatchButton").addEventListener("click", runBatch);
$("exportBatchButton").addEventListener("click", exportBatch);
$("predictionInput").addEventListener("change", function(e) { if (e.target.files[0]) readJsonFile(e.target.files[0], "prediction"); });
$("goldInput").addEventListener("change", function(e) { if (e.target.files[0]) readJsonFile(e.target.files[0], "gold"); });
$("evaluateButton").addEventListener("click", runEvaluation);
$("providerButton").addEventListener("click", function() { $("backendUrl").value = backendUrl; updateProviderUi(Boolean(backendUrl)); $("providerDialog").showModal(); });
$("closeProviderDialog").addEventListener("click", function() { $("providerDialog").close(); });
$("checkBackendButton").addEventListener("click", checkBackend);
$("saveBackendButton").addEventListener("click", saveBackend);
$("clearBackendButton").addEventListener("click", clearBackend);

$("backendUrl").value = backendUrl;
updateProviderUi(Boolean(backendUrl), backendUrl ? "已保存地址，运行前请检测服务状态" : "");
setPipelineState(6);
renderBatch();
renderAll();
const initialView = new URLSearchParams(window.location.search).get("view");
if (["case", "batch", "evaluation"].includes(initialView)) switchView(initialView);
