const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const nodes = new Map();
function node(id = "") {
  if (!nodes.has(id)) nodes.set(id, {
    id, innerHTML: "", textContent: "", value: "", checked: false, disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
  });
  return nodes.get(id);
}
const navs = ["home", "cases", "results", "feedback", "profile"].map((view) => {
  const item = node("nav-" + view); item.dataset.view = view; return item;
});
const openCaseButton = node("open-case");
openCaseButton.dataset.id = "CASE-1";
const requests = [];
const currentUser = {
  user_id: "customer-user", username: "customer001", display_name: "测试客户",
  phone: null, email: null, status: "ACTIVE", role_code: "CUSTOMER", role_name: "客户",
};
const delivery = {
  delivery_id: "DELIVERY-1", recommendation_included: true,
  content: {
    care_guide: { guide: {
      disease_topic: "康复关怀",
      opening: { title: "给您的话", content: "请按医嘱稳步康复。" },
      diagnosis_explanation: { title: "病情解释", content: "这是经审核的病情解释。" },
      treatment_review: { title: "治疗过程", content: "这是经审核的治疗回顾。" },
      cost_explanation: { title: "费用解释", content: "这是经审核的费用说明。" },
      recovery_plan: [{ action: "按计划康复", timing: "每日", reason: "帮助恢复" }],
      psychological_support: { title: "安心提示", content: "不必独自承担焦虑。" },
      follow_up_plan: [{ action: "按期复查", timing: "遵医嘱", reason: "确认恢复情况" }],
      red_flags: [], limitations: ["本指引不能替代医生诊疗。"],
    } },
    attention_items: [{ pain_point_id: "pain-1", title: "用药信息需确认", summary: "材料未记录完整用法。", suggestion: "复诊时请医生核对。", severity: "medium" }],
    recommendation: [{ recommendations: [{
      item_type: "HOSPITAL", entity_name: "测试医院", city: "上海", department: "康复医学科",
      reasons: [{ title: "专科匹配", explanation: "公开资料显示设有相关专科。" }],
      limitations: [], sources: [],
    }] }],
    recommendation_unavailable_reason: null,
  },
};
function response(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}
async function fetchMock(path, options = {}) {
  requests.push({ path, options });
  if (path === "/api/auth/me") return response(currentUser);
  if (path === "/api/customer/dashboard") return response({
    total: 1, results_available: 1, awaiting_result: 0, feedback_submitted: 0,
    recent_cases: [{ case_id: "CASE-1", claim_event_id: "EVENT-1", claim_status: "COMPLETED", care_status: "DELIVERED_MOCK", delivery_id: "DELIVERY-1", updated_at: "2026-10-12T08:00:00Z" }],
  });
  if (path === "/api/customer/cases/CASE-1") return response({
    case_id: "CASE-1", claim_event_id: "EVENT-1", claim_status: "COMPLETED",
    care_status: "DELIVERED_MOCK", updated_at: "2026-10-12T08:00:00Z",
    delivery, feedback_submitted: false,
  });
  if (path === "/api/auth/logout") return response({ logged_out: true });
  throw new Error("Unexpected fetch: " + path);
}
const documentMock = {
  getElementById: node,
  createElement() {
    return {
      _text: "", set textContent(value) { this._text = value == null ? "" : String(value); },
      get innerHTML() { return this._text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); },
    };
  },
  querySelectorAll(selector) {
    if (selector === ".nav-item") return navs;
    if (selector === ".open-case") return [openCaseButton];
    return [];
  },
};
const context = {
  document: documentMock, fetch: fetchMock, location: { replace() {} }, alert() {},
  setTimeout(fn) { fn(); }, clearTimeout() {}, console,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("web/dist/customer.js", "utf8"), context);

async function flush() {
  for (let index = 0; index < 4; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
(async () => {
  await flush();
  assert.equal(node("userName").textContent, "测试客户");
  assert.match(node("content").innerHTML, /查看已关联案件/);
  assert.doesNotMatch(node("content").innerHTML, /发起理赔/);

  await openCaseButton.onclick();
  await flush();
  const html = node("content").innerHTML;
  for (const heading of ["本次就医回顾", "需要关注", "康复与下一步建议", "本次就医品质观察", "下次就医推荐"]) {
    assert.match(html, new RegExp(heading));
  }
  assert.match(html, /测试医院/);
  assert.match(html, /用药信息需确认/);
  assert.doesNotMatch(html, /entity_id|HOSP-SECRET|source_ids|evidence_fields|fingerprint/);
  assert.ok(requests.some((item) => item.path === "/api/customer/cases/CASE-1"));
  console.log(JSON.stringify({ checks: 12, scope: "CUSTOMER result information architecture and API wiring" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
