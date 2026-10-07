# Competition Hardening Report

Date：2026-10-06。Phase D代码与工程回归完成；真实E2E **BLOCKED_BY_TEST_DATA**，无正式KR成绩声明。停止新增产品功能，保留Phase A–C闭环。

## 1. Scope

本轮处理真实性、安全误报、KR1口径、OCR证据一致性、跨病例状态和真实E2E准备。未添加病种、Agent、知识图谱、真实消息/App、反馈扩展；未改Quality Profile/Recommendation算法，未用合成数据填KR4，未开展KR2/3正式人工标注。

### 开发前的最新仓库核查

| 问题 | Phase A–C后状态 | 代码证据与本轮处理 |
|---|---|---|
| runBatch公式分数/假阶段 | Still Exists | [app.js](D:/yiliao/medical-track-task-4-2/web/dist/app.js)原runBatch使用90+取模、91+取模、sleep及写死批次；Option B禁用，删除假结果。 |
| count/18称完整率 | Still Exists | 原renderMetrics；删除公式，改为非空字段计数，真实字段confidence均值单独说明来源。 |
| 安全声明误报 | Still Exists | [care_engine.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/care_engine.py)原MEDICATION_CHANGE只搜关键词子串，命中必带的“不要据此自行停药…”；新增否定保护上下文。 |
| KR1跨实体/跨病例flatten，两套公式 | Still Exists | [evaluation.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/evaluation.py)原忽略case_id/list索引，前端comparableItems重复实现；统一重写为Python评测。 |
| evidence存在但未验证原文/绑定 | Still Exists | 原short只validate_case，long只文件检查；[long_case.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/long_case.py)有重复不可达elif；共同registry校验取代它。 |
| 重新结构化后后台旧Pain/Care/画像 | Already Fixed | [storage.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/storage.py)save_structured/save_pain已清下游/portfolio，未重复改写。 |
| 改内容沿用旧审批 | Already Fixed | Phase C delivery_input_snapshot/save_review/send_delivery已用fingerprint及事务再次核对，保留。 |
| 前端切病例/迟到回写 | Still Exists | 原caseId change只清部分状态；Pain/Care及legacy audit仍可残留，直接fetch无请求代次保护；补集中reset和身份/代次检查。 |
| 补建画像/触达/反馈模块 | No Longer Relevant | Phase B/C已有真实持久化闭环，本轮不重建、不扩展。 |
| 真实图片→LLM→care可复现 | Still Exists | 未发现当前授权测试图片；历史缓存或docs声称42页不构成本轮已执行证据，明确blocked。 |

## 2. Fake / Mock Audit

[app.js](D:/yiliao/medical-track-task-4-2/web/dist/app.js)：runBatch/loadBatchFolder/exportBatch全部返回禁用说明，初始batchCases=[]，不再有固定完成率、confidence、复核数、状态循环、延时假处理或可导出假批次。HTML未加载JS时按钮也disabled，页面显示 **Batch processing is not enabled in this competition prototype.** 未接真实Batch，未声称已完成批处理。

runPipeline移除假阶段动画及离线本地模板成功路径；真实上传必须有后端和案件。按钮名改“运行OCR与结构化”，不再把第一问称全部功能已运行。未执行AI时固定样例明确标记，演示流程不刷出真实完成状态。前端不再暗示PDF可处理，不扩展PDF支持。

合法保留：

- demoCase/demoDocuments：固定UI示例，caseStatus及说明明确“未执行AI/非正式数据”；均值置信度不展示为真实统计。样例内具体证据/分数是示例内容，不能作gold或比赛成绩。
- mock_web_inbox：Phase C合法模拟渠道，AUTO/HUMAN_APPROVED和mock标记保留，记录真实持久化。
- tests的synthetic_test fixtures：只验证工程边界；不纳入默认库或正式KR4。默认cases仍0，没有伪造历史数据。
- 固定rule/quality权重：既有算法参数，不是随机或硬编码病例结果；算法未改。

