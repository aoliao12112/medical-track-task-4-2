# 第四问：医院与医生来源数据审计

## 这一步解决什么

在正式设计“医院/医生/治疗方案品质画像”前，先确认现有脱敏病例能否稳定提供：

1. 案件所属医疗机构；
2. 带明确职责的医生；
3. 肺炎、骨折、糖尿病三个目标病种分别覆盖多少家机构。

案件文件夹的 `CL...` 只作 `case_id`，不能当作患者编号、医院编号或医生编号。

## 安全边界

- 只 OCR 压缩包中的图片，不读取 `_manifest.json`。
- OCR 在本机 RapidOCR 中运行，不向 DeepSeek 或其他外部服务发送病例。
- 医院和医生采用本地 HMAC 生成稳定伪匿名编号。
- 安全报告只包含 `HOSP-...`、`DOC-...`；真实名称映射与 OCR 缓存只保存在被 git 忽略的 `work/provider_audit/private/`。
- 只有主治、经治和手术角色可以成为医生画像候选。报告医师、检验者、审核者等必须单独保存，不能混入经治医生排名。

## 运行命令

先冒烟验证 3 个案件、每个案件最多 3 张图片：

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\audit_provider_coverage.py' --max-cases 3 --max-images-per-case 3
```

再运行完整审计：

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\audit_provider_coverage.py'
```

OCR 按图片内容哈希缓存，中断后重新运行会复用已经完成的页面。

## 产物

- `work/provider_audit/provider-coverage-report.md`：便于人工阅读的覆盖报告。
- `work/provider_audit/provider-coverage-report.json`：第四问后续程序输入。
- `work/provider_audit/provider-contexts.safe.json`：逐案件的脱敏医院、医生角色与病种。
- `work/provider_audit/private/`：真实名称映射、密钥和 OCR 缓存，只能留在本机。
- `work/provider_audit/private/provider-review-queue.private.json`：多机构冲突案件的逐案人工复核清单，包含候选名称和证据页，禁止上传。

## 人工复核

程序跑完后，医学生只需要抽查：

1. 先处理 `provider-review-queue.private.json` 中全部多机构冲突案件；没有冲突的案件再随机抽查至少 10 个；
2. 医生角色是否把报告/检验医师误当成经治医师；
3. 病种命中是否来自肯定诊断，而不是“排除骨折”“未见肺炎”等否定表达；
4. 多机构冲突案件（转诊、外院报告）中的主机构选择是否正确。

在抽查完成前，结果只能叫“候选机构上下文”，不能直接宣称为正式品质画像。
