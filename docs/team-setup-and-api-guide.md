# 团队环境与 API 调用说明

本文用于多名队员在不同电脑上运行同一版本代码，并共用同一个 DeepSeek 账户与 API Key。

## 1. 先明确“共用”的含义

- 两个人可以使用同一个 DeepSeek API Key、模型名称、余额和调用配额。
- 每个人仍然在自己的电脑上运行本地 OCR 和本地后端：`http://127.0.0.1:8000`。
- 不要把其中一台电脑的后端直接暴露到公网，也不要通过公网转发患者材料。
- 同时调用时会共同消耗余额、并共享账户限流。队员需要提前约定测试时间与病例范围。
- API Key 只通过密码管理器或其他私密方式传递一次，禁止提交 GitHub、写进网页、截图发群或粘贴到报告。

DeepSeek 官方入口：

- API 文档：<https://api-docs.deepseek.com/>
- API Key 管理：<https://platform.deepseek.com/api_keys>

如果 Key 泄露，立即在平台撤销并重新创建；不要只删除聊天记录。

## 2. 两台电脑统一安装

每名队员分别执行：

```powershell
git clone https://github.com/aoliao12112/medical-track-task-4-2.git
cd medical-track-task-4-2

py install 3.12
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -e '.[api,dev]'
```

如果已经克隆：

```powershell
git pull origin main
& '.\.venv\Scripts\python.exe' -m pip install -e '.[api,dev]'
```

必须使用仓库内 `.venv`，不要调用电脑上残留的 Python 2.7 或损坏的 Anaconda。

## 3. 两台电脑配置同一个 Key

每名队员在自己的电脑执行：

```powershell
Copy-Item '.\web\backend\.env.example' '.\web\backend\.env'
notepad '.\web\backend\.env'
```

只修改本地文件中的：

```dotenv
DEEPSEEK_API_KEY=你们共同使用的真实Key
DEEPSEEK_MODEL=deepseek-flash
```

两台电脑的 `DEEPSEEK_MODEL` 和长病例参数应保持一致。`web/backend/.env` 已被 `.gitignore` 排除。

也可以只在当前 PowerShell 临时设置：

```powershell
$env:DEEPSEEK_API_KEY='真实Key'
$env:DEEPSEEK_MODEL='deepseek-flash'
```

关闭该终端后临时变量会失效。

## 4. 一键启动

终端一：

```powershell
& '.\scripts\start_backend.ps1'
```

终端二：

```powershell
& '.\scripts\start_frontend.ps1'
```

打开：

- 网页：<http://127.0.0.1:8765>
- 角色门户：<http://127.0.0.1:8765/roles.html>
- FastAPI 交互文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

网页右上角填写 `http://127.0.0.1:8000` 并检测连接。

## 5. API 调用顺序

| 顺序 | 接口 | 输入 | 输出 | 是否调用 DeepSeek |
|---:|---|---|---|:---:|
| 0 | `GET /api/health` | 无 | OCR、LLM与各引擎状态 | 否 |
| 1a | `POST /api/cases/ocr-only` | 案件号 + 多张图片 | 逐页OCR待复核稿 | 否 |
| 1b | `POST /api/cases/save-reviewed-ocr` | 人工修改后的OCR文本 | 本地保存状态 | 否 |
| 1c | `POST /api/cases/restructure-from-ocr` | 已确认脱敏的修改版OCR文本 | 新 `ClaimCase` | 是 |
| 2 | `POST /api/cases/analyze-pain-points` | `ClaimCase` | `PainPointReport` | 可选 |
| 3 | `POST /api/cases/generate-care-guide` | 病例 + 痛点 | `CareGuideReport` | 可选 |
| 4a | `GET /api/quality/audit-summary` | 无 | 机构覆盖安全审计 | 否 |
| 4b | `POST /api/quality/profiles/build` | `ProfileCaseRecord[]` | 六维品质画像 | 否 |
| 4c | `POST /api/quality/recommendations` | 画像组合 + 病种 | 可解释推荐或拒绝原因 | 否 |

第四问不需要 DeepSeek 自由打分，只使用前三问结构化字段和确定性计算。

## 6. PowerShell 调用示例

以下命令建议在 PowerShell 7 中运行。`-NoProxy` 可避免本机代理误拦截 `127.0.0.1`。

### 6.1 健康检查

```powershell
$BaseUrl = 'http://127.0.0.1:8000'
Invoke-RestMethod -NoProxy "$BaseUrl/api/health"
```

`deepseek-ready` 只表示 Key 已载入，不代表余额充足或 Key 一定有效；第一次实际模型调用才会验证远端授权。

### 6.2 第一问：只在本机运行图片 OCR

多文件上传使用 `curl.exe` 最稳定：

```powershell
curl.exe --noproxy "*" -X POST `
  -F "case_id=由案件创建接口或网页生成的UUID" `
  -F "files=@D:\病例\01-门诊病历.jpg" `
  -F "files=@D:\病例\02-检查报告.jpg" `
  -F "files=@D:\病例\03-费用清单.jpg" `
  "http://127.0.0.1:8000/api/cases/ocr-only" `
  -o ".\work\case-ocr.json"
```

返回的 `documents` 包含逐页 OCR 文本、坐标和人工修正状态，`external_model_called` 必须为
`false`。先在网页逐页删除身份信息，不要直接把原始 OCR 发送到 DeepSeek。