展示指标来源：非空字段数来自当前结构化结果；字段confidence均值只来自字段原始confidence且未校准；OCR来自block confidence；pain confidence来自明确展示的规则/模型method；evidence_coverage是引用字段可定位比例，非事实准确率；品质coverage来自保存记录/可计算维度；KR1只从Python评价独立导入的prediction/gold。无正式pain agreement/warmth成绩被编造。空gold返回NOT_EVALUABLE；无应有关键字段时missing_rate=null，页面显示“—”。

## 3. Safety Fix

位置：[care_engine.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/care_engine.py)，unsafe_medication_instruction、validate_care_guide、finalize_care_guide。

修复前：regex搜“自行停药”子串，不区分“不要自行停药”，连系统强制免责声明都可BLOCKED；直接“把剂量增加一倍”等又可能漏检。

修复后：精确移除紧邻药物动作的禁止语境（不要/请勿/切勿/不得/不可/不能/禁止，含据此、自行等及顿号/或串列），再按分句检测剩余药物指令和绕过医生表述；仅处理明确“如需调整…请咨询医生”的条件咨询句。禁止句不屏蔽整段其他危险指令；“不要自行停药，但可以换药”仍BLOCK。未删停药/剂量关键词，未新增NLP模型。action.timing也纳入安全检查。

PASS覆盖用户5句、完整必带免责声明、条件咨询和非用药的“逐步增加活动量”；BLOCK覆盖用户5句，以及立即停药、自己换药、无需医生、保护句后另加危险指令。测试将**真实validate_care_guide产生的检查**接入Phase C Gate：安全声明不BLOCKED，危险指令BLOCKED，审核/发送均409。

修复误报后发现fallback原本依赖错误BLOCKED才review_required；已明确rules_fallback或warnings需review。不是所有病例强制审核；原低风险免审测试继续通过。Phase C阻断/审批算法未放宽。

边界：规则覆盖当前明确词法模式，不能证明所有语言变体/事实医学合理性；仍需成员C对真实输出复核。原确定性诊断模式保留，未宣称全面安全模型完成。

## 4. KR1 Evaluation

唯一实现：[evaluation.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/evaluation.py)，evaluate；CLI保持 `python -m medical_claims.evaluation prediction.json gold.json`。网页runEvaluation通过[app.py](D:/yiliao/medical-track-task-4-2/web/backend/app.py)的POST /api/evaluation/structured调用同一函数，旧JS flatten/公式已删除。

流程：

```text
case_id独立对齐 → mutual unique entity matching → 同一实体字段比较
→ 每病例计数 → micro汇总 + macro平均 + per-field/per-case
```

- 必须有非空case_id；同一输入中重复ID拒绝。不同病例永不匹配，预测多余case计FP、gold缺案计FN。macro_f1为双方case_id并集上各病例F1的算术均值。
- diagnoses：规范化exact name或相同非空code；certainty、diagnosis_type属性另评分，不能只报诊断名正确。
- medications：规范化exact name；dose/frequency/route/duration等在该药内比较，不用于“优化”匹配。
- procedures：exact name，并在双方有日期/body_site/is_historical时要求一致；明显不同日期/目标不匹配。
- examinations：exact name + 双方存在的date/category；results按item_name在该检查内匹配。
- hospitalization字段独立；cost总额/各财务对象字段独立，breakdown按category绑定amount。
- 候选必须双向唯一；重复名称无可靠区分时算mismatch，不靠排序/剂量找最优得分。标注集需人工消除歧义，不使用Hungarian或医学本体。
- 空白/常见标点、全量数字表示以及qd/bid/tid等已有频次alias统一；不做模糊语义匹配。evidence/confidence/status、entity IDs、events/quality flags不混入抽取事实F1；events另需时间线验收。

正式输出：micro_precision、micro_recall、micro_f1、macro_f1、per_field_metrics、per_case_metrics、missing_rate、hallucination_rate、原兼容precision/recall/f1、分子分母计数、competition_key_fields、evaluator_version=kr1-entity-aligned-v2、evaluation_status。

