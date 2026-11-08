# 快速开始（完整版）

README 里只放最短的那条路径。这里是从零到跑通的完整步骤，以及会卡住人的地方。

> 需要 Python **3.10 或更高**（代码里用了 `str | None` 这类语法）。本项目在 **3.13.15** 上开发并验证；CI 在 **Ubuntu + Python 3.13** 上跑同一套测试，所以代码本身不依赖 Windows——下面命令里的路径写法是 Windows 的，Linux/macOS 换成对应的即可。
>
> **只想看一眼效果**：可以直接用 [在线演示](https://customer-service-agent-9o0y.onrender.com)，不用装任何东西。

## 1. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

PowerShell 首次执行报 `禁止运行脚本` 时，先开一次当前会话的策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**Linux / macOS**：激活命令换成 `source .venv/bin/activate`，其余相同。

## 2. 配置密钥

```powershell
Copy-Item .env.example .env
```

`.env` 里有两个必需项和三个可选项：

| 变量 | 用途 | 必需 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 对话与工具调用 | 是 |
| `SILICONFLOW_API_KEY` | 文本向量化（建索引与检索） | 是 |
| `REDIS_URL` | 会话存储；不填则退回进程内存 | 否 |
| `RATE_LIMIT_PER_MINUTE` | 每 IP 每分钟请求上限，默认 10 | 否 |
| `DAILY_TOKEN_LIMIT` | 每日 prompt token 上限，默认 1000000 | 否 |

`.env` 已在 `.gitignore` 中，不会被提交。密钥缺失时 `app/config.py` 在 **import 阶段**就抛 `RuntimeError`，不会等到第一个请求才收到 401。

## 3. 建检索索引

```powershell
python -m scripts.build_index
```

生成 `index.json`（同样在 `.gitignore` 中，**必须自己跑一次**）。修改 `data/docs/policy.txt` 后需要重跑。

## 4. 启动 Redis（可选）

**不装也能跑**：`REDIS_URL` 为空时自动退回进程内存，单进程完全正常。装它的唯一理由是**多 worker 部署**——会话必须跨进程可见。

Windows 上的装法，以及那个 `unknown command 'HELLO'` 的协议坑（redis-py 8.x 默认 RESP3，社区移植版停在 5.0），见 [setup.md](setup.md)。

## 5. 启动服务

```powershell
uvicorn app.api:app --reload
```

打开 <http://127.0.0.1:8000> 直接用页面问；交互式接口文档在 <http://127.0.0.1:8000/docs>。

多 worker 部署：

```powershell
uvicorn app.api:app --workers 2
```

会话和锁都在 Redis 里，进程数量不影响会话一致性——这一点用并发实验验证过，见 README 的「设计决策 4」。

## 6. 跑测试

```powershell
pytest
```

48 个测试，不调用真实模型（`app.api.run_agent` 在测试中被替换）。测试用 `MemoryStore`，**不需要 Redis 在跑**。

## 7. 上线

部署到 Render 的步骤、`--forwarded-allow-ips` 那个坑、免费实例的三条限制，见 [deploy.md](deploy.md)。
