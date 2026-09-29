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
  "costs": {},
  "events": [],
  "quality_flags": []
}
```

核心字段使用统一的 `FieldValue` 包装，记录字段值、标准化值、状态、
置信度、原文证据与告警。`OcrProvider`、`MedicalNlpProvider` 和
`LlmProvider` 均为可替换接口，待官方 AI 中台开放后接入具体实现。

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
