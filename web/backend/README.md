# 本地 OCR 与 DeepSeek 后端

这层服务只用于第一问：图片在本机由 RapidOCR 识别，DeepSeek 仅接收 OCR 后的文本并按 ClaimCase JSON Schema 输出结果。

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
uvicorn web.backend.app:app --host 127.0.0.1 --port 8000
```

网页右上角点击“演示模式”，填写 `http://127.0.0.1:8000` 并检测连接。

当前后端优先处理 JPG、JPEG、PNG、BMP、WebP。官方样本本身是图片，因此不影响本轮数据；PDF 后续可增加逐页转换。
