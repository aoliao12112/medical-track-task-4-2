# Medical Track Task 4-2

基于理赔医护记录的“医护洞察与温度指引”引擎原型。

团队安装、共享 DeepSeek Key、本地启动以及全部接口调用示例见
[`docs/team-setup-and-api-guide.md`](docs/team-setup-and-api-guide.md)。真实密钥、病例、OCR缓存和
身份映射禁止上传 GitHub。

## 团队

- 1 名医学生
- 2 名软件相关专业学生

## 初步计划

- 周期：7 天
- 阶段 1：完成 A
- 阶段 2：并行完成 B、C
- 阶段 3：完成 D，并进行整合与交付

后续将在明确题目需求后补充技术方案、任务分工与每日里程碑。

## 当前范围：第一问

当前骨架实现“病历结构化与医疗事件重建”的领域模型和编排接口：

```text
医疗文档 → OCR → 医学实体抽取 → LLM 跨文档结构化
        → 确定性质量校验 → 标准 JSON/医疗事件时间线
```

顶层输出字段为：

```json
{
  "case_id": "synthetic_001",
  "encounter_type": "inpatient",
  "hospitalization": {},
  "diagnoses": [],
  "procedures": [],
  "medications": [],
  "examinations": [],
  "costs": {},
  "events": [],
  "quality_flags": []
}
```

核心字段使用统一的 `FieldValue` 包装，记录字段值、标准化值、状态、
置信度、原文证据、推导公式与告警。`OcrProvider`、`MedicalNlpProvider` 和
`LlmProvider` 均为可替换接口，待官方 AI 中台开放后接入具体实现。

### 费用字段口径

费用不能只存一个含义模糊的“个人支付”。当前 Schema 明确分为：

- 费用口径：总费用、医保目录内费用、医保目录外费用；
- 基金及第三方支付：基本医保统筹、大病保险、医疗救助、补充医疗保险、商业保险等；
- 个人负担属性：目录内个人自付、目录外个人自费、个人负担合计；
- 实际付款渠道：个人账户、家庭共济账户、现金。

只有材料明确记载的金额标记为 `extracted`；按完整算式计算出的字段标记为
`derived` 并记录 `derivation`。缺少计算项时不得按 0 处理。

### 第一问的诊断边界

第一问只忠实提取病历中的诊断、诊断类型、确定性和原文证据，并检查跨文档冲突。
它不判断“医生诊断不合理”“医生错误”或“治疗不当”。确有证据冲突时，仅生成
`DIAGNOSIS_DOCUMENT_CONFLICT` / `DIAGNOSIS_EVIDENCE_REVIEW` 复核标记；临床合理性分析属于后续痛点识别模块。

### OCR人工复核闭环

真实材料处理后，网页会保留每页OCR文本和平均置信度。复核人员可对照原图修正模糊字、
数字、单位、日期和否定词，再使用修正文本重新结构化；此过程不会重复运行OCR。任何OCR修正
都会清空旧的痛点报告和温度指引，要求基于更新后的病例重新生成，避免下游继续引用旧事实。

## 本地开发

项目要求 Python 3.11 或更高版本。请勿使用系统中的 Python 2.7。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

在尚未安装 `pytest` 时，也可以用标准库运行当前测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 目录

```text
src/medical_claims/models.py      # JSON Schema 和领域模型
src/medical_claims/providers.py   # OCR、医学 NLP、LLM 接口
src/medical_claims/pipeline.py    # 端到端编排
src/medical_claims/validation.py  # 日期、住院天数和费用一致性校验
src/medical_claims/pain_models.py # 第二问痛点JSON Schema
src/medical_claims/pain_rules.py  # 费用、用药、检查与体验确定性规则
src/medical_claims/pain_engine.py # 规则与DeepSeek候选校验、合并和去重
src/medical_claims/pain_evaluation.py # 痛点与专家标注一致率评测
src/medical_claims/care_models.py # 第三问温度指引JSON Schema
src/medical_claims/care_prompt.py # 病例事实包与生成上下文
src/medical_claims/care_engine.py # 生成后安全校验与降级输出
src/medical_claims/care_evaluation.py # 温度感与可行动性盲评汇总
src/medical_claims/provider_models.py # 第四问脱敏医院/医生角色上下文
src/medical_claims/provider_extraction.py # 医院、医生角色和病种覆盖提取
src/medical_claims/quality_models.py # 第四问品质画像与推荐JSON Schema
src/medical_claims/quality_engine.py # 六维画像聚合、准入门槛和解释性推荐
knowledge/care_guides/ # 三个验收病种知识卡 + 肺结节演示卡（医学审核前均为草稿）
tests/test_pipeline.py            # 使用假 Provider 的接口测试
examples/synthetic_001.json       # 空白案件输出示例
web/dist/                         # 第一问网页演示与批量评测界面
web/backend/app.py                # RapidOCR + DeepSeek 本地接口
```

## 第二问：多维痛点识别

后端启动后，将第一问的 `ClaimCase` 发送到：

