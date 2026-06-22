"""
API Proxy — 带完整使用记录的 API 代理服务

兼容两种协议：
  - OpenAI 格式: /v1/chat/completions  (Authorization: Bearer xxx)
  - Claude 格式:  /v1/messages         (x-api-key: xxx)

功能：
  1. 自动检测协议，透明代理到上游 API
  2. 每个用户独立子Key，记录所有提示词和响应
  3. Web 管理面板：用户管理 + 使用统计 + 请求日志
  4. 支持流式(SSE)和非流式请求

启动：
  python main.py
  或 uvicorn main:app --host 0.0.0.0 --port 8800
"""
import os
import json
import time
import uuid
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db

# ---- Configuration ----
# 上游 API Key（你的真实密钥）
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY", "")

# mimo 官方上游（同时兼容 OpenAI 和 Claude 格式）
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
CLAUDE_BASE_URL = os.environ.get("CLAUDE_BASE_URL", "https://token-plan-cn.xiaomimimo.com/anthropic")
CLAUDE_VERSION = os.environ.get("CLAUDE_VERSION", "2023-06-01")

# 管理面板
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin123")
# 云平台会通过 PORT 环境变量指定端口
PROXY_PORT = int(os.environ.get("PORT", os.environ.get("PROXY_PORT", "8800")))

app = FastAPI(title="API Proxy", version="2.0.0")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))


# ---- Protocol Detection ----

class Protocol:
    OPENAI = "openai"
    CLAUDE = "claude"


def detect_protocol(request: Request, path: str) -> str:
    """根据请求特征自动检测协议"""
    # Claude 协议特征：x-api-key header 或 /v1/messages 路径
    if request.headers.get("x-api-key"):
        return Protocol.CLAUDE
    if path.endswith("/messages") or "/messages" in path:
        return Protocol.CLAUDE
    # 默认 OpenAI
    return Protocol.OPENAI


# ---- Auth ----

async def verify_sub_key(request: Request) -> dict:
    """从请求中提取并验证子Key，支持两种协议的认证方式"""
    sub_key = None

    # 方式1: Authorization: Bearer <sub_key> (OpenAI 格式)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        sub_key = auth[7:].strip()

    # 方式2: x-api-key: <sub_key> (Claude 格式)
    if not sub_key:
        sub_key = request.headers.get("x-api-key", "").strip()

    if not sub_key:
        raise HTTPException(401, "Missing authentication. Use 'Authorization: Bearer <key>' or 'x-api-key: <key>'")

    user = db.get_user_by_key(sub_key)
    if not user:
        raise HTTPException(403, "Invalid or disabled sub-key")

    # 检查调用次数限制
    if user["max_calls"] > 0:
        count = db.get_user_call_count(user["id"])
        if count >= user["max_calls"]:
            raise HTTPException(429, f"Call limit reached ({user['max_calls']} calls)")

    return user


def verify_admin(request: Request):
    """管理面板认证"""
    token = request.cookies.get("admin_token") or request.query_params.get("token")
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "Unauthorized")


# ---- Prompt Extraction ----

def extract_openai_prompt(data: dict) -> tuple[str, str]:
    """从 OpenAI 格式提取 prompt 文本和 model"""
    model = data.get("model", "")
    messages = data.get("messages", [])
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(f"[{role}] {c.get('text', '')}")
                    elif c.get("type") == "image_url":
                        parts.append(f"[{role}] [图片]")
    return "\n".join(parts), model


def extract_claude_prompt(data: dict) -> tuple[str, str]:
    """从 Claude 格式提取 prompt 文本和 model"""
    model = data.get("model", "")
    parts = []
    # system
    system = data.get("system", "")
    if system:
        if isinstance(system, str):
            parts.append(f"[system] {system}")
        elif isinstance(system, list):
            for s in system:
                if isinstance(s, dict) and s.get("type") == "text":
                    parts.append(f"[system] {s.get('text', '')}")
    # messages
    messages = data.get("messages", [])
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(f"[{role}] {c.get('text', '')}")
                    elif c.get("type") == "image":
                        parts.append(f"[{role}] [图片]")
                    elif c.get("type") == "tool_use":
                        parts.append(f"[{role}] [工具调用: {c.get('name', '')}]")
                    elif c.get("type") == "tool_result":
                        parts.append(f"[{role}] [工具结果]")
    return "\n".join(parts), model


