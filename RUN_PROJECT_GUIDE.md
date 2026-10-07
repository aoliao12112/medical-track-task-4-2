# RUN PROJECT GUIDE

面向第一次接手项目的 Windows / PowerShell 操作者。核对日期：2026-10-06。所有命令以 `D:\yiliao\medical-track-task-4-2` 为仓库目录；命令中的图片文件名需要替换成你实际提供的文件。

本指南依据当前 `pyproject.toml`、`scripts/start_backend.ps1`、`scripts/start_frontend.ps1`、`web/backend/app.py`、`web/backend/.env.example`、`web/dist/index.html`、`web/dist/app.js`、`scripts/run_real_e2e.py`、`REAL_E2E_CHECKLIST.md` 和 Phase A–D 实现记录。此次只生成指南，没有启动服务、安装依赖、运行测试、读取真实密钥、清空数据库或调用外部模型。

## 1. 环境检查：已有的与需要准备的

| 项目 | 当前代码要求 / 仓库检查结果 | 你需要准备什么 |
|---|---|---|
| Python | `pyproject.toml` 要求 **Python >=3.11**；当前 `.venv` 报告版本为 3.14.8 | 本机需要可运行的 Python；复制来的虚拟环境可能引用另一台电脑的 Python，不能保证可用 |
| 虚拟环境 | 根目录 `.venv` 已存在；解释器为 `.venv\Scripts\python.exe` | 先检查能否执行，不必重新创建，也不需要 Anaconda |
| 基础依赖 | `pydantic>=2.8,<3` | 通过下方项目安装命令安装 |
| API 依赖 | FastAPI、uvicorn[standard]、python-multipart、python-dotenv、httpx | 位于 `[project.optional-dependencies].api`，安装时必须带 `api` |
| OCR 依赖 | **RapidOCR 与 onnxruntime 均已列在 `api` 依赖中**；当前环境已安装 rapidocr 3.9.2 / onnxruntime 1.30.0 | 首次真实识别还需确认 OCR 引擎与模型能初始化；健康接口成功不代表已推理成功 |
| 测试依赖 | `pytest>=8,<9`，位于 `dev` | 安装时带 `dev` |
| DeepSeek | 配置模板已存在；`web/backend/.env` 文件也存在，但本次没有读取其内容 | 真实 LLM 流程需要你自行配置有效 Key、可用账户及网络；是否已经填写有效 Key 未核验 |
| 前端 | 已提交 `web/dist/index.html`、`app.js`、`styles.css`，由 Python 静态服务器提供 | 浏览器即可，**不需要 npm install / npm build** |
| Node | 启动系统不需要；当前检查未在 PATH 中找到 `node` | 只有运行前端 hardening 测试时才需要 Node |
| Docker | 现有启动脚本不用 Docker | 不需要安装或启动 Docker |
| 数据库 | Python 内置 SQLite；默认 `outputs/competition.db` 已存在 | 不需要 MySQL/PostgreSQL/数据库服务；保留现有文件 |
| 病例图片 | `REAL_E2E_CHECKLIST.md` 当前记录 `BLOCKED_BY_TEST_DATA`，尚无核验授权的真实输入 | 真实 E2E 需要提供同一案件 1–3 张授权、脱敏医疗图片 |

先在 PowerShell 执行：

```powershell
cd D:\yiliao\medical-track-task-4-2
Test-Path .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip show pydantic fastapi uvicorn python-multipart python-dotenv httpx rapidocr onnxruntime pytest
.\.venv\Scripts\python.exe -m pip check
```

第一条检查应为 `True`，Python 版本应满足 >=3.11，依赖检查不应报告缺包或冲突。当前环境已包含这些依赖，正常时直接启动即可。

如果 `.venv` 不存在，先确认本机安装了 Python：

```powershell
py --version
```

若 Python Launcher 可用且版本满足要求，再创建：

```powershell
py -3 -m venv .venv
```

若没有 `py`，但 `python --version` 返回真实的 >=3.11 解释器，可改用 `python -m venv .venv`。若出现 Microsoft Store 的 “Python was not found”，应先安装并配置 Python，再创建环境；第一步失败后不要继续执行 `.venv` 安装命令。已有但损坏的 `.venv` 应先检查其基础 Python 是否存在；本指南不要求删除现有环境。

## 2. 从零启动：两个常驻窗口，建议另开一个操作窗口

