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
const navs = ["home", "pending", "cases", "profile"].map((view) => {
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
const testableSource = source.replace(/\}\)\(\);\s*$/, "globalThis.__medicalDisplayTest={evidenceSelect,aggregateRecommendationItems};})();");
vm.runInContext(testableSource, context);
for (const required of [
  "/field-reviews", "确认无误", "修改并确认", "无法确认", "preview_available",
  "/analysis/prepare", "/module-reviews", "准备医疗分析结果", "生成状态", "复核状态",
  "该结果基于旧病例版本生成", "本次病例观察", "发送到客户站内收件箱（比赛演示）",
  "/pain-points/", "SHOW", "EDIT", "HIDE", "/care/revision", "最终医疗门禁",
  "修订文字和病例依据", "发布前模块状态", "返回该模块",
  "已引用依据摘要", "查看详细依据", "修改依据", "care-evidence-search",
  "手术/治疗", "住院信息", "推荐理由与证据", "查看来源", "aggregateRecommendationItems",
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

const pickerHtml = context.__medicalDisplayTest.evidenceSelect({care_evidence_options:[
  {path:"diagnoses[0].name",label:"主诊断",value:"肺部阴影"},
  {path:"procedures[0].name",label:"手术",value:"VATS肺部分切除"},
  {path:"costs.total_amount",label:"总费用",value:"55541.2"},
]},["diagnoses[0].name","costs.total_amount"]);
assert.match(pickerHtml,/已引用 2 项/);
assert.match(pickerHtml,/主诊断：肺部阴影/);
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

async function flush() { await new Promise((resolve) => setImmediate(resolve)); }
(async () => {
  await flush();
  assert.equal(node("userName").textContent, "医疗复核员");
  assert.match(node("content").innerHTML, /最近处理案件/);

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