指标定义：字段TP为已匹配实体内同一路径规范化值一致；FP=预测字段-TP，FN=gold字段-TP；micro P/R/F1汇总计数后计算。缺字段和错值都会降recall，但只缺字段进入Missing Rate。

**关键字段口径**（COMPETITION_KEY_FIELDS）：diagnoses.name/code；procedures.name/performed_date/body_site；medications.name/dose/frequency/route/duration；hospitalization.admission_date/discharge_date/length_of_stay_days；costs内Schema的非空FieldValue，包括总额/明细、支付/负担/比例。只在gold明确非空且非not_applicable/not_present时作为应有字段。department、药品其他说明、examinations、诊断certainty/type等仍计F1但不计这版关键缺失分母。

```text
missing_rate = 缺失的gold关键字段数 / 应有的gold关键字段数
```

匹配实体内错误但非空的dose不算缺失；未匹配/遗漏实体的gold关键字段都缺失。原值null不能靠非空normalized_value算“存在”。分母0时null，不用0%伪装达标。这是明确的项目KR1操作定义，正式专家gold需按同一合同核验。

**内部辅助Hallucination Rate**=`(预测字段数 - 正确字段数)/预测字段数`：包含新增实体/字段和不支持的错值FP；不是纯新增实体比例，也不是主办方官方指标，字段FP不等同医学幻觉定论。无预测字段时约定0并同时报告分母0。

剂量/频次交换fixture F1=4/6而非1、跨案全交换F1=0、缺药降低R/提高missing、额外药降低P/提高内部hallucination、手术日期/目标不符扣分、总额对但费用串category仅.6、诊断属性错仅1/3。上述都是回归验证，**没有真实gold便不能证明F1≥.90**。

## 5. Evidence Grounding

共同函数：[evidence_grounding.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/evidence_grounding.py)，build_ocr_evidence_registry / verify_case_evidence / apply_evidence_grounding。registry以(document_id,page)唯一定位full_text/blocks，重复来源拒绝。

验证：document存在、明确page存在、非空evidence.text经NFKC/大小写/空白和有限标点规范化后在该页出现；bbox必须精确对应含该原文的实际OCR block。诊断/手术/药物FieldValue需包含实体名，或可定位于包含当前实体名的同一block。药物字段原值需在引用text中出现，并可与该药名在无其他药名的同一行/block绑定；防止Drug A剂量借Drug B，或整页长引用掩盖串药。

失败保留value/confidence/evidence，extracted→review_required，附“Evidence verification failed”字段warning和案件QualityFlag；既有conflict/derived不强制覆盖。派生值不当成逐字原文提取，但已有证据仍检查。事件evidence也经registry校验；事件缺证据产生warning。

[app.py](D:/yiliao/medical-track-task-4-2/web/backend/app.py)短病例finalize及统一wrapper调用共同函数；长病例merge完整性和最终wrapper使用同一核心，旧重复elif及重复field遍历实现删除。对字段引用路径，care_engine不再把review_required/conflict值当已通过定位。Phase C按字段状态/质量标记进入复核，不删字段或改Gate规则。

边界：是来源/文本/明显实体绑定检查，不是语义entailment。OCR表格分行、别名、复杂用药跨行记录可保守触发review；需要人工核对。不是把“证据对象存在”或“原文含字符串”当全面事实正确。

## 6. Cross-case Isolation

[app.js](D:/yiliao/medical-track-task-4-2/web/dist/app.js)：resetCaseState清workflow/文档/result/Pain/Care/legacy quality audit/portfolio/推荐/上下文/coverage/gate/inbox/feedback/task/选择ID及病种输入；caseId change立即执行，创建/恢复/载入demo也执行。渲染不能把旧病例case_id改写成新ID；Pain/Care按当前ID拒绝旧报告。

