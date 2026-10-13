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
const navs = ["home", "consult", "routing", "followups", "profile"].map((view) => {
  const item = node("nav-" + view); item.dataset.view = view; return item;
});
const requests = [];
const currentUser = {
  user_id: "service-user", username: "service001", display_name: "测试客服",
  phone: null, email: null, status: "ACTIVE", role_code: "SERVICE", role_name: "客服人员",
};
let followUpTasks = [{
  task_id: "TASK-1", claim_event_id: "EVENT-1", category: "human_service_request",
  priority: "medium", status: "OPEN", created_at: "2026-10-12T08:00:00Z",
  feedback_comment: "希望客服说明下一步安排", feedback_rating: 2,
  request_contact: true, customer_phone_masked: "****3456", assignee: null,
}];
const routedTasks=[{task_id:"ROUTED-1",claim_event_id:"EVENT-1",category:"medication_question",priority:"HIGH",status:"RESOLVED",target_role:"MEDICAL",created_at:"2026-10-12T08:00:00Z",claimed_at:"2026-10-12T08:10:00Z",resolved_at:"2026-10-12T08:20:00Z",assignee_name:"医疗复核员",note:"请核对用药问题",resolution:"已完成医学答复"}];
function response(body, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}
async function fetchMock(path, options = {}) {
  requests.push({ path, options });
  if (path === "/api/auth/me") return response(currentUser);
  if (path === "/api/service/dashboard") return response({
    total_cases: 1, pending_consultations: 0, active_follow_ups: 1, urgent_follow_ups: 0,
    recent_cases: [],
  });
  if (path === "/api/service/follow-ups") return response(followUpTasks);
  if (path === "/api/service/routed-tasks") return response(routedTasks);
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
    return [];
  },
};
const context = {
  document: documentMock, fetch: fetchMock, location: { replace() {} }, alert() {},
  window: { prompt() { return null; } }, setTimeout(fn) { fn(); }, clearTimeout() {}, console,
};
vm.createContext(context);
const serviceSource = fs.readFileSync("web/dist/service.js", "utf8");
vm.runInContext(serviceSource, context);
for (const required of [
  "医疗问题进入医疗待办", "客服本人继续处理", "route_target_role",
  "target_role: targetRole", "请完整选择分流目标", "SERVICE_HANDLING",
]) {
  assert.ok(serviceSource.includes(required), `service routing UI missing: ${required}`);
}

async function flush() {
  for (let index = 0; index < 4; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
(async () => {
  await flush();
  navs.find((item) => item.dataset.view === "followups").onclick();
  await flush();
  const html = node("content").innerHTML;
  assert.match(html, /客户已同意回访/);
  assert.match(html, /联系手机号：\*\*\*\*3456/);
  assert.match(html, /负责人：未领取/);
  assert.doesNotMatch(html, /13800123456/);
  assert.match(html, /已分流任务跟踪/);
  assert.match(html, /已完成医学答复/);
  assert.ok(requests.some((item) => item.path === "/api/service/follow-ups"));

  followUpTasks = [{ ...followUpTasks[0], request_contact: false, customer_phone_masked: null }];
  navs.find((item) => item.dataset.view === "followups").onclick();
  await flush();
  const protectedHtml = node("content").innerHTML;
  assert.match(protectedHtml, /客户未同意回访/);
  assert.match(protectedHtml, /联系方式受保护，不予展示/);
  assert.doesNotMatch(protectedHtml, /\*\*\*\*3456/);
  console.log(JSON.stringify({ checks: 11, scope: "SERVICE masked contact and consent rendering" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
