# 第二问人工复核说明

人工复核只在需要形成正式评测金标准时进行。普通网页演示不要求每个病例都有金标准。

## 复核人需要看的材料

1. 脱敏后的原始病历、费用单和结算单；
2. 第一问正式结构化结果；
3. 系统导出的 `CASE-ID.pain-points.json`；
4. 待复核的 `pain_gold.candidate.json`。

## 医学复核内容

对每条候选痛点确认：

- 痛点是否真实存在于当前材料；
- 维度是否属于临床、费用或体验；
- 严重程度是否合适；
- 引用的原文证据是否准确；
- 建议动作是否安全，是否存在诱导自行停药或改变治疗的问题。

复核人可以新增漏检痛点，也可以删除误报。不得直接复制系统预测作为金标准。

## 表述边界

- 可以写“用药与诊断对应关系需要医学复核”；
- 不写“医生用药错误”或“治疗不合理”；
- 可以写“费用构成存在差异，建议核对”；
- 不写“医院乱收费”；
- 可以写“当前材料未发现明确疗程”；
- 不写“医生没有告知”，因为理赔材料可能并不完整。

## 文件状态

复核完成后保留候选文件，并生成：

```text
pain_gold.final.json
pain_annotation.final.json
pain_evaluation-report.json
```

建议标注信息：

```json
{
  "reviewer": "medical_student_01",
  "reviewed_at": "YYYY-MM-DD",
  "annotation_status": "reviewed",
  "review_result": "approved_with_changes"
}
```