窗口 1 跑 backend，窗口 2 跑 frontend；服务启动后会占用所在终端，这是正常现象。健康检查、测试和 CLI 放在额外的窗口 3。三个窗口都从仓库根目录执行。

### A. 激活虚拟环境与按需安装依赖

在窗口 1 输入：

```powershell
cd D:\yiliao\medical-track-task-4-2
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 报禁止运行脚本，只为**当前窗口**设置执行策略，再执行激活命令：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

无需管理员窗口或永久修改系统策略。激活成功通常显示 `(.venv)`。现有启动脚本直接调用 `.venv` 的 Python，因此激活并非启动的必要条件。

仅在缺依赖、首次创建环境或安装检查失败时执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
```

### B. 窗口 1：启动 backend

```powershell
.\scripts\start_backend.ps1
```

正常日志包含 `Application startup complete` 和 `Uvicorn running on http://127.0.0.1:8000`。保持窗口打开。脚本会在存在时加载 `web/backend/.env`。

### C. 窗口 2：启动 frontend

```powershell
cd D:\yiliao\medical-track-task-4-2
.\scripts\start_frontend.ps1
```

若这个窗口也禁止运行脚本，先执行同样的 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`。正常日志包含 `Serving HTTP on 127.0.0.1 port 8765`。保持窗口打开。

### D. 打开网址

- 前端：**http://127.0.0.1:8765/**
- 后端健康：**http://127.0.0.1:8000/api/health**
- API 文档：**http://127.0.0.1:8000/docs**

后端 `http://127.0.0.1:8000/` 返回 404 是正常的：页面由另一个服务器提供。`/favicon.ico` 的 404 也不代表业务失败。

如只是不想执行 PowerShell 脚本，可用等价命令：

```powershell
# 窗口 1；仅在 web/backend/.env 存在时使用 --env-file
.\.venv\Scripts\python.exe -m uvicorn web.backend.app:app --host 127.0.0.1 --port 8000 --env-file web/backend/.env
```

```powershell
# 窗口 2
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory web/dist
```

如果 `.env` 不存在，手动后端命令去掉 `--env-file web/backend/.env`；优先使用现有脚本，它已处理这一情况。

## 3. .env 配置：只在本地填写

位置：`D:\yiliao\medical-track-task-4-2\web\backend\.env`。不要把真实 Key 放到浏览器、聊天或 Git，也不要通过输出整个 `.env` 来排查。

需要配置时，在服务尚未启动或已经停止的窗口执行；以下命令**不会覆盖已有配置**：

```powershell
cd D:\yiliao\medical-track-task-4-2
if (-not (Test-Path -LiteralPath web\backend\.env)) {
    Copy-Item -LiteralPath web\backend\.env.example -Destination web\backend\.env
}
notepad web\backend\.env
```

模板中的变量如下。`YOUR_KEY_HERE` 只是占位符，请在你自己的记事本中替换；其余值来自当前 `.env.example`，一般保持默认：

```dotenv
DEEPSEEK_API_KEY=YOUR_KEY_HERE
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT_SECONDS=360
DEEPSEEK_MAX_RETRIES=2
DEEPSEEK_MAX_OUTPUT_TOKENS=18000
DEEPSEEK_CHUNK_MAX_OUTPUT_TOKENS=30000
DEEPSEEK_PAIN_MAX_OUTPUT_TOKENS=10000
DEEPSEEK_CARE_MAX_OUTPUT_TOKENS=12000
LONG_CASE_PAGE_THRESHOLD=8
LONG_CASE_CHARACTER_THRESHOLD=24000
LONG_CASE_CHUNK_MAX_PAGES=6
LONG_CASE_CHUNK_MAX_CHARACTERS=18000
LONG_CASE_CHUNK_MAX_OCR_BLOCKS=600
LONG_CASE_CACHE_ENABLED=true
```

可选环境变量 `COMPETITION_DB_PATH` 由 `src/medical_claims/storage.py` 使用；不设置时使用根目录 `outputs/competition.db`。第一次接手建议保留默认数据库位置。若队友已有自定义配置，backend 与 CLI 必须使用相同数据库位置，避免“同一个 case_id 读不到”的问题。

修改 `.env` 后需重启 backend。Key 缺失时仍可启动两端、健康检查、创建/读取空案件、验证 SQLite、加载品质和闭环页面、运行隔离回归测试；不需要为这些操作先申请 Key。

