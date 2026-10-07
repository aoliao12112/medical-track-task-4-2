const PUBLICATION_KEY = "medicalClaimsPatientPublicationV1";
const LOCAL_FEEDBACK_KEY = "medicalClaimsPendingPatientFeedbackV1";

function $(id) { return document.getElementById(id); }
function escapeHtml(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function(char) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]; }); }
function maskCaseId(value) { const text = String(value || ""); return text.length <= 4 ? text : "••••" + text.slice(-4); }
function displayDate(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" }); }

async function readPublication() {
  if (new URLSearchParams(window.location.search).get("demo") === "1") return demoPublication();
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const previewToken = fragment.get("preview_token");
  const apiBase = fragment.get("api_base");
  if (previewToken || apiBase) {
    if (!previewToken || !apiBase) throw new Error("手机预览链接不完整");
    let parsedApi;
    try { parsedApi = new URL(apiBase); } catch (error) { throw new Error("手机预览服务地址无效"); }
    if (!["http:", "https:"].includes(parsedApi.protocol)) throw new Error("手机预览服务地址不受支持");
    const response = await fetch(apiBase.replace(/\/$/, "") + "/api/mobile-preview", {
      headers: { "X-Preview-Token": previewToken }
    });
    const payload = await response.json().catch(function() { return null; });
    if (!response.ok) throw new Error(payload && payload.detail ? payload.detail : "无法读取手机预览");
    return payload;
  }
  try { return JSON.parse(localStorage.getItem(PUBLICATION_KEY) || "null"); }
  catch (error) { return null; }
}

function demoPublication() {
  return {
    version: "patient-publication-demo-v1",
    publication_id: "DEMO-PUBLICATION",
    status: "demo",
    case_id: "DEMO-RESP-001",
    published_at: null,
    backend_url: null,
    report: {
      case_id: "DEMO-RESP-001",
      guide: {
        case_id: "DEMO-RESP-001",
        disease_topic: "肺炎",
        opening: { title: "想先对您说", content: "这段时间经历检查、治疗和理赔材料整理，感到疲惫或担心都很正常。我们根据这次就医材料，把已经发生的事情和接下来可以做的准备整理在这里。" },
        diagnosis_explanation: { title: "这次诊断怎么理解", content: "本次材料记录了肺炎。它表示肺部出现了感染或炎症，需要结合症状、检查结果和恢复情况持续观察。单独一个名称不能代表病情轻重，请以经治医生的综合判断为准。" },
        treatment_review: { title: "本次治疗回顾", content: "材料记录了针对感染和不适症状的治疗，并完成了相关检查。药品名称、剂量和疗程应继续以出院医嘱为准，不要自行停药、换药或调整剂量。" },
        cost_explanation: { title: "费用怎么理解", content: "本次材料中的医疗总费用、医保支付、个人自付和个人自费需要分别核对。个人自付属于医保目录内个人承担部分，个人自费通常是目录外费用，两者不能合并理解。" },
        recovery_plan: [
          { action: "按照出院医嘱休息和用药", timing: "从今天开始", reason: "让治疗按计划完成，并观察症状变化" },
          { action: "记录体温、咳嗽和呼吸情况", timing: "每天", reason: "复诊时能更清楚地向医生说明恢复过程" },
          { action: "准备本次病历和检查报告", timing: "复诊前", reason: "便于医生连续了解本次治疗经过" }
        ],
        psychological_support: { title: "给您的提醒", content: "看到检查异常或医学术语时紧张并不罕见。恢复往往需要一点时间，先照顾好休息和饮食，有疑问时逐项向医生确认，不必一个人反复猜测。" },
        follow_up_plan: [
          { action: "确认复诊时间和复查项目", timing: "按出院医嘱", reason: "不同病情所需的复查时间并不相同" },
          { action: "把仍然担心的问题写成清单", timing: "复诊前", reason: "避免就诊时遗漏重要问题" }
        ],
        red_flags: ["呼吸明显困难或胸闷加重", "持续高热或精神状态明显变差", "口唇发紫、意识异常或其他紧急不适"],
        limitations: ["本指引仅依据本次提交的理赔医疗材料生成，不能替代医生诊断。", "不要据此自行停药、换药或调整剂量。"]
      }
    }
  };
}

function renderActionList(items, caseId) {
  const progressKey = "medicalClaimsCareProgress:" + caseId;
  let progress = {};
  try { progress = JSON.parse(localStorage.getItem(progressKey) || "{}"); } catch (error) { progress = {}; }
  $("recoveryList").innerHTML = (items || []).map(function(item, index) {
    return '<label class="action-item ' + (progress[index] ? "done" : "") + '"><input type="checkbox" data-action-index="' + index + '" ' + (progress[index] ? "checked" : "") + '><span><strong>' + escapeHtml(item.action) + '</strong><span>' + escapeHtml(item.timing) + ' · ' + escapeHtml(item.reason) + '</span></span></label>';
  }).join("");
  function updateProgress() {
    const boxes = Array.from(document.querySelectorAll("[data-action-index]"));
    const state = {};
    boxes.forEach(function(box) { state[box.dataset.actionIndex] = box.checked; box.closest(".action-item").classList.toggle("done", box.checked); });
    localStorage.setItem(progressKey, JSON.stringify(state));
    $("actionProgress").textContent = boxes.filter(function(box) { return box.checked; }).length + "/" + boxes.length;
  }
  document.querySelectorAll("[data-action-index]").forEach(function(box) { box.addEventListener("change", updateProgress); });
  updateProgress();
}

function renderPublication(publication) {
  const delivery = publication && publication.delivery;
  const report = publication && (publication.report || (delivery && delivery.content && delivery.content.care_guide));
  const guide = report && report.guide;
  $("emptyState").hidden = true;
  $("patientContent").hidden = true;
  $("modeBanner").hidden = true;
  if (!guide) { $("emptyState").hidden = false; return; }
  $("patientContent").hidden = false;
  const demo = publication.status === "demo";
  const preview = publication.status !== "published";
  $("modeBanner").hidden = false;
  $("modeBanner").className = "mode-banner" + (demo ? " demo" : preview ? "" : " published");
  $("modeBanner").textContent = demo ? "界面演示数据 · 不代表真实患者或医疗结论" : preview ? "当前为内部预览，尚未正式发送给用户" : "本指引已完成发送前安全审核";
  $("reviewBadge").textContent = demo ? "演示数据" : preview ? "内部预览" : "已安全审核";
  $("openingTitle").textContent = guide.opening.title;
  $("openingContent").textContent = guide.opening.content;
  $("diseaseTopic").textContent = "主题 · " + guide.disease_topic;
  $("caseLabel").textContent = "关怀编号 · " + maskCaseId(publication.case_id || report.case_id);
  $("publishedAt").textContent = demo ? "仅供界面演示" : preview ? "尚未发布" : "发布于 · " + displayDate(publication.published_at);
  [["diagnosis", guide.diagnosis_explanation], ["treatment", guide.treatment_review], ["cost", guide.cost_explanation]].forEach(function(pair) {
    $(pair[0] + "Title").textContent = pair[1].title;
    $(pair[0] + "Content").textContent = pair[1].content;
  });
  $("supportTitle").textContent = guide.psychological_support.title;
  $("supportContent").textContent = guide.psychological_support.content;
  renderActionList(guide.recovery_plan, report.case_id);
  $("followupList").innerHTML = (guide.follow_up_plan || []).map(function(item) { return '<article class="followup-item"><strong>' + escapeHtml(item.action) + '</strong><span>' + escapeHtml(item.timing) + ' · ' + escapeHtml(item.reason) + '</span></article>'; }).join("");
  $("redFlagList").innerHTML = (guide.red_flags || []).map(function(item) { return "<li>" + escapeHtml(item) + "</li>"; }).join("");
  $("limitationList").innerHTML = (guide.limitations || []).map(function(item) { return "<li>" + escapeHtml(item) + "</li>"; }).join("");
  if (delivery && delivery.content) {
    const recommendations = delivery.content.recommendation || [];
    if (recommendations.length) {
      const reasons = recommendations.flatMap(function(reportItem) { return reportItem.recommendations || []; }).slice(0, 3).map(function(item) { return item.entity_id + "：" + (item.reasons || []).map(function(reason) { return reason.title; }).join("、"); });
      $("recommendationContent").textContent = reasons.length ? "本次有充分历史证据的候选包括：" + reasons.join("；") + "。推荐结果仅作就医选择参考。" : "当前没有达到证据门槛的机构候选。";
    } else if (delivery.content.recommendation_unavailable_reason) {
      $("recommendationContent").textContent = delivery.content.recommendation_unavailable_reason + " 我们不会仅凭一次病历给医院或医生排名。";
    }
  }
  if (preview) {
    Array.from($("feedbackForm").elements).forEach(function(element) { element.disabled = true; });
    $("feedbackStatus").textContent = "预览状态不接收反馈；审核发布后才会开放。";
  }
}

function queueFeedback(payload) {
  let queue = [];
  try { queue = JSON.parse(localStorage.getItem(LOCAL_FEEDBACK_KEY) || "[]"); } catch (error) { queue = []; }
  queue.push(payload);
  localStorage.setItem(LOCAL_FEEDBACK_KEY, JSON.stringify(queue));
}

async function submitFeedback(event, publication) {
  event.preventDefault();
  const helpfulInput = document.querySelector('input[name="helpful"]:checked');
  const clarity = Number($("clarityScore").value);
  const reassurance = Number($("reassuranceScore").value);
  const actionability = Number($("actionabilityScore").value);
  const helpful = helpfulInput.value === "true";
  const category = clarity <= 3 ? "not_clear" : actionability <= 3 ? "recovery_question" : helpful ? "clear" : "other";
  const payload = {
    target_type: "overall_service",
    rating: Math.round((clarity + reassurance + actionability) / 3),
    helpful: helpful,
    category: category,
    comment: $("unresolvedQuestion").value.trim(),
    clarity_score: clarity,
    reassurance_score: reassurance,
    actionability_score: actionability,
    consent_to_contact: $("consentToContact").checked
  };
  const button = $("submitFeedbackButton");
  button.disabled = true;
  button.textContent = "正在提交…";
  try {
    if (!publication.backend_url || !publication.delivery_id) throw new Error("正式触达后端未连接");
    const response = await fetch(publication.backend_url.replace(/\/$/, "") + "/api/deliveries/" + encodeURIComponent(publication.delivery_id) + "/feedback", { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(payload) });
    const data = await response.json().catch(function() { return null; });
    if (!response.ok) throw new Error(data && data.detail ? data.detail : "提交失败");
    $("feedbackStatus").textContent = "谢谢您的反馈，我们已经收到。反馈编号：" + data.feedback.feedback_id;
    $("feedbackForm").reset();
    $("questionCount").textContent = "0";
  } catch (error) {
    queueFeedback(Object.assign({ queued_at:new Date().toISOString() }, payload));
    $("feedbackStatus").textContent = "当前未连接本地服务，反馈已暂存在本浏览器，待工作人员同步。";
  } finally {
    button.disabled = false;
    button.textContent = "提交反馈";
  }
}

async function initializePatientPage() {
  let publication = null;
  try {
    publication = await readPublication();
    renderPublication(publication);
  } catch (error) {
    $("patientContent").hidden = true;
    $("modeBanner").hidden = false;
    $("modeBanner").textContent = "手机预览无法打开";
    $("emptyState").hidden = false;
    $("emptyState").querySelector("h1").textContent = "暂时无法查看这份指引";
    $("emptyState").querySelector("p").textContent = error.message + "。请确认手机与电脑连接同一网络，或重新生成二维码。";
  }
  $("unresolvedQuestion").addEventListener("input", function() { $("questionCount").textContent = String(this.value.length); });
  $("feedbackForm").addEventListener("submit", function(event) { submitFeedback(event, publication); });
}

initializePatientPage();
