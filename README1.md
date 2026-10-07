# Medical Track Task 4-2

基于理赔医护记录的“医护洞察与温度指引”引擎原型：从医疗材料中重建病例事实，识别临床、费用和体验痛点，生成个性化关怀，并连接品质画像、证据推荐、人工复核、模拟触达和反馈处理。

本文依据 **2026-10-07 当前代码及本地记录**更新，覆盖 Phase A–D、前端中文化、授权勾选调整，以及第四问多源医院画像候选池。原 README 保留不变；完成度地图和日志中的早期描述是历史状态，不应覆盖当前代码事实。

## 1. 项目简介

团队由两名软件相关成员和一名医学背景成员组成。当前技术栈为 Python >=3.11、FastAPI、RapidOCR / onnxruntime、DeepSeek、SQLite，以及静态 HTML / JavaScript / CSS 前端。

**最小工程闭环已实现，正式比赛效果指标尚未证明达标。** 固定演示样例、测试 fixture、模拟触达和真实医疗运行必须区分。

## 2. 当前完成情况

“已完成”仅指当前范围内的工程实现；“功能已实现但缺正式验证”指代码可接线、有工程测试，尚缺实案或独立效果验证。数据依赖和未实现项单独列出，不将测试通过率换算成赛题达标率。

| 模块 | 核查结论 | 当前事实与待补证据 |
|---|---|---|
| 1. 病历输入与 OCR | 功能已实现但缺正式验证 | 图片上传、本地 RapidOCR、页文字/坐标/可信度、人工修正已接入；支持 JPG/JPEG/PNG/BMP/WebP；**PDF/Word 未启用**。真实图片识别及现场操作待验收。代码：`web/backend/app.py::recognize_uploaded_documents` |
| 2. 结构化与事件重建 | 功能已实现但缺正式验证 | ClaimCase、字段证据与可信度、同案多材料、长病例分块合并和事件输出已实现；历史/本次事件区分仍有限，时间线须实案核验。代码：`models.py`、`long_case.py`、`validation.py` |
| 3. 多维痛点 | 功能已实现但缺正式验证 | 临床/费用/体验规则及 LLM 候选审核已接入，规则具体类型超过 5 类；没有正式专家一致率 >=85% 的证据。代码：`pain_rules.py::detect_rule_pain_points`、`pain_engine.py` |
| 4. 个性化温度指引 | 功能已实现但缺正式验证 | 病例事实、痛点与知识卡进入生成上下文；六类关怀、安全检查和明确降级已实现。三验收病种已有知识卡，但实案个性化、温度感/可行动性 >=4/5 尚未验证。代码：`care_prompt.py`、`care_engine.py` |
| 5. 医院/医生/治疗方案画像 | 功能已实现但缺正式验证 | 历史病例三类主体/六维画像仍保留；另已接入标准名称、地区、类型、等级、病种科室、重点专科、专科能力及来源链接，形成公开机构能力层。两类来源分层展示，不以单病例推断医院质量。代码：`multisource_quality.py`、`profile_records.py`、`quality_engine.py` |
| 6. 可解释推荐 | 功能已实现但缺正式验证 | 已为肺炎、流感、高血压建立证据门槛和候选池；候选准入至少需要名称确认、病种科室/能力证据及两个可追溯字段。当前只生成候选池，不排名、不强制凑足数量、不生成最终推荐。原历史推荐引擎保持不变。 |
| 7. 案件主线与持久化 | 已完成（最小工程范围） | UUID 案件、结果保存/读取、下游失效及切案隔离；SQLite 无需独立服务。代码：`storage.py::CaseStorage`、`workflow_models.py` |
| 8. 风险分级与模拟触达 | 已完成（模拟工程范围） | 自动/人工复核/阻止分支、旧审批失效、模拟收件箱；BLOCKED 不可人工放行。真实渠道未实现。代码：`delivery_gate.py`、`delivery.py`、`storage.py` |
| 9. 反馈与后续处理 | 已完成（服务处理闭环） | 反馈保存、需跟进事项、人工处理结果和完成状态持久化；不自动更新模型、提示词或推荐。代码：`storage.py::save_feedback/resolve_follow_up` |
| 10. KR1 结构化评测 | 功能已实现但缺正式验证 | 病例→实体→字段对齐已修复，网页调用唯一 Python 评测；**正式 Gold、真实 F1 >=0.90 和关键字段缺失率 <5% 成绩仍缺**。代码：`evaluation.py::evaluate` |
| 11. KR2 专家一致率 | 功能已实现但缺正式验证 | 评测器与标注模板已有；正式专家标注、匹配口径确认及 >=85% 结果未完成。代码：`pain_evaluation.py::evaluate_pain_points` |
| 12. KR3 温度/行动性盲评 | 功能已实现但缺正式验证 | 人工评分模板与汇总已有；正式盲评及 >=4/5 结果未完成。代码：`care_evaluation.py::evaluate_care_ratings` |
| 13. KR4 真实数据覆盖 | 依赖真实数据 | 覆盖审计可计算，但三病种、每病种 >=5 家合格机构和 >=5 可计算维度尚不足；`formal_kr4_pass` 不宣称通过。代码：`quality_coverage.py::audit_quality_coverage` |
| 14. 合规与外部系统对接 | 尚未完成（已有部分保护） | 授权/脱敏声明及门禁、伪匿名来源和本地材料处理已有；自动身份清除、真实授权核验、账号隔离、正式留存/删除、保险系统及真实触达接入未实现 |
| 15. 真实端到端运行 | 依赖真实数据，尚未完成验收 | 执行脚本已有，当前留存记录为 `BLOCKED_BY_TEST_DATA`；未证明图片→OCR→DeepSeek→LLM 关怀→风险→SQLite 重读真实完成。代码：`scripts/run_real_e2e.py` |