RapidOCR 本身是本地 OCR，但页面“运行OCR与结构化”会接着调用 DeepSeek，**没有 Key 不能完成真实结构化**。对已有合法结构化结果，痛点可以规则降级，温度指引可以安全降级并要求复核；这不能算真实 DeepSeek Care Guide 成功。空案件不能直接生成关怀或完成触达。

## 4. 健康检查：检查真实字段

在窗口 3 输入：

```powershell
cd D:\yiliao\medical-track-task-4-2
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' | ConvertTo-Json -Depth 6
```

也可以直接在浏览器打开健康地址。

| 返回字段 | 正常关注点 | 实际含义 |
|---|---|---|
| `status` | `ok` | 后端健康路由响应成功 |
| `revision` | 当前代码为 `2026-10-06-competition-hardening` | 识别是否仍运行旧后端进程 |
| `ocr` | `rapidocr-ready` | RapidOCR 包能导入；**没有检查模型推理成功** |
| `llm` | 有配置时 `deepseek-ready`；无配置时 `deepseek-key-missing` | 只判断变量是否非空，不验证 Key 有效性、账户或网络 |
| `examinations_schema`、`cost_semantics_schema`、`medication_instruction_schema` | 都为 `true` | 当前 schema 特性；页面连接检查会核对它们 |
| `ocr_review_workflow`、`case_persistence_workflow` | `true` | OCR 复核和案件持久化模块存在 |
| `pain_point_engine`、`care_guide_engine` | `true` | 痛点、关怀模块存在 |
| `quality_profile_engine`、`explainable_recommendation_engine`、`case_quality_integration` | `true` | 品质、推荐及案件连接模块存在，不代表历史数据足够 |
| `risk_delivery_feedback_loop` | `true` | 风险、触达、反馈闭环模块存在 |
| `long_case_chunking`、`long_case_resume_cache` | 默认 `true` | 长病例分块及缓存配置 |
| `long_case_page_threshold`、`long_case_chunk_max_pages`、`long_case_chunk_max_ocr_blocks` | 默认 `8`、`6`、`600` | 当前分块参数 |

**没有名为 `backend`、`SQLite` 或 `database` 的健康返回字段。** 上述模块布尔值不是完整运行探针；SQLite 要用下一节的创建、读取和重启后读取确认。

## 5. 页面启动验证：使用当前按钮

1. 打开 `http://127.0.0.1:8765/`。若怀疑缓存旧页面，按 `Ctrl+F5`。
2. 点击右上角连接按钮：首次通常显示 **“演示模式”**；保存过地址时可能显示 **“本地服务已连接”**。
3. 在 **“本地后端地址”** 填 `http://127.0.0.1:8000`。不要填前端 8765，也不要附加 `/api` 或 `/docs`。
4. 点击 **“检测连接”**，查看 OCR、LLM 和版本；`deepseek-key-missing` 对纯工程验证可以接受。
5. 点击 **“保存配置”**。检测连接和保存地址是两个操作。
6. 在 **“案件工作台”**，保留 **“授权未确认”**、**“脱敏未确认”**，点击 **“创建新案件”**，记录生成的 UUID。空案件应为 `RECEIVED`。
7. 点击 **“读取案件 ID”** 验证读取；切换 **“品质推荐”**，点 **“读取画像与推荐”**；切换 **“触达与反馈”**，点 **“读取当前案件闭环记录”**。空内容、未评估和无触达记录是正常现象。

“创建新案件”会清空当前页面的旧产物和待上传材料，因此真实操作时务必先创建，再添加图片。

## 6. 没有真实图片：安全的工程验证

### 创建一个空案件并验证 SQLite

在窗口 3 执行以下命令。这里只保存工程验证元数据，不创建虚假诊断，也不声明真实医疗授权：

```powershell
cd D:\yiliao\medical-track-task-4-2
$engineeringPayload = @{
    claim_event_id = 'engineering-' + [guid]::NewGuid().ToString()
    data_source = 'engineering_validation'
    consent_status = 'unknown'
    deidentification_status = 'unknown'
} | ConvertTo-Json
$createdCase = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/workflow/cases' -ContentType 'application/json' -Body $engineeringPayload
$engineeringCaseId = $createdCase.case_id
$engineeringCaseId
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$engineeringCaseId" | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$engineeringCaseId/result" | ConvertTo-Json -Depth 8
```

