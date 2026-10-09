# 本地 OCR 与 DeepSeek 后端

这层服务支撑四问：图片在本机由 RapidOCR 识别，DeepSeek 仅接收脱敏后的 OCR 文本或结构化事实包，并按受控 JSON Schema 输出结果。团队统一配置和全部接口示例见 [团队环境与 API 调用说明](../../docs/team-setup-and-api-guide.md)。

## 安装

在项目根目录创建虚拟环境后运行：

```powershell
python -m pip install -e ".[api]"
```

## 配置

不要把密钥写入网页。启动前在当前 PowerShell 会话设置：

```powershell
$env:DEEPSEEK_API_KEY="你的密钥"
```

## 启动

```powershell
& '.\scripts\start_backend.ps1'
```

脚本自动加载 `web/backend/.env`，并兼容部分 Windows 中文用户名造成的 venv 启动器损坏。
网页右上角点击“演示模式”，填写 `http://127.0.0.1:8000` 并检测连接。

## 手机预览与二维码

主后端必须继续绑定 `127.0.0.1:8000`，不要把包含 OCR、病例和模型能力的完整 API
暴露到局域网。需要手机扫码演示时，另开一个 PowerShell 窗口，在项目根目录启动只读预览网关：

```powershell
uvicorn web.mobile_gateway:app --host 0.0.0.0 --port 8766 --env-file web/backend/.env
```

该网关只提供 `patient.html`、对应 CSS/JS 和令牌保护的 `GET /api/mobile-preview`，不挂载
OCR、DeepSeek、案件管理或审核接口。第三问生成温度指引后，点击“手机预览 / 二维码”，
系统会创建默认 30 分钟有效的随机链接；数据库只保存令牌哈希，并使用新的预览编号替代真实
案件编号。手机和电脑需连接同一可信 Wi-Fi 或手机热点。不要在校园公共群聊、社交平台或公开
文档中传播预览链接；正式发送仍须通过触达安全审核。

如果自动探测到错误网卡，可在 `.env` 中指定：

```text
MOBILE_PREVIEW_HOST=192.168.1.20
MOBILE_PREVIEW_GATEWAY_PORT=8766
```

第四问新增接口：

```text
GET  /api/quality/audit-summary
POST /api/quality/profiles/build
POST /api/quality/recommendations
```

机构覆盖审计需先运行 `scripts/audit_provider_coverage.py`。画像接口只接收已经脱敏的医院、
医生和治疗方案编号；样本量或可计算维度不足时会拒绝生成排行榜。

## OCR、脱敏与人工复核

真实医疗材料优先使用两阶段接口。第一阶段只在本机运行 OCR：

```text
POST /api/cases/ocr-only
```

完成 OCR 后，可在左侧逐页查看并删除身份信息。保存按钮调用以下接口，只把修改版写入本地
SQLite，不调用 DeepSeek：

```text
POST /api/cases/save-reviewed-ocr
```

确认脱敏后，重新结构化接口为：

```text
POST /api/cases/restructure-from-ocr
```

前后端都会对修改版文本进行敏感格式预警。若命中内容已确认属于虚构占位符或非患者信息，
操作者可以进行明确人工覆核；后端将覆核方式、命中类别和修改版文本指纹写入审计日志。
该接口不会再次运行OCR，且只把修改后的 `documents[].full_text` 发送给 DeepSeek。
长病例缓存按OCR文本哈希区分，因此只会重新处理受修改影响的分块；
第二问和第三问的旧结果会自动清空，避免继续使用修正前的病例事实。

完整说明见 [`docs/ocr-deidentification-workflow.md`](../../docs/ocr-deidentification-workflow.md)。

## 角色页面

启动前端后访问 <http://127.0.0.1:8765/roles.html>。当前提供用户、理赔客服、医学审核、
运营和系统管理五类页面。页面按最小必要信息展示，但正式认证仍需企业身份系统和后端鉴权
中间件，不能把前端隐藏视为真实权限。

完成第一问后，可在网页的“痛点洞察”页点击“分析当前病例”。对应接口为：

```text
POST /api/cases/analyze-pain-points
```

请求体：

```json
{
  "case": { "case_id": "CASE-001" },
  "use_llm": true
}
```

接口优先运行本地确定性规则，再合并经过证据校验的 DeepSeek 语义候选；即使语义调用
失败，也会返回规则结果，并在 `analysis_warnings` 中说明降级原因。

完成第二问后，在“温度指引”页生成第三问，接口为：

```text
POST /api/cases/generate-care-guide
```

第三问会匹配 `knowledge/care_guides/` 中的病种知识卡，执行字段引用和医学边界校验。
知识卡尚未由医学人员审核时，输出始终保持 `review_required=true`。DeepSeek 调用失败会返回
`rules_fallback` 安全草稿，不会中断演示。

当前后端优先处理 JPG、JPEG、PNG、BMP、WebP。官方样本本身是图片，因此不影响本轮数据；PDF 后续可增加逐页转换。

## 长病例

超过 8 页或 OCR 文本超过 24000 字符时，后端会自动切换为长病例模式：

1. 每页在本地独立 OCR；
2. 默认按最多 6 页、不超过 18000 OCR 字符且不超过 600 个OCR块分块；
3. 每个分块独立结构化，单块输出过长或超时时会自动减半重试；
4. 本地确定性合并器对诊断、手术、用药、检查和事件去重；
5. 全案重新计算费用一致性并检查证据文件、页码和实体引用；
6. 不同分块出现冲突值时保留高置信度候选，同时标记人工复核，禁止静默覆盖。

成功的分块结果默认缓存在本地 `outputs/_long_case_cache/`，缓存签名包含代码版本、文件名、页码和OCR文本哈希。长任务中途失败后，使用相同材料和病例号重试会复用已完成分块；代码升级或OCR内容变化时缓存自动失效。缓存不保存原始图片，且 `outputs/` 已被 Git 忽略。

用户仍然一次上传同一案件的全部图片，不需要手工拆批。文件名不强制改写；同一案件出现同名文件时，后端会自动增加页序前缀以保证证据可追踪。

可通过环境变量调整边界：

```powershell
$env:LONG_CASE_PAGE_THRESHOLD="8"
$env:LONG_CASE_CHARACTER_THRESHOLD="24000"
$env:LONG_CASE_CHUNK_MAX_PAGES="6"
$env:LONG_CASE_CHUNK_MAX_CHARACTERS="18000"
$env:LONG_CASE_CHUNK_MAX_OCR_BLOCKS="600"
$env:DEEPSEEK_CHUNK_MAX_OUTPUT_TOKENS="30000"
$env:DEEPSEEK_MAX_RETRIES="2"
$env:LONG_CASE_CACHE_ENABLED="true"
```

不建议盲目调大分块。费用明细和检验报告字段密度高，即使页数不多也可能产生很长的 JSON。