本次只读核对默认 SQLite：案件 **3**、画像病例记录 **0**、画像产物 **1**、人工复核/触达/反馈/待办记录均 **0**。这是本地瞬时记录数，不是有效医学样本数；存在画像产物也不代表存在足够数据。本次没有写入数据库或重新运行效果评测。

## 3. 系统流程

```text
模拟理赔入口：创建案件，声明授权与脱敏
  → 上传同案图片 → RapidOCR → 可选人工修正 OCR
  → DeepSeek 结构化 / 长病例分块合并
  → 字段、日期、费用及原文证据核验 → ClaimCase 与医疗事件
  → 痛点分析 → 个性化关怀草稿与安全检查
  → 权威公开机构/专科证据 + 已确认历史病例
  → 多源医院画像 → 证据充分性判断 → 推荐候选池
  → 历史质量维度充分时才进入后续可解释推荐
     （数据不足明确拒绝，不排名、不凑数量，可仅发送关怀）
  → 发送安全检查 → 免审 / 人工复核 / 阻止发送
  → 模拟用户收件箱 → 用户反馈 → 后续处理 → 人工记录完成
```

同一个案件编号关联全流程，结果保存于 SQLite。重新结构化或更改上游结果会使相关下游产物和旧批准失效；前端拒绝错案及迟到结果。尚无真实保险事件订阅、真实消息渠道或自动训练回流。

## 4. 第一问：病历结构化与事件重建

核心实现为 `web/backend/app.py::structure_case_with_long_document_support` 与 `src/medical_claims/long_case.py`。输出包含：

```text
case_id / encounter_type / hospitalization / diagnoses / procedures
medications / examinations / costs / events / quality_flags
```

`FieldValue` 保存值、规范化值、状态、可为空的 confidence、evidence、派生说明和 warnings。证据包含 document_id、page、text 与可选 bbox；不能把“有该字段”理解为“每次都提取正确或证据完整”。费用区分总额、医保口径、基金支付、个人负担及实际支付渠道；缺项不按零处理。

`evidence_grounding.py::apply_evidence_grounding` 对短/长病例共用 OCR 来源登记与验证，核对文档、页码、原文、bbox 和明显实体串证。实体名称与属性可在同一 block 或最多三个连续 OCR block/行内绑定，窗口出现其他同类实体时仍要求复核；整页文本不能作为通用绑定区域。失败保留值并标记需复核；这不是完整语义蕴含或医学真实性证明。

