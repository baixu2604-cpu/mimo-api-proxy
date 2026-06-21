# API Proxy

带完整使用记录的 API 代理服务，**同时兼容 OpenAI 和 Claude 两种协议**。

分享 API Key 给别人用的同时，记录每个人发送的提示词和 AI 回复。

## 功能

- **双协议兼容** — 自动检测 OpenAI / Claude 格式，透明代理
- **子Key管理** — 给每人生成独立子Key，可单独限额、禁用
- **完整记录** — 记录所有提示词(Prompt)和 AI 回复(Response)
- **Web 面板** — 可视化查看用户统计、请求日志、模型分布
- **流式支持** — 完整支持 SSE 流式响应（两种协议）

## 支持的协议

| 协议 | 认证方式 | 端点 | SDK |
|------|----------|------|-----|
| OpenAI | `Authorization: Bearer <key>` | `/v1/chat/completions` | `openai` Python/Node |
| Claude | `x-api-key: <key>` | `/v1/messages` | `anthropic` Python/Node |

---

## 部署到云平台（推荐）

### 方案一：Railway（免费 $5 额度）

1. 访问 [railway.app](https://railway.app) 用 GitHub 登录
2. 点击 **New Project** → **Deploy from GitHub Repo**
3. 选择你的仓库（或上传代码）
4. 添加环境变量：
   - `UPSTREAM_API_KEY` = 你的 mimo API Key
   - `ADMIN_TOKEN` = 管理面板密码
5. 部署完成后会给你一个公网地址如 `https://xxx.up.railway.app`

### 方案二：Render（完全免费）

1. 访问 [render.com](https://render.com) 注册账号
2. 点击 **New** → **Web Service**
3. 连接 GitHub 仓库或上传代码
4. 设置：
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
5. 添加环境变量：
   - `UPSTREAM_API_KEY` = 你的 mimo API Key
   - `ADMIN_TOKEN` = 管理面板密码
6. 点击 **Create Web Service**

### 方案三：Docker 部署

```bash
# 构建镜像
docker build -t mimo-proxy .

# 运行容器
docker run -d \
  -p 8800:8800 \
  -e UPSTREAM_API_KEY=你的密钥 \
  -e ADMIN_TOKEN=管理密码 \
  --name mimo-proxy \
  mimo-proxy
```

---

## 本地运行

### 方式一：双击启动
直接双击 `start.bat`，按提示输入 API Key 即可。

### 方式二：手动启动
```bash
pip install -r requirements.txt

set UPSTREAM_API_KEY=你的密钥
set ADMIN_TOKEN=管理密码
python main.py
```

---

## 访问

| 地址 | 说明 |
|------|------|
| `https://你的域名/dashboard` | 管理面板（需登录） |
| `https://你的域名/v1/chat/completions` | OpenAI 格式代理 |
| `https://你的域名/v1/messages` | Claude 格式代理 |

## 用户使用方式

### OpenAI SDK
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://你的域名/v1",
    api_key="sk-用户的子Key"
)

response = client.chat.completions.create(
    model="mimo-v2.5",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
```

### Claude SDK
```python
import anthropic

client = anthropic.Anthropic(
    base_url="https://你的域名",
    api_key="sk-用户的子Key"
)

response = client.messages.create(
    model="mimo-v2.5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "你好"}]
)
print(response.content[0].text)
```

### curl
```bash
# OpenAI 格式
curl https://你的域名/v1/chat/completions \
  -H "Authorization: Bearer sk-用户的子Key" \
  -H "Content-Type: application/json" \
  -d '{"model": "mimo-v2.5", "messages": [{"role": "user", "content": "你好"}]}'

# Claude 格式
curl https://你的域名/v1/messages \
  -H "x-api-key: sk-用户的子Key" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model": "mimo-v2.5", "max_tokens": 1024, "messages": [{"role": "user", "content": "你好"}]}'
```

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `UPSTREAM_API_KEY` | 你的真实 API 密钥 | （必填） |
| `OPENAI_BASE_URL` | OpenAI 上游地址 | `https://token-plan-cn.xiaomimimo.com/v1` |
| `CLAUDE_BASE_URL` | Claude 上游地址 | `https://token-plan-cn.xiaomimimo.com/anthropic` |
| `CLAUDE_VERSION` | Claude API 版本号 | `2023-06-01` |
| `ADMIN_TOKEN` | 管理面板密码 | `admin123` |
| `PORT` | 服务端口（云平台自动设置） | `8800` |
| `DATA_DIR` | 数据库存储目录 | 当前目录 |

---

## 目录结构

```
mimo-api-proxy/
├── main.py           # 主程序（FastAPI 代理 + 管理 API）
├── database.py       # SQLite 数据库操作
├── templates/
│   ├── login.html    # 登录页
│   └── dashboard.html # 管理面板
├── requirements.txt  # Python 依赖
├── Dockerfile        # Docker 部署
├── Procfile          # Railway 部署
├── render.yaml       # Render 部署
├── runtime.txt       # Python 版本
├── start.bat         # Windows 一键启动
└── README.md
```

## 工作原理

```
用户 (OpenAI SDK / Claude SDK)
  │
  ├─ Authorization: Bearer sk-子Key  ──→  检测为 OpenAI 协议
  │                                         ↓
  │                                    替换为真实 Key
  │                                    转发到 mimo 上游
  │
  └─ x-api-key: sk-子Key  ──────────→  检测为 Claude 协议
                                            ↓
                                       替换为真实 Key
                                       转发到 mimo 上游

  所有请求的 prompt + response → SQLite 记录 → 管理面板查看
```
