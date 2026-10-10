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

# 第四问 Phase 1 — 独立多源能力画像与可解释推荐（2026-10-07）

## 范围与病种选择

- 本轮只实现统一数据结构、候选医院目录、能力画像、当前治疗方案评价、推荐评分、地理、历史证据规则、V2 服务/API、测试和文档。
- 最终选择肺炎、流感和高血压，候选医院分别为 9、7、5 家。公开数据审计中糖尿病仅约 3 家满足可追溯证据要求，因此没有降低门槛或生成假候选。
- 未改 OCR、前三问、Batch 和现有前端，未重新处理 70 例，也未调用外部模型进行医院排名。

## 新的独立主流程

```text
knowledge/quality/disease_provider_catalog.json
  + PatientRecommendationContext
  + 可选的 ProfileCaseRecord 事实
→ quality_phase1_service.py
→ quality_phase1_adapters.py（仅历史事实转换）
→ quality_phase1_engine.py（评分/地理/历史/医生/治疗规则）
→ HospitalRecommendationReportV2 / ClinicianRecommendation / CurrentTreatmentEvaluation
```

新增 V2 接口：

- `GET /api/quality/v2/catalog`
- `POST /api/quality/v2/recommendations/hospitals`
- `POST /api/quality/v2/recommendations/clinicians`
- `POST /api/quality/v2/treatment-evaluation`

## 新旧边界

- **保留兼容**：`quality_models.py`、`quality_engine.py`、`quality_integration.py`、`multisource_quality.py`、旧 candidate pool 和旧 API，现有页面可以继续使用。
- **仅复用数据**：旧 `ProfileCaseRecord` 与 SQLite `profile_case_records` 中的费用、用药、治疗、住院、痛点、结局和反馈事实；已审计公开 JSON 仅用于离线生成新版目录。
- **不再进入新主流程**：旧画像分、旧候选排序、`recommend_profiles` 和旧 `RecommendationReport`。未知病种也不会回退到旧推荐。
- **独立新模块**：`quality_phase1_models.py`、`disease_provider_catalog.py`、`quality_phase1_adapters.py`、`quality_phase1_engine.py`、`quality_phase1_service.py` 和跟踪目录 JSON。

## 规则落地

- 医院画像六维：机构基础、病种专科、医疗资源、区域服务、患者服务/反馈、历史案例支撑。
- 医生画像六维：基础资质、病种专长、技术/治疗专长、所属平台能力、当前病例匹配、历史案例支撑。
- 当前治疗评价六维：诊疗匹配、用药合理性、操作/手术合理性、治疗完整性、出院连续照护、风险管理；没有历史病例仍可评价。
- 医院推荐权重：病种专科 35%、地理 25%、专业能力 25%、历史证据 10%、偏好/费用 5%。缺失的可选维度不计 0，按可用权重归一化。
- 地理规则：同等能力本地优先；复杂病例、本地无合格能力或明确转诊需求时升级排序。
- 历史规则：0 例不计 0；1 例只称单病例观察；2–4 例为有限参考；5 例及以上为较充分参考，仍不等于长期质量结论。

## 文件

- 新增：`src/medical_claims/quality_phase1_models.py`
- 新增：`src/medical_claims/disease_provider_catalog.py`
- 新增：`src/medical_claims/quality_phase1_adapters.py`
- 新增：`src/medical_claims/quality_phase1_engine.py`
- 新增：`src/medical_claims/quality_phase1_service.py`
- 新增：`scripts/build_phase1_provider_catalog.py`
- 新增：`knowledge/quality/disease_provider_catalog.json`
- 新增：`tests/test_quality_phase1.py`
- 新增：`QUALITY_RECOMMENDATION_STANDARD.md`
- 修改：`web/backend/app.py`、`README1.md`、`IMPLEMENTATION_LOG.md`

## 验证

- Phase 1 单元测试覆盖目录规模、来源追溯、跨病种隔离、地理排序、核心能力门槛、历史缺失、单病例边界、推荐理由、医生非职称排序、当前治疗无历史评价和 V2 服务不回退旧推荐。
- Phase 1 单元测试：**17 passed**；新旧第四问定向回归：**28 passed**。
- 全量 Python 回归：**204 passed / 0 failed**，仅 1 条既有 Starlette/httpx 弃用提示。
- 前端模拟 DOM/API 回归：**49 checks passed**；本轮未修改前端。
- 工程测试不等于正式医学效果、真实用户 E2E 或 KR4 达标。