同案多文档可合并，事件按已有时间信息重建；缺日期及历史事件语义仍需人工核验。原图不作为案件结果持久化保存。第一问不下“医生治疗错误”的结论。

## 5. 第二问：多维痛点识别

接口：`POST /api/cases/analyze-pain-points`。规则结果与 DeepSeek 语义候选经证据校验、归并和去重，输出类型、严重程度、解释、证据、可信度及复核状态。

已有实际检测逻辑包括：

- **临床**：用药说明缺失、重复用药待核实、异常检查结果需后续关注。
- **费用**：医保目录外费用比例偏高、个人负担比例偏高、费用勾稽异常。
- **体验**：诊断术语不易理解。

均属于需讨论或复核的关注点，不作医疗事故或责任认定。具体覆盖以 `pain_rules.py` 的规则为准，不能把 enum 中所有类型都视为已稳定检测。规则/候选结果仍需正式专家标注验证。

## 6. 第三问：温度指引

接口：`POST /api/cases/generate-care-guide`。输入结构化病例、痛点和知识卡，包含诊断解释、治疗回顾、费用解读、康复计划、心理安抚、下次就医建议及风险提醒。

肺炎、骨折、糖尿病为三验收病种，另有肺结节知识卡。**当前四张卡的 `review_status` 均为 `reviewed`**；这只是仓库声明，不能替代审核者、审核过程和实案内容的医学复核记录，原 README 的“全部草稿”描述已过时。

生成后检查证据引用与医学边界。Phase D 已区分保护性用药声明与危险指令；真正危险内容仍阻止发送。模型失败明确输出 `rules_fallback` 并要求复核，不当作真实 LLM 成功。代码个性化路径存在，不等于实案温度和行动性盲评已通过。

## 7. 第四问：品质画像与推荐

`provider_extraction.py` 提取医院及医生角色候选；经确认上下文和明确数据来源后，`profile_records.py` 组装病例记录。`quality_engine.py` 计算医院、医生、治疗方案/治疗模式分组画像。

六个维度为费用效率、用药合理性、治疗规范性、再入院控制、患者反馈、数据质量。缺数据就标不可用；记录规范性等属于代理指标，治疗模式分组不是官方治疗等级，不将文档完整度当临床疗效。

`multisource_quality.py` 读取 `outputs/hospital_public_data_audit.json` 与 `outputs/dataset_audit_summary.json`，建立独立的公开医院画像层。字段包括标准医院名称、地区、医院类型、等级、病种相关科室、国家/省级临床重点专科、可验证专科能力、`source_ids`、原始链接和查询日期。公开字段无来源时保持缺失。

当前肺炎、流感、高血压候选池采用以下门槛：医院名称可靠确认；有当前病种相关科室或专科能力证据；至少两个彼此独立、可追溯到公开 `source_ids` 的推荐证据字段。医院等级仅展示，不能单独准入。现有审计得到肺炎 **9/10**、流感 **7/9**、高血压 **5/5** 家进入候选池，分别排除 1、2、0 家。候选池表示可进入后续比较，不是排名或最终推荐。

历史病例层继续承载费用、用药、治疗记录、住院、痛点、结局和反馈等观察值。样本不足或尚未结构化时只标为“历史病例参考”，不形成医院整体质量结论，也不参与稳定质量评分。若 SQLite 中存在达到原画像门槛的结构化历史记录，仍沿用原 `quality_engine.py` 的计算公式、样本量和证据字段，不由公开资质补分。

原推荐引擎继续使用同病种比较、默认每主体至少 3 例、可计算维度及理由门槛；至少两条理由关联可计算维度和证据。一般推荐准入不等于更严格的 KR4 五维覆盖通过，本轮没有修改原评分或排名算法。

