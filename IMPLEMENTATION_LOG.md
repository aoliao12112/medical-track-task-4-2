# Implementation Log

## 2026-10-06 — Phase A: Minimal Case Workflow

目标：保留现有前三问核心算法，用同一个安全 UUID 案件关联 OCR、结构化（含事件）、痛点、温度指引，并在 SQLite 保存和恢复。仅完成 Phase A，不继续开发第四问、推荐、审核、触达或反馈。

### Files

新增：

- `src/medical_claims/workflow_models.py`
- `src/medical_claims/storage.py`
- `tests/test_competition_workflow.py`
- `IMPLEMENTATION_LOG.md`

修改：

- `web/backend/app.py`
- `web/dist/app.js`
- `web/dist/index.html`
- `PROJECT_COMPLETION_MAP.md`（末尾 Implementation Progress / Phase A）

### Storage and API

默认 SQLite：`outputs/competition.db`（Git ignored，已创建空库）。可设置 `COMPETITION_DB_PATH`。仅 cases / case_results 两张表，无版本表、复杂谱系、事件溯源。

新增：

- `POST /api/workflow/cases`（HTTP 201）
- `GET /api/workflow/cases/{case_id}`
- `GET /api/workflow/cases/{case_id}/result`

创建请求示例：

```json
{
  "claim_event_id": "claim-demo-001",
  "data_source": "official_deidentified_dataset",
  "consent_status": "authorized",
  "deidentification_status": "confirmed"
}
```

上述值必须由调用方根据实际数据来源和授权情况填写。它们是声明字段，不是系统已验证的合规结论。

后端生成 UUID，避免姓名、身份证、手机号作为案件 ID。同一个 claim_event_id 的重复创建目前产生不同案件，未做事件去重。

现有处理 API 保留医疗输入/输出 Schema，但**现在必须先创建案件**：上传 form 的 case_id、OCR 修正的 case_id、痛点/指引的 case.case_id 使用创建返回的 UUID。任意旧 ID 不会自动创建，返回 404。

### Minimal Usage

1. 重启后端以加载新接口；刷新页面以加载 `app.js?v=20261006-minimal-case-workflow`。
2. 在原工作台配置本地后端。在 Current Case 中按实际情况选择授权/脱敏声明，点击“创建新案件”。
3. 上传图片，点击现有“运行完整流程”按钮完成 OCR 和结构化；此按钮仍沿用原来的第一问行为，不隐式替代第二问/第三问操作。
4. 在“痛点洞察”点击“分析当前病例”；再在“温度指引”点击“生成温度指引”。
5. Current Case 显示后端已保存状态和 Ready / Not Ready。刷新后使用最近案件 ID 自动恢复；也可在案件编号输入 UUID 并点击“读取案件 ID”。
6. 修改 OCR 后重新结构化成功会清空旧痛点和指引，需要重新生成。重新分析痛点也会清空旧指引。

API 快速创建（PowerShell）：

```powershell
$claimPayload = @{
    claim_event_id = 'claim-demo-001'
    data_source = 'official_deidentified_dataset'
    consent_status = 'authorized'
    deidentification_status = 'confirmed'
} | ConvertTo-Json
$createdCase = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/workflow/cases' -ContentType 'application/json' -Body $claimPayload
$createdCase.case_id
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$($createdCase.case_id)"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$($createdCase.case_id)/result"
```

### Workflow Semantics

`RECEIVED → PROCESSING → STRUCTURED → PAIN_READY → CARE_READY`

