# Real E2E Checklist

当前状态：**BLOCKED_BY_TEST_DATA**（2026-10-06）。已递归检查仓库代码/示例/知识/测试目录，以及 ignored `work/`、`outputs/`；没有找到当前可用、授权可核验的 JPG/PNG 等医疗病例图片。分块 JSON 和历史文档中的运行描述不能替代本轮真实图片输入。没有生成“官方病例”，没有外发医疗文本或调用 DeepSeek。

阻塞证据：[run_metadata.json](D:/yiliao/medical-track-task-4-2/outputs/e2e/20261006T114819206124Z/run_metadata.json)。这是一次输入检查，不是成功 E2E；latencies 为空，token_usage=null，case_id=null，external_llm_called=false。

## 需要提供什么

1. 同一案件1–3张清晰、完整的脱敏 JPG/PNG；例如出院小结、用药页、费用清单。现有后端也支持 JPEG/BMP/WebP。本轮不增加PDF/Word支持。
2. 核实使用授权包含测试与将OCR文本发送给项目现有DeepSeek服务。遮除姓名、证件号、联系方式、住址、条码/二维码等可识别信息；文件名用 `document-1.jpg` 等中性名称。
3. 医学字段仍应可读：诊断、时间、手术/药品、剂量/频次、费用。不要为了脱敏删掉全部验证内容。
4. 本地填写既有 `web/backend/.env`，含有效 DEEPSEEK_API_KEY；不要发到聊天或Git。确认依赖已安装，RapidOCR本地模型可以启动。

授权与脱敏由操作者核验；CLI flags只是既有状态声明，不是自动合规证明。

## 自动记录的运行方式

[run_real_e2e.py](D:/yiliao/medical-track-task-4-2/scripts/run_real_e2e.py) 是本地验证脚本，直接复用现有后端函数，不是新增产品模块或新算法，不需要启动uvicorn。将已核验的图片放入 Git-ignored `work/real_e2e_inputs/`，然后：

```powershell
cd D:\yiliao\medical-track-task-4-2
.\.venv\Scripts\python.exe scripts/run_real_e2e.py --images work/real_e2e_inputs/document-1.jpg work/real_e2e_inputs/document-2.png --authorized --deidentified
```

脚本执行：创建真实 UUID Case → 本地图片读取/上传对象 → RapidOCR → DeepSeek结构化 → 现有痛点API函数（use_llm=true）→ 关怀API函数（use_llm=true）→ Risk Gate → 新建storage连接重读SQLite核对。为避免缓存冒充真实结构化调用，本次验证关闭长病例缓存。使用现有同一个competition.db，不创建第二个生产库，不把材料导入KR4历史，不发送真实消息或模拟delivery。

输出到新建 `outputs/e2e/<UTC timestamp>/`：

```text
run_metadata.json
ocr.json
claim_case.json
pain_report.json
care_guide.json
risk_gate.json
```

run_metadata记录OCR/结构化/pain/care/gate/总时长秒数、实际成功与失败阶段、最终workflow状态、warnings、是否重开成功和真实API返回的usage。API未给usage则null，不估算token。失败时仅保留已实际完成的产物，不补写假结果。

状态解释：

- BLOCKED_BY_TEST_DATA / BLOCKED_BY_AUTHORIZATION / BLOCKED_BY_CONFIGURATION：尚未运行，按reason补齐输入。
- FAILED：记录真实failed_stage及错误；不拿demo/fallback冒充成功。
- EXECUTED_WITH_FALLBACK：曾执行，但Care Guide不是实际LLM结果；不能宣称目标DeepSeek→Care完整成功。
- EXECUTED：实际结构化调用、LLM关怀、SQLite重读已执行。**不等于医学正确或可发布**；风险结果可能仍REVIEW_REQUIRED或BLOCKED，必须忠实保留。

`real_e2e_passed` 只表示上述工程调用和重读要求，不是安全、F1或比赛指标通过。脚本未做真实浏览器操作；页面演示需另外核验。

## 现场页面复核

两个PowerShell终端分别运行既有脚本：

```powershell
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
```

打开 http://127.0.0.1:8765/，配置 http://127.0.0.1:8000。读取metadata里的case_id；检查OCR/字段/痛点/关怀、画像数据不足说明、准备触达风险理由，刷新后再次核对同案记录。测试另一个case_id时，旧结果须立即清空。无需为了演示强行批准BLOCKED。

## 验收前检查

- 所有文件来自同一真实授权案件，页面case_id和保存产物一致。
- OCR关键日期、药名、数字由成员C对照原图；证据失败字段不删除，标明需复核。
- care_generation_method=llm，存在实际structuring API调用记录；fallback不能隐藏。
- 每阶段latency有真实测量，token缺失明确null；最终status如实报告。
- 重开SQLite读到原结果；风险block不绕过。
- `outputs/` / `work/` 保持ignored；不把原图、API密钥、可识别OCR或未经允许的医疗结果加入Git。
- 最后另跑正式KR1独立gold和医学人工复核；工程E2E不能替代这些指标。