## 仍缺数据

- 经人工核验的医生公开资料和排班/可就诊信息；
- 患者真实城市、复杂度、合并症和明确偏好；
- 可稳定关联医院/医生的足量历史费用、结局、再入院和反馈；
- 当前治疗合理性的正式医学规则或专家复核结果；
- 独立正式 KR4 数据与验收结论。

# 第四问 Phase 2 — V2 正式前端（2026-10-07）

## 页面结构

- 将“品质推荐”正式页面切换为五块：当前患者就医需求、推荐医院、医院完整画像、医生推荐、当前治疗方案评价。
- 视觉复用第三问的页面标题、白色卡片、青绿色强调、状态标签和折叠证据；桌面端分块展示，760px 以下改为单列。
- 增加常驻但不遮挡内容的小型合规说明，不宣称系统自动完成脱敏。

## 推荐上下文

- 默认“当前病例”：病种和合并症来自当前结构化诊断，复杂度来自已有痛点严重程度及病例结构规则；城市仅为推荐情境临时输入，不修改原始病例。
- “演示病例”：肺炎+上海、流感+上海、高血压+苏州。只生成临时 V2 请求，不写当前案件或 SQLite，不固定医院结果和转诊结论。
- 切换病例立即清除第四问结果；切出演示模式后重新使用当前病例事实。

## V2 展示

- 正式页面调用 V2 医院推荐、医生推荐和当前治疗评价接口；候选能力画像随医院推荐响应返回，不再把 catalog 当作前端准入白名单。
- 医院卡按后端返回顺序展示当前推荐分、置信度、地理层级、至少两条真实理由、来源与限制；不在前端重算分数。
- 医院详情展示六个能力维度，并将地理可达性放在独立“当前推荐情境”。
- 当前没有可靠医生目录数据，页面显示资料不足，不生成假医生；若 V2 后续返回真实资料，卡片结构支持对应六维信息。
- 治疗区展示六维评价、证据、限制、人工复核提示、只基于状态生成的患者总结及历史治疗参考空状态。

## 旧版兼容边界

- 旧覆盖审计、旧 candidate pool、旧历史画像与旧 RecommendationReport 前端代码仍保留在 `legacyQualityUi` 隐藏容器。
- 普通演示入口不加载旧第四问接口，不同时展示两套推荐结果。
- 旧后端接口未删除，供兼容或开发调试使用。

## 修改文件

- `web/dist/index.html`
- `web/dist/app.js`
- `web/dist/styles.css`
- `tests/frontend_hardening.test.cjs`
- `README1.md`
- `IMPLEMENTATION_LOG.md`
- `QUALITY_RECOMMENDATION_STANDARD.md`

## 验证

- 前端专项覆盖 V2 API、三个基准演示病种、目录外明确病种、低置信诊断、地区缺失、理由来源、六维画像、医生空状态、治疗六维、中文状态、历史缺失、演示隔离和切案清理。
- 全量 Python 回归：**204 passed / 0 failed**，仅 1 条既有 Starlette/httpx 弃用提示。
- 前端模拟 DOM/API：**92 checks passed**；新增检查覆盖 V2 四接口、三个演示上下文、真实病例限制、医院/医生六维画像、治疗总结、旧入口隐藏和移动端 CSS。
- 当前未执行真实浏览器 E2E；模拟 DOM/API 测试不能替代真实病例或医学效果验证。

# 第四问 V2 — 通用病种准入与候选发现修正（2026-10-07）

## 修正后的主线

- 当前病例中可靠识别的明确疾病均可进入 V2，不再要求病种预先存在于固定目录。前端以结构化诊断状态、置信度和诊断语境选择病种，不允许用户覆盖真实病例病种。
- 新增独立 `provider_discovery.py`，按“已核验基准目录 / 本地可信缓存 → 可插拔动态 provider → 统一证据门槛”的顺序发现医院与医生。
- `disease_provider_catalog` 改为已核验能力缓存与比赛基准数据集；肺炎、流感、高血压仅代表当前已有 9/7/5 家完成覆盖验证的基准病种。
- 动态 provider 必须返回统一医院/医生能力画像。医院身份、病种相关科室或能力、核心来源链接未通过门槛的资料会被拒绝，搜索摘要和无来源常识不能直接进入推荐。
- 通过门槛的画像可写入本地 `outputs/provider_capability_cache.json`，再由后续病例复用。不同病种下的同一机构分别保存，避免能力画像互相覆盖。
- 可靠候选不足时按实际数量返回并披露限制；没有合格资料时返回空结果和明确原因，不再使用“目录外病种不支持”的结论。

