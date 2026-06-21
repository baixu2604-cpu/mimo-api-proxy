@echo off
chcp 65001 >nul
echo ========================================
echo   内网穿透 - 启动
echo ========================================
echo.
echo 请选择穿透方式:
echo   1. localtunnel (免费，无需注册)
echo   2. ngrok (需要注册免费账号)
echo   3. 退出
echo.
set /p choice="请输入选择 (1-3): "

if "%choice%"=="1" goto lt
if "%choice%"=="2" goto ngrok
if "%choice%"=="3" goto end
echo 无效选择
pause
goto end

:lt
echo.
echo [INFO] 启动 localtunnel...
echo [INFO] 首次访问需要在浏览器点击 "Click to Continue"
echo [INFO] API 调用需加 header: bypass-tunnel-reminder: true
echo.
lt --port 8800
pause
goto end

:ngrok
echo.
echo [INFO] 启动 ngrok...
echo [INFO] 首次使用需要配置 authtoken:
echo   1. 访问 https://dashboard.ngrok.com/signup 注册免费账号
echo   2. 访问 https://dashboard.ngrok.com/get-started/your-authtoken 复制 token
echo   3. 运行: ngrok config add-authtoken YOUR_TOKEN
echo.
ngrok http 8800
pause
goto end

:end
