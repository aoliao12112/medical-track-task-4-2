const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const nodes = new Map();
function node(id = "") {
  if (!nodes.has(id)) nodes.set(id, {
    id, innerHTML: "", textContent: "", value: "", disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
  });
  return nodes.get(id);
}
const navs = ["home", "tasks", "pending", "cases", "profile"].map((view) => {
  const item = node("nav-" + view); item.dataset.view = view; return item;
});
const requests = [];
let currentUser = {
  user_id: "medical-user", username: "medical001", display_name: "医疗复核员",
  phone: null, email: null, status: "ACTIVE", role_code: "MEDICAL", role_name: "医疗复核人员",
  permissions: ["MEDICAL_CASE_VIEW"],
};
function response(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}
async function fetchMock(path, options = {}) {
  requests.push({ path, options });
  if (path === "/api/auth/me" && options.method === "PATCH") {
    currentUser = { ...currentUser, ...JSON.parse(options.body) };
    return response(currentUser);
  }
  if (path === "/api/auth/me") return response(currentUser);
  if (path === "/api/medical/dashboard") return response({
    pending_review: 0, new_care_cases: 0, needs_confirmation: 0,
    completed_reviews: 0, recent_cases: [],
  });
  if (path === "/api/medical/tasks") return response([{
    task_id: "TASK-MED-1", case_id: "CASE-1", claim_event_id: "EVENT-1",
    category: "medication_question", priority: "HIGH", status: "OPEN",
    note: "请结合出院材料核对用药问题。", created_at: "2026-10-13T08:00:00Z",
  }]);
  if (path === "/api/auth/change-password") return response({ changed: true, message: "密码已修改" });
  if (path === "/api/auth/logout") return response({ logged_out: true });
  throw new Error("Unexpected fetch: " + path);
}
const documentMock = {
  getElementById: node,
  querySelectorAll(selector) { return selector === ".nav-item" ? navs : []; },
};
const context = {
  document: documentMock, fetch: fetchMock, location: { replace() {} }, alert() {},
  setTimeout(fn) { fn(); }, clearTimeout() {}, console,
};
vm.createContext(context);
const source = fs.readFileSync("web/dist/medical.js", "utf8");
const testableSource = source.replace(/\}\)\(\);\s*$/, "globalThis.__medicalDisplayTest={evidenceSelect,aggregateRecommendationItems,modulePanel,renderPain,renderQuality,renderReview};})();");
vm.runInContext(testableSource, context);
for (const required of [
  "/field-reviews", "确认无误", "修改并确认", "无法确认", "preview_available",
  "/analysis/prepare", "/module-reviews", "准备医疗分析结果", "生成状态", "复核状态",
  "该结果基于旧病例版本生成", "本次病例观察", "客户结果预览",
  "/pain-points/", "SHOW", "EDIT", "HIDE", "/care/revision", "最终医疗门禁",
  "修订文字和病例依据", "发布前模块状态", "返回该模块",
  "已引用依据摘要", "查看详细依据", "修改依据", "care-evidence-search",
  "手术/治疗", "住院信息", "推荐理由与证据", "查看来源", "aggregateRecommendationItems",
  "data-review-module=\"${module}\"", "复核通过", "退回修改",
  "归类原因", "系统建议", "纳入关怀", "编辑后纳入", "确认本页审核结果",
]) {
  assert.ok(source.includes(required), `medical field review UI missing: ${required}`);
}
assert.ok(!source.includes('data-decision="NEEDS_CONFIRMATION"'),
  "ordinary MEDICAL UI must not expose the ambiguous confirmation action");
assert.ok(!source.includes("data.pending_confirmation_count?'<div"),
  "module field warning must not use the mixed medical attention count");
assert.ok(!source.includes('<select class="input care-evidence" multiple'),
  "care evidence must not render the full multi-select by default");
const customerSource = fs.readFileSync("web/dist/customer.js", "utf8");
assert.ok(!customerSource.includes("evidence_fields"),
  "CUSTOMER portal must not expose care evidence_fields");
assert.ok(!customerSource.includes("pain_result"),
  "CUSTOMER portal must not consume raw pain_result");

const painHtml = context.__medicalDisplayTest.renderPain({
  pain_result: {summary:{clinical:1,cost:0,experience:0},pain_points:[{
    pain_point_id:"pain-1",dimension:"clinical",type:"ABNORMAL_EXAMINATION_FOLLOWUP",
    title:"复查安排需要关注",description:"检查异常但材料中未见后续复查安排。",
    recommended_action:"复诊时向医生确认复查时间。",severity:"medium",confidence:.9,
    evidence:[{document_id:"record.jpg",page:1,text:"检查异常，建议复查"}],
  }]},
  pain_item_reviews:[],
  pain_suggested_dispositions:[{pain_point_id:"pain-1",decision:"SHOW",reason:"证据与行动建议完整。"}],
  module_states:{pain:{generation_status:"GENERATED",review_status:"REVIEW_REQUIRED",result_available:true,blockers:[],warnings:[]}},
  field_review_summary:{pending:0,unresolved:0},
});
assert.match(painHtml,/具体类型：/);
assert.match(painHtml,/归类原因：/);
assert.match(painHtml,/系统建议：<\/strong>纳入关怀/);
assert.match(painHtml,/确认本页审核结果/);
assert.ok(!painHtml.includes("向客户展示"),"pain review uses internal care semantics");

