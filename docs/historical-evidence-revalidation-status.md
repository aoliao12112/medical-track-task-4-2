# Historical evidence revalidation 状态

更新时间：2026-10-14

## 当前结论

历史 evidence migration 当前暂停，状态为 `NOT_READY`。不得使用 dry-run
结果批量修改 `competition.db` 中已有病例的 `structured_result_json`、字段状态、
warnings 或 quality flags。

只读 dry-run 实现予以保留：

- `src/medical_claims/evidence_revalidation.py`
- `scripts/revalidate_historical_evidence.py`
- `tests/test_evidence_revalidation.py`

当前 final dry-run 报告保留在：

- `outputs/logs/evidence-revalidation-dry-run-20261014-final.json`

## 暂停原因

final dry-run 仍包含 219 个 examination 字段恢复候选。这些候选尚未逐项记录：

- 实际命中的 OCR 行或空间区域；
- 对应 bbox 或匹配区域；
- 匹配方式；
- 同一区域的竞争检查项目及数量；
- 项目名和值是否位于同一视觉行。

因此，当前报告中的 verifier PASS 不能单独证明 examination 项目和值已经唯一
绑定。重复“阴性”、相似项目名、OCR 粘连和跨栏表格仍可能产生归属歧义。
在这些候选具备逐项可审计依据前，不执行正式历史迁移，也不继续处理这 219 个
候选。

## 已完成的安全修复

- 费用、结算或收费清单中的药品规格不能用于恢复 medication dose；
- 多药区域、重复同名药及只有字段值而没有药名的 medication 属性不会自动恢复；
- `%` 作为有意义的检验单位保留；
- `0. 1ml` 可保守归一化为 `0.1ml`，且不会令 `1.2mg` 与 `12mg` 等价；
- `CONFIRMED`、`CORRECTED`、`UNRESOLVED` 人工 overlay 不受历史恢复逻辑覆盖。

## 新病例行为

历史迁移暂停不影响新病例。新上传病例仍在当前正常结构化流程中使用新版
evidence verifier；本说明只限制对数据库中历史结果进行批量回写。