页面新增“公开证据推荐候选池”，可切换肺炎、流感和高血压，查看医院名称、等级、类型、相关科室、重点专科、能力、历史病例数、证据充分性及原始来源。`GET /api/quality/multisource-candidate-pools` 只返回候选池和排除原因，`generated_final_recommendation=false`。**当前真实历史覆盖仍不足，可靠最终推荐可能不可用；这是保守拒绝，不应补假分或合成历史凑指标。**

## 8. 案件工作流、触达与反馈闭环

- Phase A：UUID 案件、OCR/结构化/痛点/关怀持久化，默认 `outputs/competition.db`；可用 `COMPETITION_DB_PATH` 指定路径。
- Phase B：案件与品质数据组装、历史导入、画像构建、推荐查询、覆盖审计连接。
- Phase C：风险分级、按需团队人工确认、内容指纹及事务校验、模拟收件箱、反馈和后续处理。
- Phase D：移除假 Batch、修复用药声明误报、KR1 对齐评测、OCR 原文 grounding、切案隔离、真实 E2E 检查工具。当前 Batch 已在后续迭代接入真实案件工作流。
- 真实 Batch：批处理中心有独立的批次名称、授权和脱敏确认，不依赖案例工作台当前案件；按病例子文件夹自动创建案件，依次调用 OCR/结构化、痛点分析和温度指引，并从 SQLite 重读保存结果。

授权与脱敏已在首页改为两项勾选确认；都勾选才能在前端新建案件，仍提交原 `authorized` / `confirmed` 值。勾选不自动证明已获授权或完成脱敏。

`AUTO_DELIVER` 表示免人工批准，仍须用户点击发送；`REVIEW_REQUIRED` 需有效人工复核；`BLOCKED` 不能批准绕过。审核者目前是团队成员声明，不是认证医生账号。触达仅为 `mock_web_inbox`，没有真实短信/APP/企业微信。

低评分、无帮助或非“内容清楚”类别可触发后续处理。处理结果保存并标记完成；该服务闭环不自动调整推荐、模型或提示词，也不把服务评分自动当医院品质评分。

## 9. 本地运行

不需要 Anaconda、Docker、Node 或独立数据库服务。首次缺环境时创建 `.venv` 并安装 `.[api,dev]`；已有正常环境直接启动。

```powershell
cd D:\yiliao\medical-track-task-4-2
# 仅在未创建环境时执行
python -m venv .venv
# 仅在首次安装或缺依赖时执行
.\.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
```

本地配置 `web/backend/.env`；从 `.env.example` 复制时不要覆盖已有配置。真实 DeepSeek 流程需要有效 `DEEPSEEK_API_KEY`，不在网页填写或打印密钥。

两个 PowerShell 窗口分别执行：

```powershell
# 窗口 1
cd D:\yiliao\medical-track-task-4-2
.\scripts\start_backend.ps1
```

```powershell
# 窗口 2
cd D:\yiliao\medical-track-task-4-2
.\scripts\start_frontend.ps1
```

脚本被策略阻止时，仅在对应窗口执行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 后重试。打开 http://127.0.0.1:8765/，填写后端 http://127.0.0.1:8000，检测并保存配置。健康地址为 http://127.0.0.1:8000/api/health；API 文档为 http://127.0.0.1:8000/docs。

页面按“确认授权/脱敏→创建新案件→添加图片→OCR与结构化→分析痛点→生成指引→读取画像与推荐→检查是否可以发送→模拟触达→反馈/后续处理”操作。Batch 页面选择包含多个病例子文件夹的总目录后，会对每例运行同一真实后端流程；进度取自实际请求状态，结果从 SQLite 重读后导出。**PDF/Word 当前未启用**。无 Key 可检查启动和空案件等工程能力，不能完成真实 LLM 结构化或真实 Batch 全流程。健康接口的 OCR/LLM ready 仅表示包可导入/配置非空，不证明实际调用成功。

Batch 目录应为“总目录/病例子目录/图片”，例如 `待处理病例/病例A/第1页.jpg`、`待处理病例/病例B/第1页.png`；同一病例子目录内的图片合并为一个案件。用户只需在 Batch 页确认本批次授权与脱敏、选择总目录并开始处理，无需提前创建案件。扫描结果列出病例、图片、不支持文件及可识别的无有效图片目录。失败例单独重试时复用原 `case_id` 并从未完成阶段继续；处理后的病例可加载到案例工作台查看和人工处理。