const pickerHtml = context.__medicalDisplayTest.evidenceSelect({care_evidence_options:[
  {path:"diagnoses[0].name",label:"主诊断",value:"肺部阴影"},
  {path:"procedures[0].name",label:"手术",value:"VATS肺部分切除"},
  {path:"costs.total_amount",label:"总费用",value:"55541.2"},
]},["diagnoses[0].name","costs.total_amount"]);
assert.match(pickerHtml,/已引用 2 项/);
assert.match(pickerHtml,/主诊断：肺部阴影/);
const currentEvidence = pickerHtml.match(/<details class="care-evidence-current">([\s\S]*?)<\/details>/)[1];
assert.equal((currentEvidence.match(/主诊断：肺部阴影/g)||[]).length,1,
  "selected evidence must render once in the detail list");
assert.match(pickerHtml,/<details class="care-evidence-current"><summary>查看详细依据<\/summary>/);
assert.ok(!pickerHtml.includes('<details class="care-evidence-current" open'),
  "evidence details must be collapsed by default and use native toggle behavior");
assert.ok(!pickerHtml.includes("care-evidence-summary-list"),
  "summary must not duplicate the complete evidence list");
assert.match(pickerHtml,/诊断/);
assert.match(pickerHtml,/费用/);
assert.ok(!pickerHtml.includes("<select"),"care evidence editor must use grouped, collapsed checkboxes");

const aggregated = context.__medicalDisplayTest.aggregateRecommendationItems([
  {item_type:"HOSPITAL",entity_id:"h-1",entity_name:"测试医院",rank_no:1,total_score:88,evidence_status:"VERIFIED",scores:[],evidence:[
    {reason:"具备肺部影像相关能力",source_url:"https://hospital.example/resp",source_name:"医院官网",evidence_summary:"设有呼吸专科",evidence_status:"VERIFIED"},
    {reason:"支持肺部影像相关诊断",source_url:"https://hospital.example/resp",source_name:"医院官网",evidence_summary:"设有呼吸专科",evidence_status:"VERIFIED"},
  ]},
  {item_type:"HOSPITAL",entity_id:"h-1",entity_name:"测试医院",rank_no:2,total_score:80,evidence_status:"PARTIALLY_VERIFIED",scores:[],evidence:[
    {reason:"具备肺部影像相关能力",source_url:"https://health.example/list",source_name:"卫健委",evidence_summary:"专科名单包含该院",evidence_status:"PARTIALLY_VERIFIED"},
  ]},
]);
assert.equal(aggregated.length,1,"same recommendation entity must be merged");
assert.equal(aggregated[0].evidence.length,3,"raw evidence must remain available");
assert.equal(aggregated[0]._displaySources.length,2,"same source_url must display once");
assert.equal(aggregated[0]._displayEvidence.length,1,"similar capability reasons must be grouped");
assert.ok(aggregated[0]._displayEvidence.length<=4,"default recommendation reasons must be limited to four");

const qualityHtml = context.__medicalDisplayTest.renderQuality({
  quality:{single_case_notice:"单病例观察",historical_profiles:[],summary:{overall_score:82,overall_level:"良好",scored_weight_coverage:.85},case_observations:[
    {metric_code:"treatment_diagnosis_match",raw_value:null,status:"REVIEW_REQUIRED",disease_group:"pneumonia",evidence:{reasoning:"需要结合完整病情复核",evidence_fields:["diagnoses","procedures"],limitations:["缺少完整适应证信息"]}},
    {metric_code:"medication_rationality",raw_value:null,status:"DATA_UNAVAILABLE",disease_group:"pneumonia",evidence:{reasoning:"当前材料未记录可评价的用药",evidence_fields:["medications"],limitations:[]}},
    {metric_code:"procedure_rationality",raw_value:null,status:"DATA_UNAVAILABLE",disease_group:"pneumonia",evidence:{reasoning:"当前材料未记录手术或操作",evidence_fields:["procedures"],limitations:[]}},
    {metric_code:"risk_management",raw_value:null,status:"REVIEW_REQUIRED",disease_group:"pneumonia",evidence:{reasoning:"需要核对风险处置",evidence_fields:["pain_report","events"],limitations:[]}},
    {metric_code:"discharge_continuity",raw_value:null,status:"DATA_UNAVAILABLE",disease_group:"pneumonia",evidence:{reasoning:"缺少出院计划",evidence_fields:["medications","events"],limitations:[]}},
    {metric_code:"treatment_completeness",raw_value:80,status:"OBSERVED",disease_group:"pneumonia",evidence:{reasoning:"结构化材料覆盖较完整",evidence_fields:["diagnoses","hospitalization"],limitations:["仅评价记录完整性"]}},
  ]},
  module_states:{treatment_quality:{generation_status:"GENERATED",review_status:"REVIEW_REQUIRED",result_available:true,blockers:[],warnings:[]}},
  field_review_summary:{pending:0,unresolved:0},
});
for(const label of ["诊疗一致性","用药合理性","手术/操作合理性","风险管理","出院与随访完整性","病历与诊疗完整性"]){
  assert.match(qualityHtml,new RegExp(label));
}
assert.match(qualityHtml,/证据不足 \/ 暂不评分/);
assert.match(qualityHtml,/80\.0 \/ 100/);
assert.match(qualityHtml,/82\.0 \/ 100/);
assert.match(qualityHtml,/评价信息覆盖/);
assert.match(qualityHtml,/85%/);
assert.doesNotMatch(qualityHtml,/未获得 score/);