- OCR 成功先保存，即使之后结构化失败也能读取 OCR。
- 新 structured_result 保存事务会清空 pain_result / care_result，状态回到 STRUCTURED。
- 可捕获的 OCR/结构化失败回到最后成功保存的结果阶段；没有新增 FAILED 状态。
- 生成前和保存时核对当前病例/痛点输入，拒绝过时写回（409），无复杂版本系统。
- CARE_READY 包括原有明确标识的降级指引；是存储状态，不等于医学审核通过或比赛指标达标。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short
```

结果：**66 passed**，本轮 14 项、原有 52 项。第三方 TestClient 有一条弃用提示。首次沙箱内运行因 Windows 临时目录权限失败，经批准在沙箱外运行通过；没有为此修改项目运行逻辑或依赖。

新增测试覆盖创建、保存各结果、PROCESSING、重开 storage、重新结构化清空下游、404、409、错误保留 OCR、痛点重跑清空指引，以及 API 串联到 CARE_READY 后读取。接口串联中的 OCR/结构化使用显式替身，痛点用实际规则，指引用原有降级生成，绝不把此测试当作真实病例 E2E。

前端 JavaScript 语法、初始化、创建/恢复在模拟 DOM/API 的 Node 环境验证通过。未执行真实浏览器 E2E。

**Real E2E not executed**：当前仓库 examples/work/outputs 等可访问目录未找到可用图片病例；长病例分块 JSON 缓存不是图片上传证据。本轮未调用外部 DeepSeek，也未将医疗内容外发。

默认库已初始化，测试病例仅存在于临时测试库。未使用模拟结果填充正式库。

### Remaining Limitations

- 原图不持久化；恢复后能查看 OCR、字段、事件、痛点、指引，但不能自动恢复原图。
- 元数据声明不替代真实授权和脱敏核验；没有用户身份、权限、数据库加密、删除/留存政策接口。
- 没有事件去重、案件列表、后台处理队列或进程强杀后的 PROCESSING 恢复功能。
- 新材料结构化失败时保留旧成功结果与最新 OCR；不得把旧结果当作新材料分析成功。
- 旧脚本/调用者若使用自行编写的 case_id，需先调用创建接口并传入返回 ID。本轮未修改其他脚本。
- 未开发画像、推荐、人工审核、触达、反馈或反馈回流；未修 F1、安全正则、fake batch、提示词、PDF 或 UI 风格。

状态：Phase A 实现及本地测试完成；真实 OCR → DeepSeek → Care Guide → SQLite 验证待具备授权测试图片后执行。停在 Phase A。

## 2026-10-06 — Phase B: Quality Profile & Recommendation Integration

状态：接线实现完成，**87 tests passed / 0 failed**；正式 KR4 **NOT_READY**，真实历史数据 0。本轮停止在 Phase B，不进入审核/触达/反馈。

### Existing Fourth-Question Capability

重新检查了 `quality_models.py`、`quality_engine.py`、`provider_extraction.py` 与原构建脚本：医院/医生/治疗模式均有 grouping；同病种比较；默认主体3样本；六维 calculated / insufficient_sample / data_unavailable；>=3可计算维度才能生成加权综合分；综合置信度>=.33；推荐理由至少两条来自 calculated 且非 data_quality 维度；不满足时拒绝。原引擎和 Schema 均未改写。

六维既有算法：费用反向百分位（优先日均费用）；有证据用药痛点扣分；记录要素完整度代理；100×(1-30日再入院率)；反馈均值/5×100；字段置信度减质量标记惩罚。缺少稳定 token/再入院标签及反馈时不可用，不能默认100。

查到原用药评分存在“有报告且未发现痛点则起评100”的路径。本轮 adapter 没有具体有证据的用药风险信号时不向画像传入该维度的报告，返回 unavailable；源报告在输入 JSON 中保留。未修改用药评分算法，没有宣称空痛点是治疗优秀。

### Files

新增5个文件：

- `src/medical_claims/profile_records.py`
- `src/medical_claims/quality_integration.py`
- `src/medical_claims/quality_coverage.py`
- `scripts/check_competition_coverage.py`
- `tests/test_quality_integration.py`

修改7个文件：

- `src/medical_claims/storage.py`
- `web/backend/app.py`
- `scripts/build_quality_profiles.py`
- `web/dist/app.js`
- `web/dist/index.html`
- `PROJECT_COMPLETION_MAP.md`
- `IMPLEMENTATION_LOG.md`

OCR、结构化、Pain Engine、Care Guide prompt/生成核心、F1、安全正则和 fake batch 均未修改。

### Assembly Contract

`build_profile_case_record(ProfileRecordInput) → ProfileAssembly`，可用时包含既有 `ProfileCaseRecord`；缺主病种/医院返回 record=null 和明确原因。

输入历史 JSON 外层为：

```text
{
  records: [
    {
      case: 原 ClaimCase,
      pain_report: 原 PainPointReport 或 null,
      provider: {
        context: 原 CaseProviderContext,
        institution_confirmed: true/false,
        confirmed_clinician_ids: [已确认的来源ID]
      },
      data_source: 数据来源声明,
      stable_patient_token: 已有稳定脱敏token或null,
      readmitted_within_30_days: 已有真实标签或null,
      patient_feedback_score: 已有真实评分或null
    }
  ]
}
```

不是把 raw ProfileCaseRecord 中填一个医院名就能进入主流程。机构/医生使用原 audit 的 HOSP-/DOC-12位十六进制伪匿名 ID、来源定位、置信度和角色。医院明确确认且无冲突才进入比较；医生必须明确确认且为主治/经治/手术角色，不接受报告/检查医生充当经治医生。

病种遵循既有 fracture/pneumonia/diabetes 命名，解析当前确认 primary 或首个可靠 discharge，要求 extracted、高置信度、证据；历史/疑似/未知病种拒绝，不使用 OCR 候选或 LLM 猜分类。

provider候选复用 `build_safe_context` 与既有 audit 私有 HMAC key；API不返回真实名称映射，也不自动把候选当已确认。人工核对来源仅是数据身份声明，不是新增指引医生审批流程。

### SQLite & API

仍使用 `outputs/competition.db`（或 COMPETITION_DB_PATH）。增加 `case_profile_contexts` / `profile_case_records` / `quality_portfolios` 三张 JSON 表，按案件 upsert、一个 current portfolio，无版本谱系。

新增：

- `POST /api/quality/records/import`
- `GET /api/quality/portfolio`
- `GET /api/quality/coverage`
- `GET /api/workflow/cases/{case_id}/quality-context`
- `POST /api/workflow/cases/{case_id}/quality-context`
- `GET /api/workflow/cases/{case_id}/recommendations`

复用 `POST /api/quality/profiles/build`，请求 `{use_saved_records:true, minimum_sample_size:3}` 使用确认后入库数据构建并持久化。原 raw-record build 和自行提供 portfolio 的 recommendation 路由保留为 PREVIEW ONLY，不修改正式库、不纳入 coverage。

当前案件 POST context 的 body 为 `{provider:{context, institution_confirmed, confirmed_clinician_ids}}`，可附已有 token/再入院/反馈标签；这些是原画像需要的历史结局输入，不实现本项目新的用户反馈收集。

当前已保存病例 + 痛点自动组装，只有 Phase A 已声明 authorized / confirmed 的案件能入画像库。重新结构化清除来源确认、当前画像和旧 portfolio；重跑痛点清除当前画像和旧 portfolio，构建时从当前结果重组。重复导入不扩大样本，历史导入不覆盖已登记工作流案件。

当前 recommendation GET 从 case_id 读取病种、确认上下文、已保存 portfolio，并调用原 `recommend_profiles` 三次；要求前三问完成到 CARE_READY。缺病种/确认来源/数据或门槛时返回具体拒绝原因，not_evaluated 与 insufficient_evidence 区分。

### Frontend Usage

重启后端、刷新新 `app.js?v=20261006-quality-integration`。在既有工作台创建授权脱敏案件，完成前三问，进入“品质推荐”：

1. “读取画像与推荐”读取当前 case_id、disease_group、安全候选及保存数据。
2. 对已有核验资料确认医院和合格医生来源，或导入已确认的安全上下文 JSON。
3. 根据同一合同导入真实历史病例 JSON；测试数据必须标记 synthetic_test，页面明确标识。
4. “构建已保存历史画像”，复用原引擎。原先的机构审计仍保留，但其候选数不当正式覆盖。
5. 查看医院/医生/治疗模式六维样本、公式与不可用原因；查看三类推荐的两理由、依据、分数、置信度与样本。缺数据不显示空排行榜或虚假0/100。

刷新后读取 SQLite portfolio，不靠内存保留。没有 UI 美化或改版。

### CLI & KR4 Audit

```powershell
.\.venv\Scripts\python.exe scripts/build_quality_profiles.py --from-storage
.\.venv\Scripts\python.exe scripts/check_competition_coverage.py
```

可通过 `--database` 指定原 SQLite；旧脚本 `input output` 预览用法保留。真实默认库已经运行两条命令：持久化一个0病例/0画像的空 portfolio，未补合成数据。导出位于 `outputs/quality_profiles/` 和 `outputs/kr4-coverage.json`，均 Git ignored。

当前结果：

| Disease | Institutions | Calculated dimensions | Qualified institutions | Status |
|---|---:|---:|---:|---|
| fracture | 0/5 | 0/5 | 0/5 | NOT_READY |
| pneumonia | 0/5 | 0/5 | 0/5 | NOT_READY |
| diabetes | 0/5 | 0/5 | 0/5 | NOT_READY |

已接受真实历史病例0、病种0、医生画像0、治疗模式画像0。仅有7份同一案件的分块缓存，不足以确认完整病例、机构、痛点与结局，不作为历史画像记录。

coverage 检查样本、>=5可计算维度、置信度、两个来自实际维度的推荐理由，每病种>=5家合格机构，并验证医生/治疗模式画像可用。就绪只称 READY_FOR_REVIEW，formal_kr4_pass 固定不作证明；不能因为代码存在或 synthetic_test 达到计数而显示 PASS。

### Verification

全部 **87 passed / 0 failed**，其中 Phase B **21** 项、先前 **66** 项。第三方 TestClient 有一条弃用提示。

smoke 用 synthetic_test fixtures、临时 SQLite 实际运行：

```text
ClaimCase + PainPointReport + confirmed provider
→ import API / assembler
→ ProfileCaseRecord
→ existing build_quality_portfolio
→ persisted portfolio
→ current case recommendations API
→ existing recommend_profiles（3 entity types）
→ reopened storage / reload
```

画像及推荐引擎未 mock。验证样本不足拒绝、未确认当前来源即使有历史也拒绝、医生角色过滤、无反馈/再入院/用药信号为 unavailable、理由对应计算维度、重复导入、画像失效重建、重开读取、来源错案/不存在案/未授权声明拒绝。

3×5×3 的完整 synthetic fixture 只用于测试 coverage 分支，始终不能证明真实 KR4。没有把测试病例写入默认库。忽略目录 `work/phase_b_smoke/` 的 UI payload 也明确为 synthetic fixture。

前端在 Node 模拟 DOM/API 中验证语法、初始渲染、真实引擎 fixture 的理由/缺失维度/测试标签，以及 Available / Insufficient Evidence / Not Evaluated 三种状态；未执行真实浏览器 E2E。

本轮没有 OCR/DeepSeek 网络调用或真实医疗内容外发；真实图片到 Care Guide E2E 仍未执行。本轮用户要求的 fixture smoke 已执行。

### Missing Data & Limitations

- 正式 KR4 仍缺三个病种、每病种至少5家确认医院、合格经治医生、完整的可比较病例与治疗字段、对应痛点，以及至少使5维可计算的真实结局/反馈等输入。默认每主体3例通常需要45个独立主病种病例覆盖15个“病种—机构”组合，不能把分块当不同患者。
- 确认与数据来源仍为调用方声明，正式需要核验；本轮未建审核/授权验证系统。
- 费用未风险调整；规范性/数据质量为代理指标；治疗模式名称哈希不等于临床方案等级；用药未检出不默认优秀。
- 历史 JSON 导入逐条保存，后续记录冲突时已成功记录保留，按ID幂等重试；没有整批版本或事件溯源。
- 更改病例/来源/历史数据后需要重建；旧缓存失效，不显示过时推荐。
- 没有审核、触达、反馈收集、反馈回流，未做比赛数值指标修复。完成 Phase B 后停止。

## 2026-10-06 — Phase C: Risk-based Delivery & Feedback Loop

### Result / Scope

实现持久化 Care Guide + 真实推荐查询 → Risk Gate → 免审或按需人工复核 → mock_web_inbox → Feedback → OPEN task → 人工 resolution → RESOLVED。只完成 Phase C；不改 OCR、结构化/pain/care 核心、prompts、quality/recommendation 算法、KR1/2/3评测、fake batch、PDF，也没有真实短信/企微、账号体系、自动训练、多智能体或UI重做。

### Added / Changed Files

新增：

- [delivery_gate.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/delivery_gate.py)：evaluate_delivery_gate，三分支、已有信号、.8/.5/3门槛。
- [delivery.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/delivery.py)：prepare_delivery_content、DeliveryRecord、AUTO/HUMAN_APPROVED、当前内容fingerprint、care-only。
- [review_models.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/review_models.py)：审核目标/决定/声明reviewer、必填fingerprint。
- [feedback_models.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/feedback_models.py)：Feedback、FollowUpTask、非空resolution。
- [test_delivery_feedback_loop.py](D:/yiliao/medical-track-task-4-2/tests/test_delivery_feedback_loop.py)：33项临时SQLite/API/风险闭环测试。

修改（包括复用尚未提交的 Phase A/B 文件）：

- [storage.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/storage.py)：同库4表、事务快照核对、审核/发送/反馈/待办存取、幂等与状态保护。
- [workflow_models.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/workflow_models.py)：追加9个闭环状态。
- [quality_integration.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/quality_integration.py)：已保存care且非PROCESSING的后续状态继续使用原推荐查询；算法不变。
- [app.py](D:/yiliao/medical-track-task-4-2/web/backend/app.py)：11个API、资源404、health risk_delivery_feedback_loop/revision。
- [app.js](D:/yiliao/medical-track-task-4-2/web/dist/app.js)、[index.html](D:/yiliao/medical-track-task-4-2/web/dist/index.html)：五个最小区块、按需审核、冻结收件箱、模式/理由/缺数据展示、反馈及解决待办。
- [PROJECT_COMPLETION_MAP.md](D:/yiliao/medical-track-task-4-2/PROJECT_COMPLETION_MAP.md)：追加完整 Phase C 规则、API、存储、验收和完整度。
- [IMPLEMENTATION_LOG.md](D:/yiliao/medical-track-task-4-2/IMPLEMENTATION_LOG.md)：本节。

### Policy / Storage

BLOCKED 只消费已有安全结果，不修regex、不给人工绕过。REVIEW_REQUIRED 消费 review_required/fallback、覆盖<.8、无检查/review检查、未审核知识、care warnings、quality warning/error、字段冲突、用药及high/critical痛点；available推荐检查confidence<.5、sample<3、warnings和至少两条带字段证据理由。无上述信号且安全通过才AUTO；通用limitations不一律强制人工。

仅需审核内容允许APPROVED/REJECTED，目标care/recommendation/combined；最新相关拒绝阻止发送，批准必须覆盖风险目标。当前内容指纹绑定审核与UI发送；变更内容/来源/portfolio使旧批准失效；已发送内容禁止事后审核改写。send在事务内再次校验，冻结实际报告，重复同内容幂等。

沿用competition.db，新增reviews、deliveries、feedback、follow_up_tasks。delivery包含mock_web_inbox、AUTO/HUMAN_APPROVED及完整payload；feedback严格1–5评分+bool helpful+8类category；`rating<=3 OR !helpful OR category!=clear` 创建OPEN。resolve非空且不可覆盖，全部OPEN解决才RESOLVED；旧反馈不覆盖新处理阶段，正向反馈不抹掉待办。

推荐不足不是错误：不发假推荐，仍可按关怀自身gate走care-only，并展示明确缺数据说明。反馈是服务复核，不自动修改prompt、训练、推荐权重，也不冒充医院patient_feedback_score。

### APIs / Validation

新增：POST case delivery/prepare、delivery/send；GET case deliveries、delivery单条；POST/GET case reviews；POST delivery feedback、GET case feedback；GET全局/当前case follow-ups、POST follow-up resolve。具体完整route和函数见完成度地图Phase C表。

全量运行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short`：**120 passed / 0 failed**（原87 + 新33），1个既有第三方Starlette/httpx deprecation warning。Windows测试临时目录限制下使用已授权测试命令在沙箱外执行。

