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
vm.runInContext(fs.readFileSync("web/dist/medical.js", "utf8"), context);
const source = fs.readFileSync("web/dist/medical.js", "utf8");
for (const required of [
  "/field-reviews", "确认无误", "修改并确认", "无法确认", "preview_available",
  "/analysis/prepare", "/module-reviews", "准备医疗分析结果", "生成状态", "复核状态",
  "该结果基于旧病例版本生成", "本次病例观察",
]) {
  assert.ok(source.includes(required), `medical field review UI missing: ${required}`);
}

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
  console.log(JSON.stringify({ checks: 15, scope: "MEDICAL profile and field review mock DOM/API regression" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
