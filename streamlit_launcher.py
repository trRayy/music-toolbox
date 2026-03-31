import tkinter as tk
import subprocess
import os

# ===== 配置区（只改这里）=====
STREAMLIT_EXE = r"C:\Users\User\anaconda3\Scripts\streamlit.exe"
APP_SCRIPT = r"C:\Users\User\Desktop\auto\music_dashboard63.py"
# =============================

def run_streamlit():
    cmd = f'"{STREAMLIT_EXE}" run "{APP_SCRIPT}"'
    subprocess.Popen(cmd, shell=True)

root = tk.Tk()
root.title("音乐数据看板启动器")
root.geometry("300x150")

label = tk.Label(root, text="点击按钮启动 Streamlit 看板")
label.pack(pady=20)

btn = tk.Button(
    root,
    text="🚀 启动音乐数据看板",
    command=run_streamlit,
    height=2,
    width=20
)
btn.pack()

root.mainloop()