## 接口与边界

- 新增 `POST /api/quality/v2/provider-discovery`：查询可信资源并触发部署层配置的动态发现 provider。
- 新增 `POST /api/quality/v2/provider-cache/verified`：仅保存通过统一证据门槛的能力画像。
- 现有医院推荐接口已改为消费通用发现结果；评分权重、地理规则、历史证据规则均未修改。
- 本节初版只实现动态发现插件协议和 fallback 编排；后续“在线发现与证据链”修正已补充真实联网适配器，见下节。
- 前三问、Batch、SQLite schema 和旧第四问兼容接口均未修改。

## 验证

- 新增回归覆盖：目录外明确病种进入动态发现、合格资料可推荐、病种证据错配被拒绝、少于 5 家时不凑数、本地可信缓存写入与复用、无证据画像拒绝写入、V2 新接口注册。
- 前端回归覆盖：固定白名单已移除、糖尿病等目录外明确病种会调用真实 V2 接口、低置信诊断不会被提升为推荐事实。
- 全量 Python 回归：**208 passed / 0 failed**，仅 1 条既有 Starlette/httpx 弃用提示。
- 前端模拟 DOM/API 回归：**95 checks passed**。这些结果验证当时的工程行为，不代表动态联网来源、真实病例推荐质量或 KR4 已正式达标。

# 第四问 V2 — 在线动态发现与证据链（2026-10-07）

- 新增 `online_provider_discovery.py`。配置 Key 时优先使用 Serper；无 Key 时使用搜狗网页检索并以 Bing RSS 降级发现候选链接。搜索标题和摘要不写入能力证据。
- 系统随后抓取候选权威原始页面，只接受卫健委、政府、大学/医学院或能够自证身份的医院官网页面，并拒绝内网地址、非网页内容和无法确认来源的站点。
- 发现顺序为当前城市、同省、周边区域医疗中心；复杂病例或明确转诊情境再扩大到国家临床重点专科范围。
- 病种能力需求由病例明确诊断生成。三个基准病种使用已验证科室词；其他病种使用病例诊断名称作为能力检索事实，不凭大模型常识猜测所属专科。
- 动态医院画像沿用统一 `HospitalCapabilityProfile`；动态医生画像沿用统一 `ClinicianCapabilityProfile`。没有历史病例不影响公开能力准入。
- 新增结构化 `CapabilityEvidenceRecord`，保存 `source_name`、`source_type`、`source_url`、`query_date`、必要短 `evidence_text`、`evidence_field` 和 `supported_claim`。正式推荐理由必须关联具体 evidence record 或真实历史病例字段。
- 医院评分完成后，再对前列医院发现医生；医生职称只展示，现有医生推荐权重未修改。
- 核验成功的医院和医生自动写入 `outputs/provider_capability_cache.json`。相同病种与机构优先复用，默认 90 天刷新周期；缓存过期时保留本次可用资料并同时尝试在线核验。
- 前端等待状态依次说明病种能力解析、权威资源发现/核验、推荐与医生匹配。医院卡、医院关键画像和医生卡可展开查看理由、短证据、来源机构、来源类型、原始网址与查询日期。
- 网络或搜索 provider 不可用时返回明确降级原因；不会回退旧推荐、不会把目录外病种标记为不支持、不会用搜索摘要或固定结果补位。
- 隐私边界：在线发现只发送病种名称和推荐地区，不发送 OCR 文本、病例正文或患者身份字段。
- 未修改前三问、Batch、医院评分权重、缺失维度归一化或当前治疗六维。

验证：在线医院/医生发现、原始页面证据、搜索摘要隔离、自动缓存、网络失败降级、推荐理由证据关联均有回归。随后完成了真实联网排查：修复中文医疗查询质量不足、第三方聚合页误判、名单标题误当医院、营销句误当医院名、医生统计语句误当姓名等问题；旧版在线缓存会按新版证据规则失效重核。以目录外病种“白内障 + 上海市”实际运行，生成 5 条查询、取得 30 条搜索结果、读取 20 个原始页面，最终有 2 家医院和 2 名医生通过权威证据门槛并进入 V2 结果。诊断文件为 `outputs/live_cataract_shanghai_discovery_verified.json`，最终含医生结果的验收文件为 `outputs/live_cataract_shanghai_acceptance.json`。全量 Python **216 passed**，前端模拟 DOM/API **100 checks passed**；这些结果不等于全国医疗资源已完成预采集或 KR4 已正式达标。

