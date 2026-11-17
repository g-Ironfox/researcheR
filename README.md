# 论文工具

## 论文 Web UI


## 使用

开发期间叠加 `docker-compose.dev.yml` 启动（挂载 `webui/` 源码并开启热重载）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

首次启动或依赖有变化时加 `--build` 重建镜像：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

浏览器打开 `http://localhost:8667`，即可上传 PDF 并查看已保存论文。上传时按文件内容的 SHA-256 与已保存论文查重，重复文件会跳过并显示文件名。文件保存在 `data/uploads/`，通过 Docker volume 持久化。

停止服务：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

## 上传时 OCR

在项目根目录的 `.env` 中设置 `DEEPSEEK_API_KEY=你的密钥`，然后重建并启动 Web UI（依赖只安装在 Docker 镜像内）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build webui
```

上传新 PDF 后，服务端会同步调用 DeepSeek OCR（`rich_layout_recognition`），等待所有页面识别完成再返回。批量上传会依次处理，期间页面显示等待提示。PDF 会发送至 DeepSeek；已有重复文件仍跳过，不重复调用 OCR。

每篇论文的结果保存到 `data/uploads/<论文ID>.ocr.json`，在论文列表点击 **JSON** 下载。JSON 包含 `source`（文件信息）、`status`、`error`，以及 `result` 中的 DeepSeek 原始响应；每页文本位于 `result.pages[].text`，保留版面标记，页码 `index` 从 0 开始。页数不符或页面 `finish_reason=length` 时标记为 `partial`。

未配置密钥、超时或接口报错时，仍保留 PDF 并生成说明原因的 JSON，不会伪装成识别成功；删除论文会同时删除 OCR JSON。原有论文不自动补做 OCR。未 OCR 的论文可点击列表中的 **开始 OCR**；识别失败、未配置密钥或结果不完整的论文可点击 **重新 OCR**。识别期间按钮禁用，完成后自动更新状态和 JSON 下载链接；重试使用原 PDF，并覆盖该论文之前的 OCR JSON。单文件超过 100 MiB 或 1000 页时不调用接口。

在容器内运行集成测试（使用模拟 OCR 响应，不调用付费接口）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec webui python -m unittest discover -s tests -v
```

## Markdown 阅读器

OCR 完成后，在论文列表点击 **生成 Markdown** 会打开 Markdown 阅读器，并将完整的 OCR 原始 `result` 一次性发送给 DeepSeek Chat 模型整理；首次生成按模型 API 用量计费。默认模型为 `deepseek-flash`，可在 `.env` 中通过 `DEEPSEEK_CHAT_MODEL` 修改。OCR 原始 JSON 保持不变，Markdown 单独保存为 `data/uploads/<论文ID>.md`；阅读器可切换原文与简体中文，并下载当前语言版本。点击中文后会把 Markdown 原文发送给 DeepSeek Chat 翻译，译文缓存为 `data/uploads/<论文ID>.zh.md`，再次切换时直接读取；重新生成原文会自动清除旧译文。首次生成或翻译会产生模型 API 用量。若模型输入超出上下文或输出不完整，操作会报错且不会覆盖已有文件。删除论文会同时删除 OCR、原文和译文文件。

## 自动提取参考文献

在论文编辑窗口点击 **提取全部参考文献**，提取的是**论文中引用的全部文献 / References 列表**，不是论文自身的 BibTeX。系统扫描所有页面，优先使用已有 OCR，缺页时回退到 PDF 文本，不截取前三页或丢弃长页；没有可读文本的扫描版请先 OCR。

全文以带重叠的分段发送给 DeepSeek，长论文会产生多次 API 用量。每条文献保留编号、标题、作者数组、年份、期刊/会议、卷期、页码、出版社、DOI、URL、原始引用文本和来源页码。未知信息留空；原始引用必须能在输入文本中核对。跨分段重复条目会合并，输出截断、字段错误或未完整处理时操作失败，不覆盖已有文献。OCR 不完整或存在无文本页面时会明确提醒；模型提取仍需人工核对，不能保证无遗漏。

编辑窗口以可折叠卡片显示每条文献，支持按标题、作者、年份和 DOI 搜索、逐字段编辑、手动添加及删除。提取结果先预览，点击 **保存** 后作为元数据 JSON 中的 `references` 数组持久化。旧版 `reference` 文本在折叠区保留，不强行转换。重新提取需确认；无结果、报错或提取期间手工修改列表时不会替换原列表。

测试使用模拟模型，不产生付费请求：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec webui python -m unittest discover -s tests -v
node webui/tests/test_reference_ui.mjs
```

## 自动提取标题和摘要

在论文编辑窗口的“论文信息”区点击 **提取标题和摘要**，使用现有 DeepSeek 配置，从前三页文本提取论文自身的原标题及完整摘要。优先读取已有 OCR，缺失时使用 PDF 原生文本；扫描版没有可读文本时需要先 OCR。保留原文语言，不翻译、不另写总结；未知或不完整的字段留空，界面保留该字段原有内容。提取结果会与输入原文核对，返回格式异常、截断或无法核对时不会修改已有内容。

结果先填入表单，检查后点击 **保存** 生效；提取期间手动修改过的字段不会被覆盖。此操作会将前三页可读文本发送至 DeepSeek 并产生 API 用量，不会触发额外 OCR，也不会修改已有参考文献列表。