def extract_prompt(body_str: str, protocol: str) -> tuple[str, str, bool]:
    """统一提取 prompt、model、is_stream"""
    if not body_str:
        return "", "", False
    try:
        data = json.loads(body_str)
    except json.JSONDecodeError:
        return "", "", False

    is_stream = data.get("stream", False)

    if protocol == Protocol.CLAUDE:
        prompt, model = extract_claude_prompt(data)
    else:
        prompt, model = extract_openai_prompt(data)

    return prompt, model, is_stream


# ---- Response Extraction ----

def extract_openai_response(resp_text: str) -> tuple[str, int, int, int]:
    """从 OpenAI 响应提取回复文本和 token 用量"""
    response_text = ""
    prompt_tokens = completion_tokens = total_tokens = 0
    try:
        data = json.loads(resp_text)
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            parts = []
            content = msg.get("content", "")
            if content:
                parts.append(content)
            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                parts.append(f"[思考] {reasoning}")
            response_text = "\n".join(parts)
    except (json.JSONDecodeError, IndexError, KeyError):
        response_text = resp_text[:2000]
    return response_text, prompt_tokens, completion_tokens, total_tokens


def extract_claude_response(resp_text: str) -> tuple[str, int, int, int]:
    """从 Claude 响应提取回复文本和 token 用量"""
    response_text = ""
    input_tokens = output_tokens = total_tokens = 0
    try:
        data = json.loads(resp_text)
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens
        # content blocks
        content = data.get("content", [])
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "thinking":
                    parts.append(f"[思考] {c.get('thinking', '')}")
                elif c.get("type") == "tool_use":
                    parts.append(f"[工具调用: {c.get('name', '')}]")
        response_text = "\n".join(parts)
    except (json.JSONDecodeError, IndexError, KeyError):
        response_text = resp_text[:2000]
    return response_text, input_tokens, output_tokens, total_tokens


# ---- Upstream Headers ----

def build_upstream_headers(request: Request, protocol: str) -> dict:
    """构建上游请求 headers，替换认证信息"""
    headers = dict(request.headers)
    # 移除 hop-by-hop 和客户端认证
    for h in ["host", "connection", "transfer-encoding", "authorization", "x-api-key"]:
        headers.pop(h, None)

    if protocol == Protocol.CLAUDE:
        headers["x-api-key"] = UPSTREAM_API_KEY
        headers["anthropic-version"] = CLAUDE_VERSION
        # Claude 不需要 Authorization
        headers.pop("authorization", None)
    else:
        headers["authorization"] = f"Bearer {UPSTREAM_API_KEY}"
        headers.pop("x-api-key", None)

    return headers


def get_upstream_url(protocol: str, path: str, query: str = "") -> str:
    """构建上游 URL"""
    if protocol == Protocol.CLAUDE:
        # Claude: /v1/messages -> https://xxx/anthropic/v1/messages
        # CLAUDE_BASE_URL 是 https://xxx/anthropic，保留 path 中的 /v1
        base = CLAUDE_BASE_URL.rstrip("/")
        url = f"{base}{path}"
    else:
        # OpenAI: /v1/chat/completions -> https://xxx/v1/chat/completions
        # OPENAI_BASE_URL 已包含 /v1，去掉 path 中的 /v1 前缀避免重复
        base = OPENAI_BASE_URL.rstrip("/")
        sub_path = path
        if sub_path.startswith("/v1/"):
            sub_path = sub_path[3:]  # /v1/chat/completions -> /chat/completions
        url = f"{base}{sub_path}"
    if query:
        url += f"?{query}"
    return url


# ---- Health Check (must be before catch-all) ----

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "has_api_key": bool(UPSTREAM_API_KEY),
        "protocols": ["openai", "claude"],
    }