正常：创建返回 UUID，读取成功，状态 `RECEIVED`，OCR/结构化/痛点/关怀产物尚为空。保存这个 UUID，在窗口 1 停止并重新启动 backend，然后在**仍打开的窗口 3**重复 GET，确认记录仍在。不要删除或重建 `outputs/competition.db`。

把这个 UUID 填入页面案件编号，点击“读取案件 ID”，便能在页面看到同一个案件。默认 `DEMO-RESP-001` 不是自动创建的 SQLite 案件。

可以继续做只读 API 验证：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/quality/portfolio' | ConvertTo-Json -Depth 12
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/quality/coverage' | ConvertTo-Json -Depth 12
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$engineeringCaseId/deliveries" | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$engineeringCaseId/feedback" | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/workflow/cases/$engineeringCaseId/follow-ups" | ConvertTo-Json -Depth 6
```

无历史画像时 portfolio 返回 `not_evaluated`；已构建但不足时可能为 `insufficient_evidence`。没有触达/反馈/待办时数组为空。空案件点击“准备触达 / 更新风险”得到缺少 Care Guide 的 409，是流程校验正确，不能批准或发送空内容。

### 用现有 fixture 验证风险、模拟触达和反馈

运行现有测试，不把 fixture 导入正在使用的数据库：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_competition_workflow.py tests/test_quality_integration.py tests/test_delivery_feedback_loop.py tests/test_competition_hardening.py -q --tb=short
```

这些测试用临时数据库和 synthetic/fixture 数据；OCR/LLM 相关场景使用替身，不需要真实图片或外部模型调用。覆盖案件持久化、品质集成、AUTO/REVIEW/BLOCKED 风险分支、BLOCKED 不可人工绕过、模拟触达、反馈保存和 Follow-up。测试结束后 fixture 不会成为页面当前 SQLite 中的用户案件。

页面 **“载入演示案件”** 仅检查固定样例的显示，已明确标识未运行 OCR/AI；它不能替代真实案件或完整服务闭环。**“批量中心”** 当前禁用是 Phase D 的预期行为，不应出现假完成量、假置信度。

上述全部是**工程验证**，不能证明真实 OCR、DeepSeek、医疗准确性或 KR1–KR4 达标。没有真实医疗产物时，不要求你在页面强行完成模拟触达；闭环逻辑先由隔离测试验证。

## 7. 有授权脱敏图片：CLI 真实 E2E

先阅读根目录 `REAL_E2E_CHECKLIST.md`。准备同一案件 1–3 张清晰 JPG/PNG；代码也接受 JPEG、BMP、WebP。当前不支持直接上传 PDF/Word，需要先转成图片。

操作者核验授权包括测试用途和把 OCR 文本发送到现有 DeepSeek 服务。去除姓名、证件号、联系方式、住址、条码/二维码等身份信息，使用中性文件名；保留可核验诊断、日期、药品剂量/频次和费用。程序 flags 不会自动证明已经授权或脱敏。

```powershell
cd D:\yiliao\medical-track-task-4-2
New-Item -ItemType Directory -Path work\real_e2e_inputs -Force
```

由你手动把已核验的图片放到这个目录。例如确有 `document-1.jpg` 时：

```powershell
.\.venv\Scripts\python.exe scripts/run_real_e2e.py --images work/real_e2e_inputs/document-1.jpg --authorized --deidentified
```

如确有第二张材料，完整命令为：

```powershell
.\.venv\Scripts\python.exe scripts/run_real_e2e.py --images work/real_e2e_inputs/document-1.jpg work/real_e2e_inputs/document-2.png --authorized --deidentified
```

- `--authorized`：你声明已核实使用授权，包含所需外部 LLM 处理。
- `--deidentified`：你声明已经完成脱敏核验。
- `--images`：列出实际存在的图片路径；不要复制并不存在的第二个文件名。

脚本仅有以上三个参数，不需要 `--output` 或 `--case-id`。它直接复用后端函数，**不要求启动 uvicorn 或前端**；读取 `web/backend/.env`，创建新案件，使用同一个默认 SQLite，不清空现有数据。若 backend 使用自定义 EnvFile/数据库路径，要确保 CLI 也使用一致配置。

实际流程：创建 Case → RapidOCR → DeepSeek 结构化 → 痛点 → Care Guide → Risk Gate → 重开 SQLite 核对。此次验证关闭长病例缓存，避免缓存代替真实结构化调用。脚本不发送模拟消息，也不自动提交反馈；这两步由页面完成。

