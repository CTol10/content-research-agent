@echo off
chcp 65001 >nul
echo ============================================
echo   评论抓取工具 - 打包脚本
echo ============================================
echo.

echo [1/2] 检查 PyInstaller...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo 正在安装 PyInstaller...
    pip install pyinstaller --quiet
)
echo PyInstaller 已就绪
echo.

echo [2/2] 开始打包...
pyinstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo 打包失败！请检查错误信息。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成！
echo   输出文件: dist\评论抓取工具.exe
echo ============================================
echo.
echo 分发给用户时，请同时提供:
echo   1. 评论抓取工具.exe
echo   2. config.ini.example (用户复制为 config.ini 并填入 API Key)
echo   3. input.xlsx (输入文件)
echo.
pause