```text
POST /api/cases/analyze-pain-points
```

请求体为 `{"case": <ClaimCase>, "use_llm": true}`。本地规则负责费用比例、费用勾稽、
用药说明缺失、重复用药和异常检查等可确定判断；DeepSeek 只生成需要语义理解的候选。
所有 LLM 证据都必须与第一问 evidence 匹配，否则会被本地引擎丢弃。输出只用于关怀与
医学复核，不作医疗责任结论，也不建议用户自行停药或改变治疗。

痛点引擎 v3 会将同类异常检验和重复诊断术语归并，排除费用清单中的“缺少用法”误报，
过滤低置信度 OCR 候选，并将单病例展示控制在最多 12 条高优先级结果。该展示上限不改变
底层结构化数据，也不能替代后续医学专家标注。

第二问专家一致率评测：

```powershell
python -m medical_claims.pain_evaluation `
  outputs\CASE-ID\pain\pain.prediction.json `
  outputs\CASE-ID\pain\pain_gold.final.json
```

## 第三问：温度指引生成

网页按“第一问病例 → 第二问痛点 → 第三问指引”顺序运行。后端接口为：

```text
POST /api/cases/generate-care-guide
```

请求体包含 `case`、`pain_report`、可选 `disease_topic` 和 `use_llm`。DeepSeek 只接收压缩后的
病例事实包和知识卡，不接收原始图片；生成草稿随后经过证据路径、医学边界、知识卡审核状态和
安全提示校验。模型调用失败时返回明确标注的 `rules_fallback` 草稿，不把降级内容冒充正式结果。

当前提供肺炎、骨折和糖尿病三个知识卡草稿。正式演示前必须按照
`docs/third-question-manual-checklist.md` 由医学人员复核，并将 `review_status` 改为 `reviewed`。

盲评填写 `evaluation/templates/care-ratings.template.json` 的同结构数组后运行：

```powershell
python -m medical_claims.care_evaluation evaluation\care-ratings.json
```

通过条件为温度感和可行动性平均分均不低于 4.0，且事实错误与不安全建议均为 0。

## 第四问：品质画像数据审计

正式构建医院、医生和治疗方案画像前，先运行本地机构覆盖审计：

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\audit_provider_coverage.py'
```

脚本只读取压缩包内的图片，明确跳过 `_manifest.json`，且不调用外部大模型。医院、医生及
文档编号均采用本地 HMAC 脱敏；可展示结果写入 `work/provider_audit/`，真实名称映射、密钥与
OCR 缓存只留在其 `private/` 子目录。完整边界、输出说明和人工复核要求见
`docs/fourth-question-provider-audit.md`。

案件文件夹编号只代表一次案件，不能当作稳定患者编号；报告医师、检验者和审核者也不能冒充
经治医生进入排名。未经抽样复核的结果只能称为“候选机构上下文”，不能直接发布排行榜。

后端同时提供 `/api/quality/profiles/build` 和 `/api/quality/recommendations`。六维口径、拒绝推荐
规则、离线批处理命令及还需人工完成的内容见 `docs/fourth-question-quality-profile.md`。

## 网页演示

网页保留明确标记的固定演示样例；真实处理需连接后端并创建/读取案件，图片由 RapidOCR 在本机识别，DeepSeek 接收 OCR 文本。Phase D 已禁用未接后端的 Batch，不再用本地模板冒充图片处理成功；PDF/Word 未启用。

具体启动方式见 `web/backend/README.md`。真实 API Key 只能放在后端环境变量中，禁止写入前端或提交到仓库。

长病例无需人工拆分。超过阈值后，后端自动执行“逐页OCR → 分块结构化 → 跨块去重合并 → 全局一致性与证据校验”。当前默认分块上限为6页/18000 OCR字符/600个OCR块，具体参数与运行说明见 `web/backend/README.md`。

## 正式字段级 F1 评测

预测结果和专家金标准必须分别标注，禁止把预测结果复制为金标准。Phase D 网页通过 `/api/evaluation/structured` 调用唯一 Python 评测：先按 case_id 对齐，再确定性匹配实体，在实体内比较字段；不能跨病例或跨药品池化。
输出 micro/macro、per-case/per-field 指标。关键字段缺失率只计应有而缺失的字段，不等于 1-recall；完整口径见 `COMPETITION_HARDENING_REPORT.md`。命令行可运行：

```powershell
python -m medical_claims.evaluation outputs/prediction.json outputs/gold.json
```

评测前会统一空白、常见中文标点及 `qd/bid/tid` 等频次表达，但不会使用模糊语义匹配。

病例材料建议统一按以下结构保存，避免原始材料、预测结果和金标准混放：

```text
outputs/<case_id>/
├─ source/       # 原始脱敏材料
├─ prediction/   # raw 与 normalized 预测
├─ gold/         # draft 与 final 专家标注
├─ preview/      # OCR和页面预览
└─ evaluation/   # F1评测报告
```

只有已独立复核、状态为 `reviewed` 的 `gold.final.json` 才用于对外报告正式 F1。
