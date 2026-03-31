@echo off
chcp 65001 >nul

REM 1) 初始化 conda
call "C:\Users\User\anaconda3\Scripts\activate.bat" "C:\Users\User\anaconda3"

REM 2) 激活你的环境（如果不是 base，改成你的环境名）
call conda activate base

REM 3) 启动 GUI 启动器（改成 app_launcher.py 的实际路径）
python "C:\Users\User\Desktop\auto\toolbox_gui_v4.1.py"

pause
