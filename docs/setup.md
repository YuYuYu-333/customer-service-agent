# 本地环境细节

完整步骤见 [quickstart.md](quickstart.md)。README 里只放最快跑起来的那条路径，会卡住人的环境细节都堆在这里。

## 在 Windows 上装 Redis

**不装也能跑**：`REDIS_URL` 为空时自动退回进程内存，单进程完全正常。装它的唯一理由是**多 worker 部署**——会话必须跨进程可见。

Windows 上没有官方 Redis，用社区移植版 [tporadowski/redis](https://github.com/tporadowski/redis) 的 5.0.14 zip 包，解压即用，不需要 Docker / WSL：

```powershell
cd D:\redis
.\redis-server.exe        # 前台运行，这个窗口别关
```

另开一个窗口验证：

```powershell
.\redis-cli.exe ping      # 期望 PONG
```

`.env` 里写：

```
REDIS_URL=redis://127.0.0.1:6379/0
```

### 协议上有个坑

这个移植版停在 Redis 5.0，不支持 RESP3 握手，而 redis-py 8.x 默认就用 RESP3，连上去会直接报 `unknown command 'HELLO'`。`RedisStore` 里显式指定了 `protocol=2`。

换成 Linux 上的 Redis 7 时这个参数留着也不影响，向后兼容。

## 容器化未验证

**本仓库不附带 Dockerfile，原因是它没有被构建验证过。**

尝试过 Docker Desktop + WSL2 后端，但宿主 Windows 的 `VirtualMachinePlatform` 组件每次启用后重启即被系统回滚（三次尝试，含 `DISM /RestoreHealth`），镜像没能在本地构建成功。

一个写出来但跑不起来的 Dockerfile 比没有更糟：它会让下一个人以为自己只需要 `docker build`。

## 依赖清单为什么是手写的

`requirements.txt` 是手工整理的直接依赖，不是 `pip freeze` 导出 —— 后者会把环境里所有传递依赖和无关包一起带上，换台机器装完仍然不知道哪些是真的需要的。

完整性验证方式有两个：

1. 本地建一个全新的空虚拟环境，`pip install -r requirements-dev.txt`，48 个测试全部通过
2. **GitHub Actions 每次推送都在干净的 Ubuntu 上重跑一遍**，结果见仓库首页的 `tests` 徽章

## Linux / macOS

激活虚拟环境的命令换成 `source .venv/bin/activate`，其余相同。Redis 用官方版即可，不需要 Windows 移植版，也就不存在上面那个 RESP3 的坑。
