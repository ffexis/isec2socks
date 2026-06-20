# isec2socks

[English](#english) | [中文](#中文)

---

## English

### What is isec2socks?

A network utility that packages the iSecSP command-line client into a Docker container. It establishes standard Array Networks VPN connections inside the container and exposes standard SOCKS5/HTTP proxy ports to the outside (e.g., your local LAN).

### Features

- **Sandbox Isolation**: The closed-source iSecSP client runs entirely inside a Docker container with kernel-level isolation. It never modifies or pollutes your host machine's physical network routes.
- **SOCKS5 Proxy**: Leverages GOST to securely convert the VPN tunnel traffic established inside the container into a standard SOCKS5 proxy, exposed on port 31080.
- **HTTP API Control**: Built-in lightweight web server providing standard RESTful endpoints (connect, disconnect, status query, configuration), ready for integration with scripts, automation flows (e.g., Home Assistant), or web frontends.

### Prerequisites

- Docker and Docker Compose
- Network access to download the VPN client (URL provided by your VPN administrator)
- VPN credentials

### Quick Start

```bash
git clone https://github.com/your-username/isec2socks.git
cd isec2socks
docker compose up -d
```

Open the Web UI at `http://localhost:31081` and configure your VPN credentials there.

### Environment Variables

VPN credentials and client download URL can be configured via environment variables, which override the config file at runtime without modifying it.

| Variable | Description | Default |
|----------|-------------|---------|
| `VPN_HOST` | VPN server address (host:port) | Read from config file |
| `VPN_USER` | Username | Read from config file |
| `VPN_PASS` | Password | Read from config file |
| `VPN_SECOND_AUTH` | Second factor authentication | Read from config file |
| `VPN_DEB_URL` | VPN client deb download URL | `https://its.pku.edu.cn/software/iSecSP_ubuntu_2.4.0.deb` |

Example in `docker-compose.yml`:

```yaml
environment:
  - VPN_HOST=arrayvpn.pku.edu.cn:443
  - VPN_USER=your-username
  - VPN_PASS=your-password
  - VPN_SECOND_AUTH=0000
```

### Network Deployment Standard

This container is designed as a stateless internal microservice, listening on `0.0.0.0` by default to support private networking (e.g., Tailscale, WireGuard) or local Docker virtual bridges.

Network access control should be fully managed and isolated by the user's infrastructure layer (e.g., firewall policies, physically isolated network segments, or upstream reverse proxies).

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI dashboard |
| `/api/health` | GET | Health check |
| `/api/vpn/status` | GET | VPN connection status |
| `/api/vpn/connect` | POST | Start VPN connection (non-blocking) |
| `/api/vpn/input` | POST | Submit user input for authentication |
| `/api/vpn/cancel` | POST | Cancel current connection |
| `/api/vpn/off` | POST | Disconnect VPN |
| `/api/vpn/log` | GET | Get VPN client logs |
| `/api/vpn/log/stream` | GET | SSE stream for real-time logs and status |
| `/api/config` | GET | Read config (passwords masked) |
| `/api/config` | PUT | Update config |

Example:

```bash
# Check status
curl http://localhost:31081/api/vpn/status

# Connect VPN
curl -X POST http://localhost:31081/api/vpn/connect

# Submit authentication input
curl -X POST -H "Content-Type: application/json" \
  -d '{"value":"123456"}' \
  http://localhost:31081/api/vpn/input

# Disconnect VPN
curl -X POST http://localhost:31081/api/vpn/off

# Update config
curl -X PUT -H "Content-Type: application/json" \
  -d '{"VPN_PASS":"new-password"}' \
  http://localhost:31081/api/config
```

### Ports

| Port | Service |
|------|---------|
| 31080 | SOCKS5 Proxy |
| 31081 | Web UI + VPN API (HTTP) |

### Architecture

```
┌─────────────────────────────────────────────┐
│  Container: isec2socks-cli                      │
│                                             │
│  ┌─────────┐  ┌──────┐  ┌───────────────┐  │
│  │ iSecSP  │  │ GOST │  │ Route         │  │
│  │ Client  │  │      │  │ Guardian      │  │
│  └────┬────┘  └──┬───┘  └───────┬───────┘  │
│       │          │              │           │
│  ┌────┴──────────┴──────────────┴───────┐   │
│  │           vpn (bash)                 │   │
│  └──────────────────┬───────────────────┘   │
│                     │                       │
│  ┌──────────────────┴───────────────────┐   │
│  │        vpn-api.py (bottle)           │   │
│  │           Port 31081                 │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### License

MIT

---

## 中文

### 什么是 isec2socks？

一个将 iSecSP 命令行客户端封装进 Docker 容器的网络工具。它可以在容器内建立标准的 Array Networks VPN 连接，并对外（如本地局域网）暴露标准的 SOCKS5/HTTP 代理端口。

### 功能特性

- **沙箱隔离**：闭源的 iSecSP 客户端完全锁在 Docker 容器内部运行，内核级隔离，绝不污染或修改宿主机的物理网络路由。
- **SOCKS5 代理**：利用 GOST 模块，将容器内建立好的 VPN 隧道流量安全地转换为标准的 SOCKS5 代理，对外暴露在 31080 端口。
- **HTTP API 控制**：内置轻量级 Web 服务，提供标准的 RESTful 接口（如连接、断开、状态查询），方便通过脚本、自动化流（如 Home Assistant）或前端进行远程控制。

### 前置条件

- Docker 和 Docker Compose
- 能访问 VPN 客户端下载地址（由 VPN 管理员提供）
- VPN 账号密码

### 快速开始

```bash
git clone https://github.com/your-username/isec2socks.git
cd isec2socks
docker compose up -d
```

打开 Web UI `http://localhost:31081`，在页面中配置你的 VPN 凭证。

### 环境变量

VPN 凭证和客户端下载地址可通过环境变量配置，在运行时覆盖配置文件，不修改文件本身。

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `VPN_HOST` | VPN 服务器地址（host:port） | 读取配置文件 |
| `VPN_USER` | 用户名 | 读取配置文件 |
| `VPN_PASS` | 密码 | 读取配置文件 |
| `VPN_SECOND_AUTH` | 二因子验证码 | 读取配置文件 |
| `VPN_DEB_URL` | VPN 客户端 deb 下载地址 | `https://its.pku.edu.cn/software/iSecSP_ubuntu_2.4.0.deb` |

在 `docker-compose.yml` 中使用：

```yaml
environment:
  - VPN_HOST=arrayvpn.pku.edu.cn:443
  - VPN_USER=你的用户名
  - VPN_PASS=你的密码
  - VPN_SECOND_AUTH=0000
```

### 网络部署规范

本容器作为无状态的内部微服务设计，默认监听 `0.0.0.0` 以适配私有组网（如 Tailscale、WireGuard）或本地 Docker 虚拟网桥。

网络的访问控制应完全由使用者的基础设施层（如防火墙策略、物理隔离网段或上游反向代理）自行托管与隔离。

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web UI 控制面板 |
| `/api/health` | GET | 健康检查 |
| `/api/vpn/status` | GET | VPN 连接状态 |
| `/api/vpn/connect` | POST | 启动 VPN 连接（非阻塞） |
| `/api/vpn/input` | POST | 提交用户输入用于认证 |
| `/api/vpn/cancel` | POST | 取消当前连接 |
| `/api/vpn/off` | POST | 断开 VPN |
| `/api/vpn/log` | GET | 获取 VPN 客户端日志 |
| `/api/vpn/log/stream` | GET | SSE 流式实时日志和状态 |
| `/api/config` | GET | 读取配置（密码脱敏） |
| `/api/config` | PUT | 更新配置 |

示例：

```bash
# 查看状态
curl http://localhost:31081/api/vpn/status

# 连接 VPN
curl -X POST http://localhost:31081/api/vpn/connect

# 提交认证输入
curl -X POST -H "Content-Type: application/json" \
  -d '{"value":"123456"}' \
  http://localhost:31081/api/vpn/input

# 断开 VPN
curl -X POST http://localhost:31081/api/vpn/off

# 更新配置
curl -X PUT -H "Content-Type: application/json" \
  -d '{"VPN_PASS":"新密码"}' \
  http://localhost:31081/api/config
```

### 端口说明

| 端口 | 服务 |
|------|------|
| 31080 | SOCKS5 代理 |
| 31081 | Web UI + VPN API (HTTP) |

### 架构

```
┌─────────────────────────────────────────────┐
│  容器: isec2socks-cli                           │
│                                             │
│  ┌─────────┐  ┌──────┐  ┌───────────────┐  │
│  │ iSecSP  │  │ GOST │  │ 路由          │  │
│  │ 客户端  │  │      │  │ 守护          │  │
│  └────┬────┘  └──┬───┘  └───────┬───────┘  │
│       │          │              │           │
│  ┌────┴──────────┴──────────────┴───────┐   │
│  │           vpn (bash 脚本)            │   │
│  └──────────────────┬───────────────────┘   │
│                     │                       │
│  ┌──────────────────┴───────────────────┐   │
│  │        vpn-api.py (bottle)           │   │
│  │           端口 31081                 │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### 许可证

MIT