产物在 `outputs/e2e/<UTC timestamp>/`：

```text
run_metadata.json
ocr.json
claim_case.json
pain_report.json
care_guide.json
risk_gate.json
```

查看 `run_metadata.json` 的 `case_id`、`status`、`latencies_seconds`、`token_usage`、warnings 及最终 workflow 状态。`token_usage=null` 表示 API 未提供或没有实际调用，不要解释为零成本。失败时只保留已完成的产物。

| 运行状态 | 含义 |
|---|---|
| `BLOCKED_BY_TEST_DATA` | 缺少有效图片；尚未完成真实运行 |
| `BLOCKED_BY_AUTHORIZATION` | 未声明已授权且已脱敏 |
| `BLOCKED_BY_CONFIGURATION` | 缺少 DeepSeek Key |
| `FAILED` | 查看实际失败阶段和错误 |
| `EXECUTED_WITH_FALLBACK` | 已执行，但关怀发生降级，不算目标 LLM 全流程成功 |
| `EXECUTED` | 实际 LLM 结构化、LLM 关怀及 SQLite 重读已完成；不代表医学正确、风险通过或比赛指标达标 |

即使脚本 `EXECUTED`，Risk Gate 仍可能是 `REVIEW_REQUIRED` 或 `BLOCKED`。本地原图/OCR/医疗结果保持在既有 ignored `work/`、`outputs/`，未经允许不要提交到 Git。

## 8. 从网页跑真实案件完整链路

两端启动并连接后，按以下顺序操作。也可跳过 1–6，把 CLI 的 `case_id` 填入页面并点“读取案件 ID”，再从已保存结果继续。

| 步骤 | 当前真实 UI 操作 | 应检查什么 |
|---|---|---|
| 1. 创建案件 | “案件工作台”中，在确已核验后选“已获授权”“已确认脱敏”，点“创建新案件” | 记录 UUID，不使用默认 demo 编号 |
| 2. 上传 | 点“添加图片”，选择同一案件图片 | JPG/JPEG/PNG/BMP/WebP；添加材料后尚未识别，显示等待本地 OCR 是正常的 |
| 3. OCR + 结构化 | 点“运行OCR与结构化”，等待后台完成 | RapidOCR 和 DeepSeek 必须实际成功；等待期间不要切案或重复点击 |
| 4. 查看结果 | 看“关键字段与证据”、事件时间线，点“查看 JSON” | 对照原图核对诊断、日期、药物数字、费用与证据；`STRUCTURED` 仅表示保存完成 |
| 5. 痛点 | 切换“痛点洞察”，点“分析当前病例” | 结果属于当前 UUID，查看证据、警告和降级说明 |
| 6. 关怀 | 切换“温度指引”，点“生成温度指引” | 六类内容、生成方式、warnings、安全结果；`CARE_READY` 不等于已获批准 |
| 7. 画像/推荐 | 切换“品质推荐”，点“读取画像与推荐” | 无可信历史数据或确认来源时，`Not Evaluated` / `Insufficient Evidence` / unavailable 是正常拒绝推荐 |
| 8. Risk Gate | 切换“触达与反馈”，点“准备触达 / 更新风险” | 看风险理由及 AUTO_DELIVER / REVIEW_REQUIRED / BLOCKED |
| 9. Mock Delivery | 允许发送时点“发送至模拟收件箱” | 出现 `mock_web_inbox` 记录，表示模拟页面触达，不是短信/APP |
| 10. Feedback | 对选中的触达记录填写 Target、Rating、Helpful、Category 和意见，点“提交反馈” | 真实保存 feedback；无可用推荐时不要选择 recommendation 反馈目标 |
| 11. Follow-up | 在“服务复核待办 · Open / Resolved”查看任务；实际处理后填写结果，点“Resolve” | OPEN → RESOLVED；刷新/重读后记录仍在 |

### OCR 复核与重新结构化

“查看或修正 OCR 文本”可编辑页文本。“保存本页修正”不是重新运行结构化；要应用到结构化结果需点“应用修正并重新结构化”，仍需 DeepSeek。重新结构化会使旧痛点、关怀及关联结果失效，要重新执行后续步骤。读取历史案件能恢复 OCR 文本，但上传原图预览不作为持久化原图保存，预览为空不等于结构化记录丢失。

