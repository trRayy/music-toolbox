import tkinter as tk
from tkinter import ttk, messagebox
import pymysql
import traceback
from config_loader import get_mysql_config

# ===== MySQL 配置（加超时，避免卡死）=====
MYSQL_CONFIG = get_mysql_config()

DEFAULT_LIMIT = 200


def log(msg: str):
    print(msg, flush=True)


class MySQLViewer(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.conn = None

        # 顶部控制栏
        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", padx=10, pady=10)

        ttk.Label(ctrl, text="表：").pack(side="left")
        self.table_var = tk.StringVar()
        self.table_combo = ttk.Combobox(ctrl, textvariable=self.table_var, state="readonly", width=28)
        self.table_combo.pack(side="left", padx=6)
        self.table_combo.bind("<<ComboboxSelected>>", lambda e: self.load_data())

        ttk.Label(ctrl, text="限制行数：").pack(side="left")
        self.limit_var = tk.StringVar(value=str(DEFAULT_LIMIT))
        ttk.Entry(ctrl, textvariable=self.limit_var, width=8).pack(side="left", padx=6)

        ttk.Button(ctrl, text="连接 / 刷新表", command=self.load_tables).pack(side="left", padx=6)
        ttk.Button(ctrl, text="刷新数据", command=self.load_data).pack(side="left")

        self.status_var = tk.StringVar(value="未连接数据库")
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", padx=10)

        # 表格区
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.tree = ttk.Treeview(frame, show="headings")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        # 关键：先让窗口渲染出来，再连接 MySQL（避免“没反应”）
        self.after(150, self.load_tables)

    def connect(self):
        if self.conn:
            return True
        try:
            log("[connect] connecting to MySQL...")
            self.conn = pymysql.connect(**MYSQL_CONFIG)
            log("[connect] connected OK")
            return True
        except Exception as e:
            log("[connect] FAILED: " + repr(e))
            log(traceback.format_exc())
            messagebox.showerror("连接失败", f"{e}")
            self.status_var.set("连接失败（见终端输出）")
            self.conn = None
            return False

    def load_tables(self):
        if not self.connect():
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SHOW TABLES;")
                tables = [r[0] for r in cur.fetchall()]
            self.table_combo["values"] = tables

            if tables:
                self.table_combo.current(0)
                self.status_var.set(f"已连接：{MYSQL_CONFIG['db']}（{len(tables)} 张表）")
                self.load_data()
            else:
                self.status_var.set("数据库中没有表")
                self.clear_tree()
        except Exception as e:
            log("[load_tables] FAILED: " + repr(e))
            log(traceback.format_exc())
            messagebox.showerror("读取表失败", f"{e}")

    def clear_tree(self):
        self.tree["columns"] = []
        for i in self.tree.get_children():
            self.tree.delete(i)

    def load_data(self):
        if not self.connect():
            return

        table = self.table_var.get()
        if not table:
            return

        try:
            limit = int(self.limit_var.get())
        except Exception:
            messagebox.showerror("参数错误", "限制行数必须是整数")
            return

        try:
            with self.conn.cursor() as cur:
                cur.execute(f"DESCRIBE `{table}`;")
                cols = [r[0] for r in cur.fetchall()]

                cur.execute(f"SELECT * FROM `{table}` LIMIT %s;", (limit,))
                rows = cur.fetchall()

            self.tree["columns"] = cols
            for c in cols:
                self.tree.heading(c, text=c)
                self.tree.column(c, width=140, stretch=True)

            for i in self.tree.get_children():
                self.tree.delete(i)

            for row in rows:
                self.tree.insert("", "end", values=row)

            self.status_var.set(f"表：{table} | 显示 {len(rows)} 行（limit={limit}）")
        except Exception as e:
            log("[load_data] FAILED: " + repr(e))
            log(traceback.format_exc())
            messagebox.showerror("查询失败", f"{e}")


def main():
    log("[main] script started")

    root = tk.Tk()
    root.title("MySQL 数据库查看器（只读）")
    root.geometry("1000x600")

    # 防止窗口跑到屏幕外：居中显示
    root.update_idletasks()
    w, h = 1000, 600
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")

    log("[main] tk window created")
    MySQLViewer(root).pack(fill="both", expand=True)

    root.mainloop()
    log("[main] exited")


if __name__ == "__main__":
    main()
