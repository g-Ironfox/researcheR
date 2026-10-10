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