### 品质推荐不足不是系统坏了

当前没有足够真实 KR4 历史数据时，不能要求医院/医生/方案榜单必然有结果。未构建 portfolio 时可能是 `Not Evaluated`；已构建但样本/来源不足时为 `Insufficient Evidence`。不需要为了页面出结果去编造历史资料、医院、医生或评分。

“构建已保存历史画像”只使用已保存历史记录，不会创造真实历史数据。“载入真实审计”是另一项读取预生成审计文件的操作；没有相应文件可能返回 404，不影响主要的“读取画像与推荐”接口。第一次运行优先验证后者。

### 风险与人工复核

- `AUTO_DELIVER`：免人工批准，但仍须你点发送按钮；没有后台定时发送。
- `REVIEW_REQUIRED`：由实际负责审核的团队成员检查内容，在“团队人工复核”填写审核者、复核说明，选择合适目标（默认 `combined_delivery`），点击 `Approve` 或 `Reject`。没有可用推荐时，不单独批准 `recommendation`。通过后再准备/确认风险状态并发送。
- `BLOCKED`：不能人工批准绕过，也不能发送。记录安全问题，交医学成员核验；不要改数据库或删警告来放行。

重复发送相同内容可能被幂等控制禁止，这是正常行为。低评分（<=3）、Helpful=No 或 Category 非 `clear` 会触发 Follow-up；高评分、Helpful=Yes、Category=`clear` 可能不产生任务。不能为了验证按钮，冒称已经完成真实用户服务；Resolve 要填写实际处理结果。

反馈保存后点“读取当前案件闭环记录”，或刷新后输入同一 UUID 再读取，确认 inbox、feedback、follow-up 仍存在。切换另一个 UUID 时旧案件结果应立即清空。

## 9. 正常结果与常见异常

| 步骤 | 正常现象 | 常见错误 | 怎么处理 |
|---|---|---|---|
| backend 启动 | startup complete，8000 监听 | `.venv` 不存在、模块缺失、脚本被禁、端口占用 | 检查解释器；按需安装 `.[api,dev]`；当前窗口设置执行策略；停止你已打开的旧服务。也可用 `start_backend.ps1 -Port 8001`，同步改页面后端地址 |
| frontend 启动 | 8765 Serving HTTP，页面可打开 | 路径错误、旧静态文件、端口占用 | 使用根目录现有脚本；Ctrl+F5；关闭你自己的重复 frontend。优先保留 8765：后端 CORS 当前允许 8765，不能只改前端端口就认为跨域也可用 |
| 健康连接 | `status=ok`，当前 revision | 页面说旧版本、连接失败 | 重启正确目录的 backend；核对端口和浏览器地址，不填 `/api/health` 作为后端基础地址 |
| RapidOCR | health 为 ready，真实识别产生页文本和 blocks | missing、ONNX/模型初始化失败 | 用项目 Python 检查 rapidocr/onnxruntime 依赖；看后端具体错误与模型文件问题；ready 仅代表可导入，不能冒称已识别 |
| DeepSeek 配置 | 有配置时 ready | `deepseek-key-missing` | 在本地 `.env` 填 Key 后重启；工程启动无需 Key；占位符非空也可能显示 ready，不能据此认为有效 |
| DeepSeek 请求 | 实际返回结构化/关怀 | 401/403、额度/限流、502/504、超时或响应校验错误 | 核对有效授权、账户和网络；看日志中的状态/失败阶段，避免分享完整医疗请求或密钥；不要把降级当成功 |
| Case 读取 | 返回同一 UUID 保存记录 | `case_id` 404 | 使用真实创建或 metadata 里的 UUID；检查是否误用 demo 编号，以及 backend/CLI 是否使用不同 DB |
| Evidence | 找到对应 OCR 页与实体证据 | `review_required` / evidence verification failed | 对照原图和页文本核验，必要时修正 OCR 后重新结构化；保留警告，不将其隐藏或当作已确认 |
| Care Guide / Risk | 安全结果允许或要求复核 | `BLOCKED` | 阅读具体安全原因并由医学成员核验；BLOCKED 不可批准绕过 |
| 推荐 | 有数据才可用；不足明确拒绝 | `unavailable` / `insufficient_evidence` / `not_evaluated` | 无真实 KR4 历史或确认上下文时正常；不要导入虚构数据；关怀可能仍可按其自身风险完成 care-only 触达 |
| 准备触达 | 已保存 Care Guide 后得到风险结果 | 空案件 409 | 先完成真实结构化、痛点和关怀；无图片时用隔离测试验证逻辑，不强行发送 |
| SQLite | 创建、读取、重启后同 UUID 仍可读 | 无写权限、磁盘不足、database locked、自定义路径不一致 | 检查 outputs/配置路径权限与磁盘；等待其他写入完成后重试，避免同案并发操作；保留数据库，不删除它 |
| Feedback / Follow-up | 已触达后可反馈，需复核时出现 OPEN | 无触达时按钮禁用、好评无任务 | 先有合法模拟触达；符合触发条件才有任务，无任务不代表故障 |