Smoke A：low-risk信号fixture → AUTO → 无审核 → mock inbox；B：review_required → send409 → APPROVED → HUMAN_APPROVED；C：blocked → 审核409、send409（包括旧批准）；D：rating2/helpfulfalse/cost_question → OPEN → resolution → RESOLVED，重开storage读取delivery/feedback/task全部保留。

CareGuideReport的LLM/pass字段是明确synthetic_test fixture，不是医疗生成或临床效果验证。所有测试用临时SQLite，不污染默认库；默认cases/reviews/deliveries/feedback/tasks均0。未调用OCR/DeepSeek外发或修改正式KR4数据。

前端Node模拟DOM/API验证语法、初始渲染、8种真实API合成载荷状态、免审发送、人工审核后的模式、fingerprint提交、反馈收据及resolve接线；**不是实际浏览器E2E**。`work/phase_c_smoke/`是忽略的本地合成载荷，不是正式数据。

### Engineering Completion / Remaining Limitations

按原15模块权重重估：Workflow90、HumanReview85、Delivery95、Feedback95、Loop90；包含Phase B质量80/推荐85集成，其他核心估计保持。整体**80.50%≈81%功能工程完整度**，不是比赛分数、医学准确率或KR成绩。

剩余：现有真实生成可能常需review；safety误报原样保持BLOCKED；证据覆盖不等于事实grounding；审核者/授权/脱敏仍为声明，无账户隔离/删除留存；仅模拟渠道及人工resolution记录；无真实浏览器/真实图片LLM E2E；正式KR4仍0，KR1/2/3缺独立正式验证。上述问题等待下一轮，Phase C完成后停止。