### 6.3 第一问：保存人工修正但不调用模型

网页“保存本页修正”会自动提交当前全部页。API 请求结构为：

```json
{
  "case_id": "案件UUID",
  "documents": [
    {
      "document_id": "01-门诊病历.jpg",
      "page": 1,
      "full_text": "姓名：[已脱敏]\n诊断：肺炎",
      "blocks": [],
      "human_corrected": true
    }
  ]
}
```

提交到 `POST /api/cases/save-reviewed-ocr`。它只写入本地 SQLite，并把案件状态恢复为
`OCR_READY`、脱敏状态恢复为 `pending_review`，不会消耗 Token。

### 6.4 第一问：确认脱敏后重新结构化

准备 `work/restructure-request.json`：

```json
{
  "case_id": "案件UUID",
  "deidentification_confirmed": true,
  "manual_override_confirmed": false,
  "sensitive_pattern_findings": [],
  "documents": [
    {
      "document_id": "01-门诊病历.jpg",
      "page": 1,
      "full_text": "人工修正后的完整OCR文本",
      "blocks": [],
      "human_corrected": true
    }
  ]
}
```

若自动规则仍命中虚构占位符或非患者信息，必须人工逐项核实后才可将
`manual_override_confirmed` 改为 `true`。后端会独立扫描修改后的文本，并记录人工覆核审计。
不要仅为了通过规则而随意填写格式逼真的假身份证号。

```powershell
Invoke-RestMethod -NoProxy `
  -Uri "$BaseUrl/api/cases/restructure-from-ocr" `
  -Method Post -ContentType 'application/json' `
  -InFile '.\work\restructure-request.json'
```

### 6.5 第二问：多维痛点

请求文件结构：

```json
{
  "case": { "这里放第一问完整ClaimCase": "不是只有case_id的占位内容" },
  "use_llm": true
}
```

```powershell
Invoke-RestMethod -NoProxy `
  -Uri "$BaseUrl/api/cases/analyze-pain-points" `
  -Method Post -ContentType 'application/json' `
  -InFile '.\work\pain-request.json'
```

将 `use_llm` 改为 `false` 时只运行本地确定性规则，不消耗 DeepSeek Token。

### 6.6 第三问：温度指引

```json
{
  "case": { "第一问完整ClaimCase": "..." },
  "pain_report": { "第二问完整PainPointReport": "..." },
  "disease_topic": "肺炎",
  "use_llm": true
}
```

```powershell
Invoke-RestMethod -NoProxy `
  -Uri "$BaseUrl/api/cases/generate-care-guide" `
  -Method Post -ContentType 'application/json' `
  -InFile '.\work\care-request.json'
```

### 6.7 第四问：真实覆盖审计

```powershell
Invoke-RestMethod -NoProxy "$BaseUrl/api/quality/audit-summary"
```

该接口只读取安全报告，不读取 `work/provider_audit/private/`。

### 6.8 第四问：品质画像

请求核心结构：

```json
{
  "minimum_sample_size": 3,
  "records": [
    {
      "case": { "第一问完整ClaimCase": "..." },
      "institution_id": "HOSP-XXXXXXXXXXXX",
      "disease_group": "pneumonia",
      "clinician_ids": ["DOC-XXXXXXXXXXXX"],
      "treatment_plan_id": null,
      "stable_patient_token": null,
      "readmitted_within_30_days": null,
      "patient_feedback_score": null,
      "pain_report": { "第二问完整PainPointReport": "..." }
    }
  ]
}
```

```powershell
Invoke-RestMethod -NoProxy `
  -Uri "$BaseUrl/api/quality/profiles/build" `
  -Method Post -ContentType 'application/json' `
  -InFile '.\work\quality-profile-request.json'
```

### 6.9 第四问：可解释推荐

```json
{
  "portfolio": { "上一步完整QualityPortfolio": "..." },
  "disease_group": "pneumonia",
  "entity_type": "institution",
  "top_k": 5
}
```

接口只推荐 `eligible_for_recommendation=true` 的画像。没有符合门槛的候选时返回空列表和拒绝原因。

## 7. 常见状态码

| 状态码 | 含义 | 处理方法 |
|---:|---|---|
| 200 | 成功 | 检查输出中的待复核标记 |
| 404 | 第四问审计报告尚未生成 | 运行 `scripts/audit_provider_coverage.py` |
| 409 | 审计报告未通过隐私边界检查 | 停止使用该报告并重新审计 |
| 422 | 请求 JSON 不符合 Schema | 查看响应 `detail` 和 `/docs` |
| 500 | 本地代码、OCR或文件错误 | 查看后端终端日志 |
| 502 | DeepSeek 远端调用失败 | 检查 Key、余额、模型名和网络 |
| 504 | DeepSeek 超时 | 保留缓存稍后重试，不要重复上传大量病例 |

## 8. 团队协作规则

1. 每次开始前执行 `git pull origin main`。
2. 两人使用不同案件号前缀，例如 `ZWX-...`、`TEAM2-...`，避免结果混淆。
3. `work/`、`outputs/`、原始病例、`.env` 和 API Key 不上传 GitHub。
4. 只提交代码、测试、脱敏模板和说明文档。
5. 同一个病例不要两台电脑同时反复调用 DeepSeek；先分配病例，再分别处理。
6. 发现输出异常时保留原始预测，不要为了提高分数手改预测结果；人工结论写入独立金标准或复核文件。