# 第四问 V2 — 推荐意图与重庆白内障验收（2026-10-08）

- 第三问温度指引和第四问 `RecommendationIntent` 共用 `external_model_policy.py`：只有案件同时满足已授权、已确认脱敏和外部模型处理开关时才允许调用，不要求重复确认。
- 第四问发送给 DeepSeek 的内容限定为主/次诊断、住院科室、手术/操作、必要医疗事件和推荐地区，不发送 case_id、OCR 全文、原始图片或直接身份信息。
- 第四问优先读取当前案件已保存的第三问 `care_guide.disease_topic`，并把它作为锁定的上游核心病种；DeepSeek 不再重新选择主病种，只补充治疗目标、专科能力需求、复杂度、地域策略和检索计划。仅在第三问尚不存在时，才降级为从结构化病例判断。

# 第四问 V2 — 固定权重评分与轻量事实核验（2026-10-08）

- DeepSeek 推荐规划统一输出 3–5 家候选医院、每院 2–3 条理由、固定五维分数，并尽量输出 1–3 名可核验医生及固定四维分数；提示词集中在 `recommendation_prompt.py`。
- 医院固定权重：病种/专科匹配 35%、地理与就医便利 20%、医院专业能力 20%、医生/专家资源 15%、技术与治疗能力 10%。
- 医生固定权重：当前病种匹配 35%、治疗/手术专长 30%、所属医院/科室平台 20%、临床资历与公开能力 15%。
- 后端忽略 DeepSeek 的 `total_score`，按固定权重重新计算；地理分使用案件城市与医院城市的确定性规则纠正。
- 联网资料只用于核验医院/医生身份、地区、科室/专业方向和推荐理由。A/B 级证据可进入正式展示；资料不足时保留候选、降低 `evidence_confidence` 并标记“公开资料有限”，只有明确病种或身份事实冲突才移除。
- 历史病例、患者反馈、完整六维画像和多个官方来源不再参与医院/医生准入，也不进入本轮固定权重总分。
- 第四问医院卡和医生卡展示后端总分、逐维分数、固定权重、理由、来源链接、证据可信度和核验状态。
- 重庆白内障授权病例真实验收：DeepSeek 锁定第三问病种，后端返回 5 家医院；前三家为重庆本地，后两家为成都/西安；医生公开资料本次未形成可靠结果，按“公开资料有限”处理，未生成占位医生。
- 五病种公开资源流程验收结果保存于 `outputs/scoring_v2_five_disease_validation_summary.json`：肺炎 5 家/1 名医生，流感 5 家/2 名医生，高血压 5 家/0 名医生，白内障 5 家/1 名医生，屈光不正 2 家/0 名医生。所有医院均带理由和证据链接；高血压与屈光不正暴露出本地候选覆盖不足，不能把工程返回数量解释为推荐质量已达标。
- 回归：Python **241 passed**；前端模拟 DOM/API **108 checks passed**。测试证明固定权重、总分重算、地理纠偏、资料不足降级和既有流程未回归，不代表正式医学推荐效果达标。
- 前端第四问同样优先显示第三问核心病种，不再自行从诊断列表重新选一次；后端仍校验该病种确实存在于当前结构化病例，防止跨案件或无依据传递。
- 常规病例采用本地优先。本地推荐最多 3 家，再补充 LLM 规划的周边一二线/区域中心城市；周边机构必须有公立三级甲等和当前病种能力证据。医院评分权重与缺失维度归一化未修改。
- 新增 `knowledge/quality/verified_provider_capability_cache.json`，保存已联网核验的白内障机构能力和来源证据，不保存最终名次。V2 运行时优先复用这层可信缓存，覆盖不足时仍进入在线动态发现。
- 重庆白内障实案最终由 V2 规则返回 3 家重庆医院，并补充成都四川大学华西医院和西安交通大学第一附属医院。每条正式理由均关联具体 evidence record 和权威原始网址。
- 验收输出：`outputs/chongqing_cataract_upstream_acceptance.json`。实案中的第四问病种为“皮质型老年性白内障左眼”，来源明确记录为 `care_guide.disease_topic`；医生动态资料本次未达到证据门槛，保持为空，不用职称或搜索摘要补位。
- 全量 Python：**235 passed / 0 failed**；前端模拟 DOM/API：**101 checks passed**。首次沙箱内全量运行因 Windows 系统 Temp 权限导致 SQLite 无法建库，改用宿主环境复跑后全部通过。

