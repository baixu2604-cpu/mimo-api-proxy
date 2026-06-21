@echo off
chcp 65001 >nul
echo ========================================
echo   API Proxy - 启动（兼容 OpenAI + Claude）
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 安装依赖
if not exist ".venv" (
    echo [SETUP] 正在创建虚拟环境...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [SETUP] 正在安装依赖...
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM 检查环境变量
if "%UPSTREAM_API_KEY%"=="" (
    echo.
    echo [WARN] 未设置 UPSTREAM_API_KEY 环境变量
    echo        请输入你的真实 mimo API Key:
    set /p UPSTREAM_API_KEY="API Key: "
)

if "%ADMIN_TOKEN%"=="" set ADMIN_TOKEN=admin123

REM 清除代理环境变量（避免 Clash 等代理干扰）
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=

REM 设置 mimo 上游地址
if "%OPENAI_BASE_URL%"=="" set OPENAI_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
if "%CLAUDE_BASE_URL%"=="" set CLAUDE_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic

echo.
echo [OK] 启动代理服务...
echo   本地地址:  http://localhost:8800
echo   管理面板:  http://localhost:8800/dashboard
echo   管理密码:  %ADMIN_TOKEN%
echo.

REM 启动代理服务（后台）
start "API Proxy" python main.py

REM 等待代理启动
timeout /t 3 /nobreak >nul

REM 启动内网穿透
echo [OK] 启动内网穿透...
echo.
lt --port 8800 --print-requests 2>&1
pause