# Phase D — Competition Hardening

Date：2026-10-06。完成代码加固及回归；真实E2E **BLOCKED_BY_TEST_DATA**。没有添加产品模块，Phase A–C闭环及原质量/推荐算法保留。本轮完成后停止。

## Recheck Before Changes

重读A/B/C日志并核对最新代码：Still Exists为runBatch取模假分/状态、count/18完整率、停药否定误报、Python/JS flatten和1-recall缺失口径、short/long证据差异/重复elif、前端切案及迟到fetch、真实图片缺失。Already Fixed为后端下游失效、require_current及Phase C fingerprint/事务旧审批保护，不重复实现。No Longer Relevant为画像/触达/反馈模块缺失，不再补产品功能。

## Files / Changes

新增：

- [evidence_grounding.py](D:/yiliao/medical-track-task-4-2/src/medical_claims/evidence_grounding.py)：唯一OCR registry核心及字段/事件原文、page、bbox、实体绑定验证。
- [test_competition_hardening.py](D:/yiliao/medical-track-task-4-2/tests/test_competition_hardening.py)：安全、真实validation→Gate、KR1、证据short/long、Batch、空gold、E2E阻塞回归。
- [frontend_hardening.test.cjs](D:/yiliao/medical-track-task-4-2/tests/frontend_hardening.test.cjs)：可重复mock DOM/API前端回归；不是浏览器E2E。
- [run_real_e2e.py](D:/yiliao/medical-track-task-4-2/scripts/run_real_e2e.py)：仅验证工具，真实函数、分阶段计时、usage、SQLite重读，缺数据/config明确阻塞。
- [COMPETITION_HARDENING_REPORT.md](D:/yiliao/medical-track-task-4-2/COMPETITION_HARDENING_REPORT.md)：9节加固报告。
- [REAL_E2E_CHECKLIST.md](D:/yiliao/medical-track-task-4-2/REAL_E2E_CHECKLIST.md)：授权脱敏图片及运行/验收清单。

