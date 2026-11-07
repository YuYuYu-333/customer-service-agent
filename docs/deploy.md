# 部署

目标平台是 [Render](https://render.com) 免费层。仓库根目录的 `render.yaml` 是一份可直接用的 Blueprint，导入即用，不需要 Docker。

## Render

| 配置项 | 值 | 为什么 |
|---|---|---|
| `buildCommand` | `pip install -r requirements.txt && python -m scripts.build_index` | `index.json` 在 `.gitignore` 里，必须在构建阶段生成。放在构建而不是启动，是因为每次冷启动都重建等于每次醒来都多一次 embedding 调用 |
| `startCommand` | `uvicorn ... --proxy-headers --forwarded-allow-ips="*"` | **不加这两个参数，限流会把所有访客算成一个人**，见下 |
| `healthCheckPath` | `/health` | 用 `/health` 而不是 `/ready`：后者会查存储，冷启动期间可能还没就绪 |
| `DEEPSEEK_API_KEY` / `SILICONFLOW_API_KEY` | `sync: false` | 声明变量名但不写值，值只能在 Render 面板里填。**仓库里搜不到任何密钥** |

### `--proxy-headers` 这条值得单独说

Render 前面挂了一层反向代理，`request.client.host` 拿到的是代理的地址，不是访客的。不加这两个参数，`app/limits.py` 按 IP 限流的那个桶就只有一个——**第一个访客打满 10 次/分钟之后，所有人都收到 429**。本地开发直接连 `127.0.0.1` 没有代理，这个坑不会自己暴露出来。

`--forwarded-allow-ips="*"` 是信任所有来源的 `X-Forwarded-For`。在 Render 上是安全的（只有它的代理能把流量送到你的实例），**自己裸机部署时不要照抄**——那样客户端可以随便伪造这个头，伪造一次就换一个 IP。

### 免费实例的三个已知限制

Demo 页底部的说明栏里也写了：

- **15 分钟无请求后休眠**，下一次访问要等 30 秒以上冷启动。所以仓库首页先放一段 21 秒的录屏，而不是只给一个链接
- **文件系统是临时的**，重启后 `index.json` 由构建产物提供、`_TICKETS` 清空——工单本来就只活一轮
- **没有 Redis**，`build_store()` 自动退回 `MemoryStore`，单进程下行为一致；启动日志会打一条 WARNING

## 依赖与容器化

`requirements.txt` 是手工整理的直接依赖，不是 `pip freeze` 导出——后者会把无关的传递依赖一起带上，装完仍然不知道哪些是真的需要的。完整性靠两件事验证：全新的空环境装一遍跑 47 个测试，以及 GitHub Actions 每次推送在干净的 Ubuntu 上重跑。

**本仓库不附带 Dockerfile**，因为它没能被构建验证过（宿主 Windows 的 `VirtualMachinePlatform` 组件反复被系统回滚）。写出来但跑不起来的 Dockerfile 比没有更糟。细节见 [setup.md](setup.md)。
