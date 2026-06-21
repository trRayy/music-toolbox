import os
import subprocess
import tkinter as tk

from config_loader import get_setting


STREAMLIT_EXE = get_setting("STREAMLIT_EXE", "streamlit")
APP_SCRIPT = get_setting("DASHBOARD_SCRIPT", os.path.join(os.path.dirname(__file__), "music_dashboard63.py"))


def run_streamlit():
    subprocess.Popen([STREAMLIT_EXE, "run", APP_SCRIPT])


root = tk.Tk()
root.title("Music Dashboard Launcher")
root.geometry("320x160")

label = tk.Label(root, text="Click the button to start the Streamlit dashboard")
label.pack(pady=20)

btn = tk.Button(
    root,
    text="Start dashboard",
    command=run_streamlit,
    height=2,
    width=20,
)
btn.pack()

root.mainloop()