修改：care_engine.py、evaluation.py、long_case.py、web/backend/app.py、web/dist/app.js、web/dist/index.html、README.md，以及本日志/PROJECT_COMPLETION_MAP.md。未改quality_engine/pain_engine、OCR算法、care prompt、KR2/3人评、原画像推荐算法或反馈功能。

Batch采用Option B：功能禁用、无默认假结果、无假分/进度导出，HTML初始也disabled。移除本地模板/阶段动画冒充上传成功；按钮明确OCR与结构化，固定样例标“未执行AI”，PDF提示改未启用而非扩展支持。字段计数/原始confidence来源说明，KR1网页只调用唯一Python实现。

Safety仅移除紧邻药物动作的禁止语境，分句检测实际用药改变/绕过医生；保护语不遮蔽其他危险语。action.timing纳入检查。首次全量发现修复误BLOCK后fallback的review_required变false，明确rules_fallback/warnings需review恢复原语义，没有把所有真实病例强制审核、没有改旧测试掩盖问题。BLOCKED仍不能人工批准绕过。

KR1按case_id→双向唯一实体候选→字段匹配：药物name内比属性，手术name/date/body_site/历史语义、检查name/date/category及结果、诊断属性、住院字段、cost breakdown绑定。case重复或无ID拒绝，歧义匹配保守mismatch。micro/macro/per-field/per-case、独立关键缺失和辅助FP比例；空gold NOT_EVALUABLE，关键分母0为null。具体关键字段合同见加固报告与COMPETITION_KEY_FIELDS，不再1-recall。

