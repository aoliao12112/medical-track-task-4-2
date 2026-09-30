# Medical Track Task 4-2

基于理赔医护记录的“医护洞察与温度指引”引擎原型。

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
tests/test_pipeline.py            # 使用假 Provider 的接口测试
examples/synthetic_001.json       # 空白案件输出示例
web/dist/                         # 第一问网页演示与批量评测界面
web/backend/app.py                # RapidOCR + DeepSeek 本地接口
```

## 网页演示

网页包含单病例完整链路、批量病例中心和字段级 F1 评测入口。未连接后端时使用脱敏演示病例或手工 OCR 文本；连接本地后端后，图片由 RapidOCR 在本机识别，DeepSeek 仅接收 OCR 后的文本。

具体启动方式见 `web/backend/README.md`。真实 API Key 只能放在后端环境变量中，禁止写入前端或提交到仓库。

## 正式字段级 F1 评测

预测结果和专家金标准必须分别标注，禁止把预测结果复制为金标准。网页评测采用保留重复字段的
微平均精确匹配；命令行可运行：

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