完整 Windows 步骤、错误处理和无图片工程验证见 [RUN_PROJECT_GUIDE.md](RUN_PROJECT_GUIDE.md)。结束时在服务窗口按 Ctrl+C；不要清空 SQLite。

真实 E2E 需要自行提供授权、脱敏图片，放入 `work/real_e2e_inputs/` 后运行（文件名必须实际存在）：

```powershell
.\.venv\Scripts\python.exe scripts/run_real_e2e.py --images work/real_e2e_inputs/document-1.jpg --authorized --deidentified
```

该脚本不要求启动 uvicorn，保存实际阶段、时延、API 提供的 usage 和 SQLite 重读结果到 `outputs/e2e/<时间戳>/`；脚本不自动模拟发送/提交反馈。详情见 [REAL_E2E_CHECKLIST.md](REAL_E2E_CHECKLIST.md)。最新留存运行仅为缺图片检查，不是成功记录。

## 10. 评测方式

### 工程回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
# 若已安装 Node，可另运行
node tests/frontend_hardening.test.cjs
```

当前全量验收为 **187 项 Python 测试通过**，其中多源候选池与原质量引擎定向测试 **11 项通过**。前端模拟 DOM/API 回归 **37 项通过**，覆盖无当前案件启动、三病例独立创建和图片绑定、单例失败隔离、原 `case_id` 重试、工作台打开、持久化结果导出及既有切案保护；它不是实际浏览器或真实医疗 E2E。fixture、假 Provider、临时 SQLite 和模拟检查只证明工程行为，不能当真实医疗结果。

### KR1：结构化

```powershell
.\.venv\Scripts\python.exe -m medical_claims.evaluation prediction.json gold.json
```

网页 `POST /api/evaluation/structured` 与 CLI 共用 `evaluation.py::evaluate`。按 case_id 独立对齐、确定性实体匹配，再比较实体内字段；交换病例、药物剂量/频次及错误费用明细会扣分。输出 micro precision/recall/F1、macro F1、分字段/分病例指标。

关键缺失率 = **Gold 中应存在但预测缺失的关键字段数 / Gold 中应存在的关键字段数**，不是 `1-recall`。关键字段合同覆盖诊断名称/编码、手术名称/日期/部位、药品名称/剂量/频次/途径/疗程、入出院日期/住院日和费用非空字段；零分母为 null。内部 hallucination_rate 为预测字段 FP 比例，包含错值，不是官方医学幻觉指标。完整口径见加固报告。

正式专家 Gold 与真实成绩尚缺，不能宣称 F1 >=0.90 或缺失率 <5%。

### KR2 / KR3 / KR4

```powershell
.\.venv\Scripts\python.exe -m medical_claims.pain_evaluation pain.prediction.json pain_gold.final.json
.\.venv\Scripts\python.exe -m medical_claims.care_evaluation evaluation\care-ratings.json
```

这些文件需要实际制作，不是仓库已提供的正式成绩。KR2 当前按病例/类型标签匹配汇总一致率，目标 >=85%；正式专家标注与口径需核验。KR3 按评分表汇总温度感与可行动性，目标均 >=4/5，同时核对事实错误与不安全建议。模板为 `examples/pain_gold.template.json`、`evaluation/templates/care-ratings.template.json`。

KR4 的公开证据候选池使用 `GET /api/quality/multisource-candidate-pools`；历史五维覆盖仍使用 `GET /api/quality/coverage` / `quality_coverage.py`。公开专科证据只能证明机构能力背景，不能替代费用、结局、反馈等历史表现。正式数据来源和独立验收仍缺，`formal_kr4_pass` 不宣称达标。

## 11. 当前仍需完成的事项

1. 提供合法授权脱敏病例图片，完成真实 OCR→DeepSeek→关怀→风险→SQLite 重读及页面闭环验收；记录真实失败、降级、风险与时延。
2. 医学成员对照原图核查事实、时间线、证据及安全建议，留存知识卡/输出审核依据。
3. 建立独立正式 KR1 Gold，按统一评测口径计算真实成绩；组织 KR2 专家标注和 KR3 盲评。
4. 对肺炎、流感、高血压候选池进行医学和来源复核；继续获取足量且可追溯的结构化 KR4 历史病例及结局/反馈，之后再验收最终推荐，不因当前候选池数量降低门槛。
5. 若要求生产对接，补真实保险/触达系统连接及账号、授权核验、留存/删除等治理能力；当前只声明本地比赛原型边界。

不得为 Batch、比赛成绩或历史表现补造结果。PDF/Word、真实消息渠道及自动模型更新均不应被写成现有功能。

## 12. 目录结构与重要文件

```text
src/medical_claims/
  models.py / validation.py / long_case.py    # 病例、校验、多材料分块合并
  evidence_grounding.py                      # OCR 原文来源与实体绑定
  pain_rules.py / pain_engine.py             # 痛点规则与候选校验
  care_prompt.py / care_engine.py            # 病例关怀上下文、安全与降级
  provider_extraction.py / profile_records.py# 来源候选及画像病例组装
  quality_engine.py / quality_integration.py # 画像、推荐与案件连接
  multisource_quality.py                     # 公开能力画像、证据门槛与候选池
  quality_coverage.py                        # KR4 覆盖核查
  workflow_models.py / storage.py            # 案件状态与 SQLite
  delivery_gate.py / delivery.py             # 风险与模拟触达
  review_models.py / feedback_models.py      # 人工确认、反馈、后续处理模型
  evaluation.py / pain_evaluation.py / care_evaluation.py