Evidence核验doc/page/text/bbox和实体绑定，药品名/原值需同一无其他药名的OCR行/block，防整页引用串药；失败保留值并标review/warning。short/long共用，旧不可达elif/重复遍历删除；care字段路径不把review/conflict当已确认。

前端集中reset所有案件产物和legacy audit，立即清空；request generation、case_id检查拒绝迟到/错案响应及A→B→A，旧catch不污染新案。已存在后端失效与fingerprint规则保持。

## Real E2E

递归搜索代码/示例/知识/测试目录与work/outputs无可用授权病例图片；历史42页文档和分块JSON不是当前输入。执行run_real_e2e.py输入检查，记录[run_metadata.json](D:/yiliao/medical-track-task-4-2/outputs/e2e/20261006T114819206124Z/run_metadata.json)：BLOCKED_BY_TEST_DATA、case_id=null、未调用DeepSeek、usage=null、无latency。没有生成官方病例或用synthetic补数据。

脚本真实分支尚未执行，依照Checklist补1–3张脱敏图片/授权/Key后运行；真实fallback/BLOCKED和未知usage如实记录。不提交原图/隐私OCR到Git；输出在既有ignored目录。默认competition.db没有新测试病例。

## Regression / Remaining Critical

最终测试计数见下方Final Verification；原A–C120项保持。新增病例/药品swap、missing/extra、手术/费用/诊断属性、安全PASS/BLOCK/无法人工绕过、未知证据page/text/bbox、错药/整页串药、空gold和缺E2E输入均覆盖。前端另跑mock DOM/API，不冒称真实浏览器。