const insufficientQualityHtml = context.__medicalDisplayTest.renderQuality({
  quality:{single_case_notice:"单病例观察",historical_profiles:[],summary:{overall_score:null,scored_weight_coverage:.45},case_observations:[]},
  module_states:{treatment_quality:{generation_status:"GENERATED",review_status:"REVIEW_REQUIRED",result_available:true,blockers:[],warnings:[]}},
  field_review_summary:{pending:0,unresolved:0},
});
assert.match(insufficientQualityHtml,/综合评分：暂不生成/);
assert.match(insufficientQualityHtml,/45%/);

const reviewHtml = context.__medicalDisplayTest.renderReview({
  review_gate:{decision:"REVIEW_REQUIRED",reasons:["safety_checks.EVIDENCE_PATHS=review","pending-hospital-x","confidence=0.049 < .5"]},
  final_gate:{decision:"NOT_READY",reasons:["客户可见模块尚未全部通过"]},
  final_review_matrix:{modules:[],blockers:[],can_final_approve:false},
  pending_confirmation_count:0,field_review_summary:{pending:0,unresolved:0},reviews:[],
});
assert.doesNotMatch(reviewHtml,/内容安全门禁|safety_checks|pending-hospital|confidence=0\.049/);
assert.match(reviewHtml,/最终医疗门禁/);
assert.doesNotMatch(source,/发送到客户站内收件箱（比赛演示）|比赛演示将结果写入客户站内收件箱/);

const recommendationReview = context.__medicalDisplayTest.modulePanel("recommendation", {
  recommendation: {items: []},
  module_states: {recommendation: {
    generation_status: "GENERATED", review_status: "REVIEW_REQUIRED",
    result_available: false, blockers: [], warnings: [],
  }},
  field_review_summary: {pending: 0, unresolved: 0},
});
assert.match(recommendationReview,/data-review-module="recommendation"/);
assert.match(recommendationReview,/data-decision="APPROVED">复核通过/);
assert.match(recommendationReview,/data-decision="RETURNED">退回修改/);

async function flush() { await new Promise((resolve) => setImmediate(resolve)); }
(async () => {
  await flush();
  assert.equal(node("userName").textContent, "医疗复核员");
  assert.match(node("content").innerHTML, /最近处理案件/);

  navs.find((item) => item.dataset.view === "tasks").onclick();
  await flush();
  assert.match(node("content").innerHTML, /客服转交的医疗咨询/);
  assert.match(node("content").innerHTML, /请结合出院材料核对用药问题/);
  assert.ok(requests.some((item) => item.path === "/api/medical/tasks"));

  navs.find((item) => item.dataset.view === "profile").onclick();
  await flush();
  assert.match(node("content").innerHTML, /编辑资料/);
  assert.match(node("content").innerHTML, /修改密码/);

  node("editProfile").onclick();
  node("profileDisplayName").value = "新医疗复核员";
  node("profilePhone").value = "+86 138 0000 0000";
  node("profileEmail").value = "medical@example.com";
  await node("profileForm").onsubmit({ preventDefault() {} });
  assert.equal(node("userName").textContent, "新医疗复核员");
  const patch = requests.find((item) => item.path === "/api/auth/me" && item.options.method === "PATCH");
  assert.ok(patch, "profile save must call PATCH /api/auth/me");
  assert.deepEqual(JSON.parse(patch.options.body), {
    display_name: "新医疗复核员", phone: "+86 138 0000 0000", email: "medical@example.com",
  });

  node("changePassword").onclick();
  node("currentPassword").value = "old-password";
  node("newPassword").value = "new-password";
  node("confirmNewPassword").value = "new-password";
  await node("passwordForm").onsubmit({ preventDefault() {} });
  assert.ok(requests.some((item) => item.path === "/api/auth/change-password"),
    "password form must call shared change-password API");
  console.log(JSON.stringify({ checks: "passed", scope: "MEDICAL review display, evidence aggregation and profile mock DOM/API regression" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