web/backend/app.py                          # 实际 HTTP API、RapidOCR/DeepSeek 接线
web/dist/                                   # 静态中文页面与既有样式
knowledge/care_guides/                       # 肺炎、骨折、糖尿病及肺结节知识卡
tests/                                      # 工程回归、fixture 与前端模拟检查
examples/ / evaluation/templates/            # 示例及人工评价模板，不是正式 Gold
scripts/                                    # 启动、真实 E2E、画像/覆盖工具
outputs/ / work/                            # 本地产物、数据库、输入及缓存（ignored）
```

- [PROJECT_COMPLETION_MAP.md](PROJECT_COMPLETION_MAP.md)：历史完整度地图及各阶段追加状态；优先读末尾 Phase D。
- [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md)：Phase A–D 实现和历史测试记录。
- [COMPETITION_HARDENING_REPORT.md](COMPETITION_HARDENING_REPORT.md)：安全、KR1、证据、切案与剩余关键问题。
- [REAL_E2E_CHECKLIST.md](REAL_E2E_CHECKLIST.md)、[RUN_PROJECT_GUIDE.md](RUN_PROJECT_GUIDE.md)：真实输入准备和运行操作。
- `docs/`：团队 API、医学复核和第四问口径说明。

## 13. 数据与合规说明

上传前由操作者核实授权范围并遮盖姓名、身份证号、联系方式、住址、条码/二维码等身份信息。前端勾选、后端状态门禁和来源声明不能代替实际核验；目前没有自动完整脱敏能力。

图片由本地 OCR 处理，脱敏 OCR 文本及病例事实会进入 DeepSeek。只发送文字不等于已自动去除隐私；使用授权需涵盖外部模型处理。医院/医生伪匿名上下文也不等于对所有患者字段完成匿名化。

真实密钥只存本地环境变量/`.env`；病例、OCR 缓存、身份映射、SQLite 和医疗产物不得未经允许提交 Git 或公开分享。当前无正式身份鉴权、多用户隔离、可证明的留存/删除流程或认证医生审核。

系统输出是需核实的关怀与讨论建议，不认定医疗责任，不替代诊疗。公开资质、重点专科和候选池不等同于疗效、排名或个体就医推荐；模拟触达不是实际用户送达证明，测试结果不是医学有效性证明。