仍需比赛前解决：授权图片的真实E2E/现场浏览器验收、独立KR1 gold及达标计算、KR2/3正式专家/盲评、KR4真实历史覆盖、真实输出的医学事实/证据和安全人工核验。原型声明不代替实际授权脱敏核验；本轮未增加账号/真实短信等功能。完成Phase D后停止。

## Final Verification

- 全量pytest最终：**175 passed / 0 failed**，原Phase A–C120项 + Phase D55项；1条第三方Starlette/httpx deprecation warning。Windows临时SQLite权限限制下经自动审批在沙箱外执行测试，未因此改依赖或测试来绕过失败。
- 前端：**25 checks passed / 0 failed**，内置Node runtime执行已保存的frontend_hardening.test.cjs（本机node不在PATH）；mock DOM/API，不是浏览器E2E。覆盖fake batch禁用、切案全清、迟到clinical/gate/评测不覆盖新内容、错案拒绝、Python指标接线及空gold。
- git diff --check通过；只读检查默认库cases/reviews/deliveries/feedback/follow_up_tasks各0，原产物不被测试污染。原Quality Engine/Recommendation/Pain算法、care prompt、OCR算法及Phase C存储/审批/反馈规则未改。
- REAL_E2E检查结果BLOCKED_BY_TEST_DATA，无外部LLM调用，无真实OCR/LLM成功/latency/token猜测。后续运行所需资料及命令已给Checklist。
- Phase D完成并停止；没有开始下一阶段或新增产品能力。

# 第四问 — 多源品质画像与推荐候选池

Date：2026-10-07。本轮把第四问从单一历史样本门槛扩展为“权威公开机构能力 + 历史理赔观察”的分层画像，但没有修改 `quality_engine.py::recommend_profiles` 的评分、排序或最终推荐逻辑，也没有生成医院排行榜。

## 实现范围

- 新增 `src/medical_claims/multisource_quality.py`：读取医院公开数据审计和本地数据集审计，输出标准名称、地区、类型、等级、病种科室、国家/省级重点专科、可验证专科能力、来源编号、原始链接和查询日期。
- 公开机构能力与历史理赔表现分层保存。历史病例保留费用、用药、治疗记录、住院、痛点、结局/反馈等原画像入口；样本不足或未结构化时只作参考，不形成医院整体质量结论。
- 新增 `GET /api/quality/multisource-candidate-pools`。返回肺炎、流感、高血压的证据充分/部分充分/不足计数、进入候选池的医院、排除医院及逐项原因；响应固定声明未生成最终推荐。
- 第四问页面新增病种切换、候选医院卡片、公开资质/科室/专科能力、历史病例数、证据充分性和原始来源链接。证据不足机构不出现在候选列表，仍保留在后端机构信息/排除记录中。

## 证据门槛

候选准入同时要求：医院名称可靠确认；当前病种存在有公开 URL 的相关科室或专科能力证据；至少两个独立字段可追溯到 `source_ids`。当前可作为理由的字段仅限病种相关科室、病种对应的国家/省级临床重点专科和可验证专科能力。医院等级、类型和名称用于展示与身份核验，不单独准入。单病例历史观察不构成医院质量理由。

现有审计结果：肺炎 10 家中 9 家进入、1 家排除；流感 9 家中 7 家进入、2 家排除；高血压 5 家全部进入。候选数量按证据门槛真实返回，没有为 5 家目标降级或补造理由。

## 文件