caseGeneration给请求绑定代次，workflowFetch和直接OCR/结构化/Pain/Care/audit响应在赋值前核对；A→B→A也拒绝第一次A的迟到响应。旧请求catch不能给新案写PROVIDER_ERROR，finally不能刷新新案。当前case_id一致也不代替代次保护。

restore/refresh、推荐/上下文、gate、delivery列表/feedback/tasks核对返回case_id，mismatch拒绝展示；send/feedback返回也核对当前案件。Phase A–C后端失效、require_current、fingerprint及事务检查保持不变。

[frontend_hardening.test.cjs](D:/yiliao/medical-track-task-4-2/tests/frontend_hardening.test.cjs)实际执行mock DOM/API回归：状态清空、Pain/Care/OCR/重结构化迟到回写、错案返回、A→B→A、收件箱错案及Python评测接线。**不是实际浏览器E2E**。

## 7. Real E2E

状态：**BLOCKED_BY_TEST_DATA**。当前仓库未发现授权可用医疗图片；未调用OCR/DeepSeek，未猜latency/token，未用同案分块JSON或历史42页描述冒充本轮验证。

已执行输入检查，输出[run_metadata.json](D:/yiliao/medical-track-task-4-2/outputs/e2e/20261006T114819206124Z/run_metadata.json)：case_id=null、external_llm_called=false、token_usage=null、latencies为空。

新增验证工具[run_real_e2e.py](D:/yiliao/medical-track-task-4-2/scripts/run_real_e2e.py)，复用真实后端函数、同一SQLite、分阶段计时、实际usage、重开核对，禁止缓存替代真实结构化；不导入KR4历史或发送消息。准备要求、命令、状态/输出合同见[REAL_E2E_CHECKLIST.md](D:/yiliao/medical-track-task-4-2/REAL_E2E_CHECKLIST.md)。脚本真实联网分支尚未有数据可执行，不声称已验证其全程成功。

## 8. Regression Tests

Python：**175 passed / 0 failed**（原Phase A–C120项 + 本轮55项），1条第三方Starlette/httpx弃用warning。[test_competition_hardening.py](D:/yiliao/medical-track-task-4-2/tests/test_competition_hardening.py)覆盖安全、真实validation→Gate、KR1实体/病例、bbox/原文/页码/串药、short/long统一、禁用batch、空gold及E2E缺数据。不把fixtures当比赛gold。

前端：**25项mock DOM/API检查通过，0失败**（含JS初始化/语法、迟到gate及重新导入gold后旧评测回写保护）；本轮通过内置Node runtime加载同一测试脚本执行，不是本机PowerShell的node命令。安装有Node的环境可重复运行：

```text
node tests/frontend_hardening.test.cjs
```

Python完整命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Phase A–C原120项保持通过。修复过程中首次全量曾因fallback review_required暴露1项失败，已修复，未修改旧测试来掩盖问题。仅剩第三方Starlette/httpx弃用warning，不是失败。

## 9. Remaining Critical Issues

1. **真实E2E与现场浏览器验收未执行**：需授权脱敏图片和有效配置；先按Checklist真实运行，检查OCR数字/日期/药名、LLM是否降级、风险理由和重读。禁止把blocked改为演示成功。
2. **KR1正式数据与成绩缺证据**：新评测口径已正确接线，但独立专家gold、可测样本及真实F1/.05缺失阈值尚未验证；不能对外宣称达标。
3. **KR2/KR3正式人工验证仍缺**：专家一致率和盲评温度/行动性未由本轮fixture证明；按计划后续由成员C组织，本轮未做标注。
4. **KR4真实历史覆盖仍NOT_READY**：仍缺三病种/每病种≥5家确认机构及足够可计算维度；继续明确拒绝推荐或care-only，不填合成历史。
5. **医学事实与规则边界须实案复核**：registry不等于语义支持，规则不等于临床安全模型；医学成员需复核真实输出及保守evidence warnings，核验原图确已授权脱敏再外发文本。

Batch禁用是本轮合法范围选择，不是必须补建的新模块。Phase D到此停止，没有开展下一阶段功能扩展。