# ---- Admin Dashboard (must be before catch-all) ----

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """管理面板首页"""
    token = request.cookies.get("admin_token")
    if token != ADMIN_TOKEN:
        return templates.TemplateResponse(request=request, name="login.html")
    stats = db.get_stats()
    users = db.list_users()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"stats": stats, "users": users})


@app.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    token = form.get("token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "密码错误")
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie("admin_token", token, max_age=86400 * 7, httponly=True)
    return resp


@app.get("/admin/api/users")
async def api_list_users(request: Request):
    verify_admin(request)
    return db.list_users()


@app.post("/admin/api/users")
async def api_create_user(request: Request):
    verify_admin(request)
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    sub_key = f"sk-{uuid.uuid4().hex[:24]}"
    max_calls = int(data.get("max_calls", 0))
    user = db.create_user(sub_key, name, max_calls)
    return user


@app.put("/admin/api/users/{user_id}")
async def api_update_user(user_id: int, request: Request):
    verify_admin(request)
    data = await request.json()
    db.update_user(user_id, **data)
    return {"ok": True}


@app.delete("/admin/api/users/{user_id}")
async def api_delete_user(user_id: int, request: Request):
    verify_admin(request)
    db.delete_user(user_id)
    return {"ok": True}


@app.get("/admin/api/logs")
async def api_get_logs(
    request: Request,
    user_id: int = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    start_date: str = Query(None),
    end_date: str = Query(None),
    keyword: str = Query(None),
):
    verify_admin(request)
    offset = (page - 1) * page_size
    logs = db.get_logs(user_id=user_id, limit=page_size, offset=offset,
                       start_date=start_date, end_date=end_date, keyword=keyword)
    total = db.get_logs_count(user_id=user_id, start_date=start_date, end_date=end_date, keyword=keyword)
    return {"logs": logs, "total": total, "page": page, "page_size": page_size}


@app.get("/admin/api/stats")
async def api_get_stats(request: Request):
    verify_admin(request)
    return db.get_stats()


# ---- API Proxy (catch-all must be last) ----

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_v1(path: str, request: Request, user: dict = Depends(verify_sub_key)):
    """代理 /v1/* 请求"""
    return await _proxy(request, f"/v1/{path}", user)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_catch_all(path: str, request: Request, user: dict = Depends(verify_sub_key)):
    """代理其他请求"""
    return await _proxy(request, f"/{path}", user)


async def _proxy(request: Request, full_path: str, user: dict):
    """核心代理逻辑 — 自动检测协议"""
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace") if body else ""

    protocol = detect_protocol(request, full_path)
    prompt_text, model, is_stream = extract_prompt(body_str, protocol)

    target_url = get_upstream_url(protocol, full_path, request.url.query)
    headers = build_upstream_headers(request, protocol)

    start_time = time.time()

    if is_stream:
        return await _proxy_stream(target_url, headers, body, user, full_path,
                                   model, prompt_text, start_time, protocol)
    else:
        return await _proxy_normal(target_url, headers, body, user, full_path,
                                   model, prompt_text, start_time, protocol)


async def _proxy_normal(target_url, headers, body, user, endpoint,
                         model, prompt_text, start_time, protocol):
    """非流式代理"""
    try:
        resp = await http_client.post(url=target_url, headers=headers, content=body)
        latency = int((time.time() - start_time) * 1000)
        resp_text = resp.text

        # 按协议解析响应
        if protocol == Protocol.CLAUDE:
            response_text, pt, ct, tt = extract_claude_response(resp_text)
        else:
            response_text, pt, ct, tt = extract_openai_response(resp_text)

        # 记录日志
        db.log_request(
            user_id=user["id"],
            endpoint=endpoint,
            method="POST",
            request_body=prompt_text[:10000],
            response_body=response_text[:10000],
            status_code=resp.status_code,
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            latency_ms=latency,
        )

        # 返回响应（保持原始格式）
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return JSONResponse(
                content=resp.json(),
                status_code=resp.status_code,
                headers={"X-Proxy-Latency-Ms": str(latency)},
            )
        else:
            return JSONResponse(
                content={"raw": resp_text},
                status_code=resp.status_code,
                headers={"X-Proxy-Latency-Ms": str(latency)},
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "Upstream API timeout")
    except httpx.ConnectError:
        raise HTTPException(502, "Cannot connect to upstream API")


async def _proxy_stream(target_url, headers, body, user, endpoint,
                         model, prompt_text, start_time, protocol):
    """流式(SSE)代理，兼容 OpenAI 和 Claude 的 SSE 格式"""
    collected_response = []
    stream_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # OpenAI: 注入 stream_options 以在流末尾获取 usage
    if protocol == Protocol.OPENAI:
        try:
            body_data = json.loads(body)
            body_data["stream_options"] = {"include_usage": True}
            body = json.dumps(body_data).encode("utf-8")
        except (json.JSONDecodeError, Exception):
            pass

    async def stream_generator():
        try:
            async with http_client.stream("POST", url=target_url, headers=headers, content=body) as resp:
                latency = int((time.time() - start_time) * 1000)

                if protocol == Protocol.CLAUDE:
                    # Claude SSE: event: content_block_delta\ndata: {"delta":{"text":"..."}}
                    current_event = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                            yield line + "\n"
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            try:
                                chunk = json.loads(data_str)
                                # 收集 content_block_delta 中的文本
                                if chunk.get("type") == "content_block_delta":
                                    delta = chunk.get("delta", {})
                                    text = delta.get("text", "")
                                    if text:
                                        collected_response.append(text)
                                # 从 message_delta 提取 token 用量
                                if chunk.get("type") == "message_delta":
                                    usage = chunk.get("usage", {})
                                    if usage:
                                        stream_usage["completion_tokens"] = usage.get("output_tokens", 0)
                            except json.JSONDecodeError:
                                pass
                            yield line + "\n"
                            continue
                        # 空行或其他行直接透传
                        yield line + "\n"

                else:
                    # OpenAI SSE: data: {"choices":[{"delta":{"content":"..."}}]}
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                yield "data: [DONE]\n\n"
                                continue
                            try:
                                chunk = json.loads(data_str)
                                # 提取 usage（流末尾的 chunk 包含 usage）
                                usage = chunk.get("usage")
                                if usage:
                                    stream_usage["prompt_tokens"] = usage.get("prompt_tokens", 0)
                                    stream_usage["completion_tokens"] = usage.get("completion_tokens", 0)
                                    stream_usage["total_tokens"] = usage.get("total_tokens", 0)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        collected_response.append(content)
                            except json.JSONDecodeError:
                                pass
                        yield line + "\n"

                # 流结束后记录日志
                full_response = "".join(collected_response)
                db.log_request(
                    user_id=user["id"],
                    endpoint=endpoint,
                    method="POST",
                    request_body=prompt_text[:10000],
                    response_body=full_response[:10000],
                    status_code=resp.status_code,
                    model=model,
                    prompt_tokens=stream_usage["prompt_tokens"],
                    completion_tokens=stream_usage["completion_tokens"],
                    total_tokens=stream_usage["total_tokens"],
                    latency_ms=latency,
                )
        except Exception as e:
            err_msg = json.dumps({"error": str(e)})
            if protocol == Protocol.CLAUDE:
                yield f"event: error\ndata: {err_msg}\n\n"
            else:
                yield f"data: {err_msg}\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Startup ----

@app.on_event("startup")
async def startup():
    if not UPSTREAM_API_KEY:
        print("WARNING: UPSTREAM_API_KEY not set!")
    print(f"API Proxy started")
    print(f"  Proxy:    http://0.0.0.0:{PROXY_PORT}")
    print(f"  OpenAI:   http://0.0.0.0:{PROXY_PORT}/v1/chat/completions")
    print(f"  Claude:   http://0.0.0.0:{PROXY_PORT}/v1/messages")
    print(f"  Dashboard: http://0.0.0.0:{PROXY_PORT}/dashboard")
    print(f"  Admin:    {ADMIN_TOKEN}")
    print(f"  Database: {db.DB_PATH}")


@app.on_event("shutdown")
async def shutdown():
    await http_client.aclose()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT)