## 10. 安全停止服务

等正在处理的 OCR/LLM/保存请求完成，再在 backend 窗口按 `Ctrl+C`，等待 shutdown 完成；在 frontend 窗口按 `Ctrl+C`。不要在写入过程中强制结束 Python。

如果激活过虚拟环境，可以执行 `deactivate`，或正常关闭空闲窗口。SQLite 文件会保留，下次启动继续使用；不需要停数据库服务，也不要删除 `outputs/competition.db`。

## 11. 正式回归测试命令

在窗口 3、仓库根目录运行：

```powershell
cd D:\yiliao\medical-track-task-4-2
.\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Phase D 的历史验收记录为 **175 passed / 0 failed**；本次文档任务没有重新运行，以你本机实际输出为准。第三方 deprecation warning 不等于测试失败。若出现临时目录 PermissionError，先检查本机临时目录权限/安全软件；不要删除当前数据库来解决测试问题。

如果你已安装 Node 且在 PATH 中：

```powershell
node --version
node tests/frontend_hardening.test.cjs
```

Phase D 历史记录为 **25 checks passed / 0 failed**。这些是 mock DOM/API hardening 检查，不能代替真实浏览器操作。若 `node` 无法识别，可跳过此项；这不会阻止 Python 后端与静态前端启动，不需要为了运行项目安装新框架。

## 12. 最短运行路线

### 5 分钟工程验证：只确认能启动

1. 检查 `.venv\Scripts\python.exe --version` 和依赖；环境已备妥时无需安装。
2. 窗口 1 执行 ` .\scripts\start_backend.ps1`，窗口 2 执行 ` .\scripts\start_frontend.ps1`。
3. 浏览器检查 `/api/health`，打开 `http://127.0.0.1:8765/`。
4. 页面填后端 `http://127.0.0.1:8000`，检测连接并保存。
5. 创建一个授权/脱敏状态未知的空案件，读取同一 UUID。无需 Key 或图片。

验收范围：两端启动、路由连接、Case 创建/读取。不是医疗 E2E。首次安装依赖可能超过 5 分钟。

### 15 分钟功能验证：无真实医疗图片

1. 完成 5 分钟路线。
2. 按第 6 节 API 命令创建空工程案件；重启 backend，再读同一 UUID 验证 SQLite。
3. 页面读取该 UUID，检查“品质推荐”“触达与反馈”；未评估、无记录和空案件不准触达均属正确状态。
4. 运行第 6 节四个测试文件，验证隔离 fixture 的风险/复核/模拟触达/反馈/待办流程。
5. 如有 Node，再运行前端 hardening 检查。可查看固定 demo 的显示，但明确这是样例。

验收范围：主要工程模块和流程约束。不会证明真实 OCR/DeepSeek 输出，也不会把 fixture 写入页面使用的数据库；耗时取决于本机。

### Real E2E：授权图片 + DeepSeek

1. 准备授权脱敏图片，放到 `work/real_e2e_inputs/`；在本地配置有效 DeepSeek Key。
2. 按第 7 节真实参数运行 `scripts/run_real_e2e.py`，查看实际阶段、时延、风险和 SQLite 重读记录。
3. 启动两端，在页面输入 metadata 的 UUID，读取已保存案件。
4. 人工对照原图核验 OCR/结构化/证据/痛点/关怀；品质不足如实显示，不补假历史。
5. 准备触达，按真实风险要求复核；允许时发送模拟收件箱，提交反馈并核实保存，必要时完成实际 Follow-up。
6. 刷新/重启后重读同案结果；切换另一个 UUID 验证隔离。

只有这一条路线包含真实医疗 OCR/LLM；它仍不替代正式比赛指标评测和医学安全审核。
