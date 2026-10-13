# 官方 KR1 金标准转换说明

## 权威来源

正式 KR1 金标准来源为仓库根目录：

`关于基于理赔医护记录的“医护洞察与温度指引”引擎 KR1补充材料.zip`

ZIP 内含 70 个病例、728 个状态为 `success` 的分页 JSON，顶层目录为“命题材料提取结果”。
这些文件是官方分页提取结果，不是现有评测器可直接读取的病例级 `ClaimCase`。

## 转换方法

运行：

```powershell
python scripts/convert_official_kr1_gold.py --official-source-confirmed
```

转换器执行以下确定性映射：

- 入出院日期、住院天数和科室 → `hospitalization`；
- 主诊断、入院诊断、出院诊断及一般诊断 → 去重后的 `diagnoses`；
- 处方笺 `medication` → 只拆分有明确边界的药名；
- 检查/病理报告 → `examinations` 及结论；
- 医保结算单与费用明细 → 总费用、医保范围、个人负担、支付渠道和费用分类；
- 患者姓名、性别、年龄等非 `ClaimCase` 评测字段不写入转换后的 gold。

转换器不会读取模型预测，也不会从诊疗经过等自由文本推测手术、剂量、频次或给药途径。
官方包没有独立手术字段，因此生成结果的 `procedures` 为空；这属于官方源字段边界，不得人工补造。

## 输出

当前正式目录：`outputs/official_kr1_gold_v2/`

- `official-kr1.gold.final.json`：70 病例聚合 gold；
- `<case_id>/gold/gold.final.json`：单病例 gold；
- `<case_id>/gold/provenance.json`：来源文件、映射字段和未映射字段名，不复制患者字段原值；
- `official-kr1.manifest.json`：源 ZIP 哈希、转换器版本、数量和转换边界。

`outputs/` 被 Git 忽略，正式 gold 不应提交到公开仓库。根目录官方 ZIP 当前由项目版本管理；若赛事数据合规要求变化，应转为受控存储。

## 评测

```powershell
python -m medical_claims.evaluation `
  outputs/prediction.json `
  outputs/official_kr1_gold_v2/official-kr1.gold.final.json
```

转换后的 gold 已通过 `ClaimCase` Schema 校验，70 个 case_id 唯一，且用同一评测器进行
自一致性检查为 1.0。自一致性只证明格式和实体对齐没有歧义，不能冒充模型的正式 F1 成绩。