# 第四问 V2 — 联网失败保留模型候选（2026-10-08）

- DeepSeek 已成功输出的医院和医生候选不再依赖联网核验成功才能展示。权威页面核验结果优先合并；没有命中缓存或联网超时的候选转换为统一的待核验能力画像，继续进入既有固定权重重算与地理排序。
- 证据状态统一为 `verified`、`partially_verified`、`pending_verification`、`contradicted`。只有明确事实冲突才移除候选；`ConnectTimeout`、搜索服务不可用、单页读取失败、暂未找到公开资料，以及缺少历史病例、患者反馈或完整六维画像均不会删除候选。
- 待核验候选的置信度降低，并明确显示“推荐基于当前病例与模型综合判断，公开证据暂未完成核验。”待核验画像不会写入可信缓存；后续重新生成时仍会优先读取有效缓存并再次尝试联网补证。
- 修复本地优先排序对同城候选的截断：前三家本地优先，其余本地候选仍保留在周边候选之后，不会因显示顺序规则被直接丢弃。
- 前端医院和医生卡显示“已核验 / 部分核验 / 待核验”，逐维状态不再直接显示内部英文值；空状态不再声称“0 家通过证据门槛”。
- 新增故意抛出 `httpx.ConnectTimeout` 的回归：DeepSeek 返回 5 家医院和 5 名医生时，后端仍返回全部候选且均为待核验；前端模拟 DOM/API 同样渲染 5 张医院卡。
- 回归：Python **241 passed**；前端模拟 DOM/API **113 checks passed**。首次沙箱内全量测试因 Windows Temp 权限造成 SQLite 建库失败，设置仓库内 `TEMP/TMP` 后全量通过；该环境错误不是业务回归。

# 数据库基础重构 — 品质画像与推荐分层（2026-10-10）

- 引入 `database_migrations.py` 和 `schema_migrations`，将现有 SQLite 从启动时临时建表改为版本化 migration；当前版本为 5。
- 原有 `case_profile_contexts`、`profile_case_records`、`quality_portfolios` 三张第四问画像表完整保留，旧接口和旧写入路径继续兼容。新链路完成双写、历史迁移和一致性验证前禁止删除。
- 新增独立品质画像层：`provider_quality_observations` 保存历史病例原子质量事实，`provider_profile_snapshots` 保存对象 × 病种的版本化画像，`provider_metric_scores` 保存费用效率、用药合理性、治疗规范性、再入院、患者反馈和数据质量等指标。
- 新增独立推荐层：`recommendation_reports`、`recommendation_items`、`recommendation_scores`、`recommendation_evidence` 保存当前案件的推荐输出、匹配分和证据链。推荐项可通过 `profile_snapshot_id` 明确关联当时使用的历史品质画像。
- 新增用户、角色、权限、案件参与关系表，以及客服会话、会话分析、案件关联、系统日志和 Batch 持久化表。当前完成的是数据层；正式登录鉴权、客服业务 API、Batch 服务端恢复尚未接入，不把建表写成业务功能已经完成。
- `CaseStorage` 增加迁移、Schema 查询、案件范围权限判断、新品质画像读写和推荐报告版本化读写。结构化或痛点结果变化时，旧推荐报告会失效。
- 已先创建 SQLite 一致性备份，再将当前 `outputs/competition.db` 从 9 表迁移到 28 表；原案件、结果、旧画像、复核、触达、反馈和待办记录数保持不变，`PRAGMA foreign_key_check` 为 0 项异常。备份保存在被 Git 忽略的 `outputs/backups/`。
- 新增 `tests/test_database_design.py`，覆盖新库建表、旧库兼容、旧画像表保留、角色案件范围、画像/推荐分层和推荐版本化。
- 回归：Python **247 passed**；前端模拟 DOM/API **130 checks passed**。测试证明 Schema 兼容性与现有工程行为未回归，不代表新画像指标已用正式历史病例完成医学有效性验证。
