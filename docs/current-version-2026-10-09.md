# 当前版本交接说明（2026-10-09）

本版本用于比赛原型演示和团队继续开发，覆盖四问主链路、角色页面、SQLite 持久化、模拟触达
与反馈。本文只描述已经存在的能力，不把规划项写成已完成。

## 一、可演示页面

| 页面 | 地址 | 用途 |
|---|---|---|
| 综合工作台 | `http://127.0.0.1:8765/` | 第一至第四问、触达、批量与评测 |
| 角色门户 | `http://127.0.0.1:8765/roles.html` | 进入五类角色页面 |
| 用户端 | `http://127.0.0.1:8765/patient.html` | 查看已预览或已发布的温度指引 |
| 理赔客服端 | `http://127.0.0.1:8765/claims-service.html` | 案件摘要、材料状态和服务待办 |
| 医学审核端 | `http://127.0.0.1:8765/medical-review.html` | 脱敏病例、痛点证据和安全检查 |
| 运营端 | `http://127.0.0.1:8765/operations.html` | 聚合覆盖、画像状态和待办 |
| 系统管理端 | `http://127.0.0.1:8765/system-admin.html` | 角色矩阵、案件范围和审计边界 |
| API 文档 | `http://127.0.0.1:8000/docs` | 当前 FastAPI 接口 |

## 二、推荐演示顺序

1. 创建或读取案件，确认材料使用授权。
2. 添加图片并只运行本地 OCR。
3. 逐页人工修正、脱敏并保存到 SQLite。
4. 完成规则检查或明确人工覆核，再调用 DeepSeek 结构化。
5. 展示诊断、手术、用药、检查、费用、事件和字段证据。
6. 运行第二问痛点识别，展示三类痛点、严重度和原文证据。
7. 运行第三问温度指引，展示事实锚点、行动计划、安全边界和用户端预览。
8. 展示第四问覆盖审计、样本门槛、画像维度和“样本不足时拒绝推荐”。
9. 在触达与反馈页面完成发布前检查、模拟发送和用户反馈。
10. 从角色门户展示客服、医学审核、运营和系统管理的信息边界。

## 三、已经实现

- `OCR_READY` 独立工作流状态和 OCR 失败恢复；
- 原图本地 OCR、脱敏文本外发的两阶段处理；
- OCR 修改版持久化、敏感格式双端检查、人工覆核和审计指纹；
- 结构化结果、痛点、温度指引的当前快照和下游失效规则；
- 用户反馈、通用跟进待办、客服会话分析预留表；
- Schema Version 7、29 张 SQLite 表及旧画像三表兼容；
- 五类固定角色及最小权限矩阵；
- 五类角色展示页面；
- Windows 中文用户名导致 venv 启动器损坏时的启动脚本兼容。

## 四、仍需人工完成

- 医学人员审核知识卡、结构化病例、痛点标签和最终温度指引；
- KR1 使用官方补充 ZIP 经 `scripts/convert_official_kr1_gold.py` 确定性转换的
  `outputs/official_kr1_gold_v2/official-kr1.gold.final.json`，不得再使用自行整理结果替代；
- 用官方 KR1 gold 重新计算结构化 F1；痛点一致率和温度盲评分仍需独立专家评审；
- 第四问补充足够病例后再构建画像；单机构单病例只能显示数据不足，不能排名；
- 比赛演示前删除或替换所有真实患者身份信息；
- 正式上线前接入企业统一身份、后端鉴权中间件、真实消息渠道和密钥管理系统。

## 五、禁止提交的内容

- `web/backend/.env` 和任何真实 API Key；
- `outputs/competition.db`、OCR缓存、日志中的患者原文；
- `work/` 下的原始病历、身份映射、HMAC密钥和私有审计输出；
- 官方原始压缩包及解压后的病例图片；
- 手机预览令牌或真实触达信息。

相关目录均应保持在 `.gitignore` 范围内。提交前仍需运行敏感信息扫描。

## 六、验证命令

核心工作流和页面测试不依赖本地第四问审计产物，可直接运行：

```powershell
$env:PYTHONPATH = "src"
& '.\.venv\Scripts\python.exe' -m pytest -q --ignore tests/test_multisource_quality.py
```

完整测试还会运行 `tests/test_multisource_quality.py`。该文件读取本地生成且被 Git 忽略的
`outputs/hospital_public_data_audit.json` 和 `outputs/dataset_audit_summary.json`；必须先在本机
完成第四问数据审计，生成这两个文件后再执行：

```powershell
$env:PYTHONPATH = "src"
& '.\.venv\Scripts\python.exe' -m pytest -q
```

这两个审计文件可能包含本地数据摘要，不应为了让测试变绿而伪造，也不得未经检查直接提交。

如果 venv 启动器在当前 Windows 用户名下损坏，优先重新创建虚拟环境；本版本启动脚本可做运行
时兼容，但不应把损坏环境复制给队友。

前端语法检查：

```powershell
node --check web/dist/app.js
node --check web/dist/role-pages.js
```

## 七、进一步阅读

- [团队环境与 API 调用说明](team-setup-and-api-guide.md)
- [OCR 脱敏与人工复核流程](ocr-deidentification-workflow.md)
- [当前数据库结构](database-schema.md)
- [第三问人工检查清单](third-question-manual-checklist.md)
- [第四问品质画像说明](fourth-question-quality-profile.md)