- 新增：`src/medical_claims/multisource_quality.py`、`tests/test_multisource_quality.py`。
- 修改：`web/backend/app.py`、`web/dist/index.html`、`web/dist/app.js`、`README1.md`、`IMPLEMENTATION_LOG.md`。
- 数据输入保持：`HOSPITAL_PUBLIC_DATA_AUDIT.md`、`outputs/hospital_public_data_audit.json`、`DATASET_AUDIT_REPORT.md`、`outputs/dataset_audit_summary.json`。
- 未修改：OCR、结构化、KR1/KR2/KR3、SQLite schema、`quality_engine.py` 推荐评分与排序、医生排名和治疗方案排名。

## 验证

新增/相关定向测试覆盖三个候选池、至少两条可追溯证据、等级不能单独准入、单病例只作参考、缺病种专科证据排除、API 路由与原质量引擎回归：**11 passed**。全量 Python 回归：**181 passed / 0 failed**，仅保留 1 条既有 Starlette/httpx 弃用提示。API 直接序列化 smoke 得到肺炎 9/10、流感 7/9、高血压 5/5，前端新增 DOM ID 与 JavaScript 绑定检查通过。

本机 `node` 不在 PATH，因此该轮未重跑独立的 `frontend_hardening.test.cjs`；全量 Python 中的 Phase D 前端源码回归已通过。该轮结束时 Batch 仍禁用；此状态已被下方“真实 Batch”实现取代。

# 真实 Batch — 现有案件工作流批量编排

Date：2026-10-07。移除 Phase D 为避免假结果而采用的 Batch 禁用方案，改为复用现有真实后端和 SQLite 持久化；未增加或修改 OCR、结构化、痛点、温度指引、第四问画像及 KR1/KR2/KR3 算法。

## 实现

- Batch 页面独立提供自动批次名称、批次级授权/脱敏确认、总目录选择和扫描结果，不读取案例工作台的当前 `case_id`、`workflowCase` 或工作台复选框。
- Batch 选择“总目录/病例子目录/图片”，按第一层病例子目录分组；扫描先展示可处理病例、总图片、不支持文件及能从 File API 识别的无有效图片目录。浏览器不会返回完全空目录，页面明确披露该限制。
- 每组独立调用 `POST /api/workflow/cases`、`POST /api/cases/process-detailed`、`POST /api/cases/analyze-pain-points`、`POST /api/cases/generate-care-guide`，最后调用案件结果与案件状态 GET 接口重读 SQLite。
- 状态只随真实请求更新为等待处理、处理中、已完成、需要复核或失败；阶段明确显示创建案件、OCR/结构化、痛点、温度指引和持久化重读。总进度按已结束病例数计算，不生成完整度、置信度、F1、温度分或模拟成功。
- 各病例按顺序隔离执行。单例异常保存失败阶段、错误及可读取的部分持久化结果，然后继续下一例；单例重试复用原 `case_id`，根据已保存的结构化、痛点和关怀结果从未完成阶段继续，不重复创建案件。
- “需要复核”来自真实结构化质量标记/字段状态、痛点 review/warning、Care Guide `review_required` 及安全检查，不使用随机或固定状态。
- 可逐例查看和下载结果；“在案例工作台中打开”使用该例真实 `case_id` 调用现有 SQLite 恢复流程，只承担结果查看和人工处理，不参与批处理执行。
- 批量导出包含批次名称、批次确认状态、扫描信息、目录名、图片数/文件名、真实 `case_id`、状态、阶段、时间、复核原因、错误、最新 workflow metadata 及 SQLite 重读结果，不包含浏览器 `File` 对象或虚构指标。

## 文件与验证

- 修改 `web/dist/index.html`、`web/dist/app.js`、`web/dist/styles.css`：启用目录选择、真实工作流、进度、逐例查看及导出。
- 修改 `tests/test_competition_hardening.py`：旧“必须禁用”断言改为真实接口、无硬编码假指标、持久化导出的源码边界检查。
- 修改 `tests/frontend_hardening.test.cjs`：在无当前案件条件下扫描三个病例并自动创建三个不同 `case_id`，核对图片绑定；第一例 OCR 失败后其余病例继续，工作台可打开结果，单例重试复用原编号，导出来自后端重读结果。前端回归 **37 checks passed**。
- 更新 `README1.md` 与本日志。后端 API、SQLite schema 和核心算法文件均未修改。
- 全量 Python 回归：**181 passed / 0 failed**，仅 1 条既有 Starlette/httpx 弃用提示。前端 Node 模拟 DOM/API：**37 passed / 0 failed**。未在测试中调用 OCR、DeepSeek 或写入默认比赛数据库。
