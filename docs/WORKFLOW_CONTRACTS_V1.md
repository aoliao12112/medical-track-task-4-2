# 四端协作契约 V1

本文档冻结三个跨端契约。V1 允许增加带默认值的可选字段；删除字段、改变语义或改变枚举值必须发布 V2。代码定义位于 `src/medical_claims/contracts.py`。

## MedicalReviewV1

- 字段决策：`CONFIRMED / CORRECTED / UNRESOLVED`。字段决策与结构化原始结果指纹绑定，上游结枔变化后自动失效。
- 疼痛单条决策：`SHOW / EDIT / HIDE`。决策与当前疼痛报告指纹绑定；`EDIT` 保存客户可读的标题、摘要和建议，`HIDE` 必须说明原因。
- 护理修订：保留原始报告和修订报告，后端重新计算证据覆盖和安全检查，前端不得提交或伪造安全状态。
- 最终门禁：授权/脱敏通过、待确认和无法确认字段均为 0、所有疼痛条目已决策、必需模块均为当前版本且已通过，才进入 `READY_FOR_FINAL_REVIEW`。
- 疼痛和护理是必需模块；品质评价和就医推荐一旦已生成并计划展示，也必须审核通过。
- 在 V1 上线前已生成、且没有模块生成日志的历史案件不追溯性阻断；任一医疗模块重新生成后，整个案件立即进入 V1 强制门禁。

## CustomerDeliveryV1

`schema_version` 固定为 `customer-delivery-v1`。客户端只能消费此 DTO，不得读取医疗原始结果。

内容字段仅包含：

- `care_guide`：剥离证据路径、模型警告和内部状态后的客户可读关怀内容。
- `attention_items`：仅由疼痛审核的 `SHOW/EDIT` 条目产生，`HIDE` 条目永不出现。
- `quality_observation`：范围固定为 `current_case`，必须包含单病例边界声明。
- `recommendation`：仅包含展示名称、地区/科室、客户可读理由、局限性和可公开来源。

禁止出现 OCR 原文、内部实体 ID、数据库 ID、内部评分、审核备注、文件路径和原始证据字段。

## CaseTaskV1

`schema_version` 固定为 `case-task-v1`，状态机固定为 `OPEN -> PROCESSING -> RESOLVED`。

- 目标角色：`CLAIM / MEDICAL / SERVICE`。
- 任务初始允许未分配；进入 `PROCESSING` 必须写入 `claimed_at`。
- 进入 `RESOLVED` 必须写入 `resolved_at` 和非空 `resolution`。
- 费用问题拆成 `medical_cost` 和 `claim_cost`，不允许使用含义不明的通用费用类别。
- 服务咨询只有在对应任务落库后才能标记为 `ROUTED`。
