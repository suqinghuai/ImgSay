import os
import sys
import base64
import requests
import time
import configparser
import datetime
import mimetypes
import argparse
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import scrolledtext
from natsort import natsorted
# 仅保留基础彩色（用于关键提示）
from colorama import init, Fore

# 初始化colorama（兼容Windows）
init(autoreset=True)

LOG_SINK = None

# ===================== 基础日志函数（简化版） =====================
def set_log_sink(sink):
    """设置日志接收器（GUI使用）"""
    global LOG_SINK
    LOG_SINK = sink


def _emit_log(level, msg, color=None):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    if LOG_SINK:
        LOG_SINK.emit(level, msg, timestamp)
        return
    prefix = f"[{timestamp}] {msg}"
    if color:
        print(f"{color}{prefix}")
    else:
        print(prefix)


def log_info(msg):
    """普通信息（默认颜色）"""
    _emit_log("info", msg)

def log_success(msg):
    """成功信息（绿色）"""
    _emit_log("success", msg, Fore.GREEN)

def log_error(msg):
    """错误信息（红色）"""
    _emit_log("error", msg, Fore.RED)


def get_base_dir():
    if getattr(sys, "_MEIPASS", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resolve_path(base_dir, path_value):
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(base_dir, path_value)


def read_config_file(base_dir):
    config = configparser.ConfigParser()
    config_path = os.path.join(base_dir, "config.ini")
    log_info(f"读取配置文件：{config_path}")

    if not os.path.exists(config_path):
        raise FileNotFoundError("配置文件不存在，请确保config.ini与程序同目录")

    config.read(config_path, encoding="utf-8")
    raw_items = {
        "API_KEY": config.get("ModelScope", "API_KEY", fallback=""),
        "API_URL": config.get(
            "ModelScope",
            "API_URL",
            fallback="https://api-inference.modelscope.cn/v1/chat/completions",
        ),
        "MODEL_ID": config.get("ModelScope", "MODEL_ID", fallback="Qwen/Qwen3-VL-8B-Instruct"),
        "IMG_FOLDER": config.get("ModelScope", "IMG_FOLDER", fallback="图片/"),
        "RESULT_FILE": config.get("ModelScope", "RESULT_FILE", fallback="识别结果.txt"),
        "BATCH_SIZE": config.get("ModelScope", "BATCH_SIZE", fallback="4"),
        "ERROR_HANDLING": config.get("ModelScope", "ERROR_HANDLING", fallback="skip"),
    }
    return config, raw_items, config_path

# ===================== 加载配置（简化版） =====================
def load_config(strict=True):
    """加载配置文件（保留核心校验）"""
    base_dir = get_base_dir()

    try:
        _, raw_items, _ = read_config_file(base_dir)
        config_items = {
            "API_KEY": raw_items["API_KEY"].strip(),
            "API_URL": raw_items["API_URL"].strip(),
            "MODEL_ID": raw_items["MODEL_ID"].strip(),
            "IMG_FOLDER": resolve_path(base_dir, raw_items["IMG_FOLDER"].strip()),
            "RESULT_FILE": resolve_path(base_dir, raw_items["RESULT_FILE"].strip()),
            "BATCH_SIZE": int(raw_items["BATCH_SIZE"]),
            "ERROR_HANDLING": raw_items["ERROR_HANDLING"].strip().lower() or "skip",
        }

        if not config_items["API_KEY"] or config_items["API_KEY"].lower().startswith("your"):
            log_error("警告：请在config.ini中填写有效的API_KEY")
        if not os.path.exists(config_items["IMG_FOLDER"]):
            log_error(f"错误：图片文件夹不存在 {config_items['IMG_FOLDER']}")
            if strict:
                input("\n按任意键退出...")
                exit(1)

        log_success("配置加载完成")
        return config_items
    except Exception as e:
        log_error(f"配置读取失败：{str(e)}")
        if strict:
            input("\n按任意键退出...")
            exit(1)
        return None

# ===================== 图片转Base64（简化版） =====================
def get_image_base64(img_path):
    """读取图片转Base64（符合魔搭社区文档规范）"""
    try:
        if not os.path.isfile(img_path):
            return ""

        with open(img_path, "rb") as f:
            image_data = f.read()

        mime_type, _ = mimetypes.guess_type(img_path)
        if mime_type is None or not mime_type.startswith('image/'):
            mime_type = 'image/png'

        base64_encoded = base64.b64encode(image_data).decode('utf-8')
        return f"data:{mime_type};base64,{base64_encoded}"
    except Exception as e:
        log_error(f"图片处理失败 {img_path}：{str(e)}")
        return ""

# ===================== 处理图片识别（简化版） =====================
def process_batch(config, pause_event=None, progress_callback=None):
    """处理图片识别（保留核心日志）"""
    API_KEY = config["API_KEY"]
    API_URL = config["API_URL"]
    MODEL_ID = config["MODEL_ID"]
    IMG_FOLDER = config["IMG_FOLDER"]
    RESULT_FILE = config["RESULT_FILE"]
    BATCH_SIZE = config["BATCH_SIZE"]

    # 扫描图片
    supported_ext = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    img_files = [f for f in os.listdir(IMG_FOLDER) if f.lower().endswith(supported_ext)]
    img_files = natsorted(img_files)
    
    log_info(f"找到可识别图片：{len(img_files)} 张")
    if not img_files:
        log_error("错误：图片文件夹中无支持的图片（jpg/png/webp等）")
        return

    # 分批处理
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    total_batches = (len(img_files) + BATCH_SIZE - 1) // BATCH_SIZE
    
    with open(RESULT_FILE, "a", encoding="utf-8") as f_out:
        for batch_idx in range(total_batches):
            # 检查是否暂停
            if pause_event is not None:
                while pause_event.is_set():
                    time.sleep(0.1)
            
            batch_files = img_files[batch_idx*BATCH_SIZE : (batch_idx+1)*BATCH_SIZE]
            log_info(f"处理第{batch_idx+1}/{total_batches}批：{', '.join(batch_files)}")
            
            # 更新进度
            if progress_callback:
                progress_callback(batch_idx + 1, total_batches, len(img_files))
            
            # 构建请求
            content = []
            # 按照文档示例，先放入文本描述
            content.append({"type": "text", "text": f"请提取以下{len(batch_files)}张图片中的文字内容，按顺序列出。"})
            
            for img_name in batch_files:
                img_data_url = get_image_base64(os.path.join(IMG_FOLDER, img_name))
                if img_data_url:
                    content.append({"type": "image_url", "image_url": {"url": img_data_url}})
            
            if len(content) <= 1:
                log_error("该批次无有效图片，跳过")
                continue
            
            payload = {"model": MODEL_ID, "messages": [{"role": "user", "content": content}], "stream": False}
            
            # 发送请求（根据错误处理策略处理）
            error_handling = config.get("ERROR_HANDLING", "skip").lower()
            
            # 根据错误处理策略设置重试次数
            if error_handling == "retry":
                max_retries = float('inf')  # 无限重试
            else:
                max_retries = 3
            
            retry_count = 0
            while retry_count < max_retries:
                try:
                    response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
                    response.raise_for_status()
                    result_text = response.json()['choices'][0]['message']['content']
                    
                    # 保存结果
                    f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n{result_text}\n---\n")
                    f_out.flush()
                    log_success(f"第{batch_idx+1}批处理完成")
                    
                    # 更新进度
                    if progress_callback:
                        progress_callback(batch_idx + 1, total_batches, len(img_files))
                    
                    break
                except Exception as e:
                    retry_count += 1
                    wait_time = 2 + retry_count  # 每次重试间隔+1秒
                    
                    if retry_count >= max_retries or (error_handling != "retry" and retry_count >= 3):
                        # 达到最大重试次数
                        log_error(f"第{batch_idx+1}批处理失败：{str(e)}")
                        
                        # 根据错误处理策略处理失败
                        if error_handling == "skip":
                            log_info(f"跳过第{batch_idx+1}批，继续处理下一批")
                            f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n处理失败（已跳过）：{str(e)}\n---\n")
                        elif error_handling == "retry":
                            log_error(f"第{batch_idx+1}批重试{retry_count}次后仍然失败，跳过")
                            f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n处理失败（重试{retry_count}次后跳过）：{str(e)}\n---\n")
                        elif error_handling == "stop":
                            log_error(f"第{batch_idx+1}批处理失败，停止处理")
                            f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n处理失败（已停止）：{str(e)}\n---\n")
                            return
                    else:
                        # 还在重试中
                        log_info(f"第{batch_idx+1}批处理失败，等待{wait_time}秒后重试 (第{retry_count}次重试)")
                    time.sleep(wait_time)
    
    log_success(f"所有任务完成，结果保存至：{RESULT_FILE}")


class GuiLogSink:
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.queue = queue.Queue()

    def emit(self, level, msg, timestamp):
        self.queue.put((level, msg, timestamp))

    def drain(self):
        try:
            while True:
                level, msg, timestamp = self.queue.get_nowait()
                color = {"info": "#e6edf3", "success": "#7ee787", "error": "#ffa198"}.get(level, "#e6edf3")
                self.text_widget.configure(state="normal")
                self.text_widget.insert("end", f"[{timestamp}] {msg}\n", (level,))
                self.text_widget.tag_config(level, foreground=color)
                self.text_widget.see("end")
                self.text_widget.configure(state="disabled")
        except queue.Empty:
            pass


class ImgSayApp:
    def __init__(self, root):
        self.root = root
        self.root.title("图言 ImgSay - 图片文字识别")
        self.root.geometry("1000x700")
        self.root.minsize(900, 650)
        self.root.configure(bg="#0d1117")

        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        self.style.configure("TFrame", background="#0d1117")
        self.style.configure("TLabel", background="#0d1117", foreground="#e6edf3", font=("Segoe UI", 10))
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=(16, 8), relief="flat")
        self.style.configure("TEntry", fieldbackground="#161b22", foreground="#e6edf3", padding=(8, 8), relief="flat")
        self.style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#f0f6fc", background="#0d1117")
        self.style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#8b949e", background="#0d1117")
        self.style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground="#58a6ff", background="#0d1117")
        self.style.configure("Card.TFrame", background="#161b22", relief="flat")
        self.style.configure("Notebook", background="#0d1117", borderwidth=0)
        self.style.configure("Notebook.Tab", background="#161b22", foreground="#8b949e", padding=(20, 12), font=("Segoe UI", 10))
        self.style.map("Notebook.Tab", background=[("selected", "#238636")], foreground=[("selected", "#ffffff")])
        self.style.configure("TNotebook", background="#0d1117", borderwidth=0)
        self.style.configure("TNotebook.Frame", background="#0d1117")
        self.style.configure("Primary.TButton", background="#238636", foreground="#ffffff")
        self.style.map("Primary.TButton", background=[("active", "#2ea043"), ("pressed", "#238636")])
        self.style.configure("Secondary.TButton", background="#21262d", foreground="#e6edf3")
        self.style.map("Secondary.TButton", background=[("active", "#30363d")])

        self.base_dir = get_base_dir()
        self.config_path = os.path.join(self.base_dir, "config.ini")
        
        # 初始化暂停相关变量
        self.pause_event = None
        self.is_paused = False

        self._build_layout()
        self._load_config_into_fields()
        self.root.after(120, self._poll_logs)

    def _build_layout(self):
        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True)

        header = ttk.Frame(main_container, style="Card.TFrame")
        header.pack(fill="x", padx=20, pady=(20, 10))
        header.configure(padding=(20, 16))
        ttk.Label(header, text="图言 ImgSay", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="多模态AI图片文字识别工具", style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(main_container)
        notebook.pack(fill="both", expand=True, padx=20, pady=(10, 20))

        run_tab = ttk.Frame(notebook)
        config_tab = ttk.Frame(notebook)
        about_tab = ttk.Frame(notebook)

        notebook.add(run_tab, text="  运行  ")
        notebook.add(config_tab, text="  配置  ")
        notebook.add(about_tab, text="  关于  ")

        self._build_run_tab(run_tab)
        self._build_config_tab(config_tab)
        self._build_about_tab(about_tab)

        self.notebook = notebook

    def _build_config_tab(self, parent):
        canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=860)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=20)
        scrollbar.pack(side="right", fill="y", padx=(0, 20), pady=20)

        container = ttk.Frame(scrollable_frame)
        container.pack(fill="both", expand=True)

        api_card = ttk.Frame(container, style="Card.TFrame")
        api_card.pack(fill="x", pady=(0, 16))
        api_card.configure(padding=(20, 16))
        ttk.Label(api_card, text="API 配置", style="Section.TLabel").pack(anchor="w", pady=(0, 12))

        self.entries = {}
        self._add_field(api_card, "API_KEY", "API Key", show="*")
        self._add_field(api_card, "API_URL", "API URL")
        self._add_field(api_card, "MODEL_ID", "Model ID")

        path_card = ttk.Frame(container, style="Card.TFrame")
        path_card.pack(fill="x", pady=(0, 16))
        path_card.configure(padding=(20, 16))
        ttk.Label(path_card, text="路径配置", style="Section.TLabel").pack(anchor="w", pady=(0, 12))

        self._add_field(path_card, "IMG_FOLDER", "图片文件夹", browse="folder")
        self._add_field(path_card, "RESULT_FILE", "结果文件", browse="file")

        settings_card = ttk.Frame(container, style="Card.TFrame")
        settings_card.pack(fill="x")
        settings_card.configure(padding=(20, 16))
        ttk.Label(settings_card, text="高级设置", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        self._add_field(settings_card, "BATCH_SIZE", "批处理数量")
        self._add_combobox(settings_card, "ERROR_HANDLING", "识别失败处理", ["跳过", "重试", "结束"])

        action_bar = ttk.Frame(container)
        action_bar.pack(fill="x", pady=(20, 0))
        ttk.Button(action_bar, text="保存配置", style="Primary.TButton", command=lambda: self._save_config(notify=True)).pack(side="left", padx=(0, 12))
        ttk.Button(action_bar, text="刷新配置", style="Secondary.TButton", command=lambda: self._load_config_into_fields(notify=True)).pack(side="left")
        
        self._bind_mousewheel(canvas, parent)

    def _bind_mousewheel(self, canvas, parent):
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

    def _build_run_tab(self, parent):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        status_card = ttk.Frame(container, style="Card.TFrame")
        status_card.pack(fill="x", pady=(0, 16))
        status_card.configure(padding=(20, 16))
        ttk.Label(status_card, text="任务控制", style="Section.TLabel").pack(anchor="w", pady=(0, 12))

        action_bar = ttk.Frame(status_card)
        action_bar.pack(fill="x")
        self.run_button = ttk.Button(action_bar, text="开始识别", style="Primary.TButton", command=self._start_processing)
        self.run_button.pack(side="left", padx=(0, 12))
        
        self.pause_button = ttk.Button(action_bar, text="暂停识别", style="Secondary.TButton", command=self._toggle_pause)
        self.pause_button.pack(side="left", padx=(0, 12))
        self.pause_button.config(state="disabled")
        
        ttk.Button(action_bar, text="打开识别结果文件", style="Secondary.TButton", command=self._open_result_file).pack(side="left")

        # 进度条框架
        progress_frame = ttk.Frame(status_card)
        progress_frame.pack(fill="x", pady=(10, 0))
        
        ttk.Label(progress_frame, text="识别进度：").pack(anchor="w")
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100, length=400)
        self.progress_bar.pack(fill="x", pady=(5, 0))
        
        self.progress_label = ttk.Label(progress_frame, text="未开始")
        self.progress_label.pack(anchor="e")

        log_card = ttk.Frame(container, style="Card.TFrame")
        log_card.pack(fill="both", expand=True)
        log_card.configure(padding=(20, 16))
        ttk.Label(log_card, text="运行日志", style="Section.TLabel").pack(anchor="w", pady=(0, 12))

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            bg="#0d1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_sink = GuiLogSink(self.log_text)
        set_log_sink(self.log_sink)

    def _build_about_tab(self, parent):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        about_card = ttk.Frame(container, style="Card.TFrame")
        about_card.pack(fill="both", expand=True)
        about_card.configure(padding=(40, 40))

        ttk.Label(about_card, text="图言 ImgSay", style="Header.TLabel").pack(anchor="center", pady=(0, 8))
        ttk.Label(about_card, text="版本 2.0.0", style="Sub.TLabel").pack(anchor="center")

        info_text = """一个基于多模态AI的图片文字识别工具

功能特点：
• 支持批量图片识别
• 自动分批处理，提高效率
• 支持智能排序，按识别结果排序
• 灵活的配置管理

使用说明：
1. 在配置页面填写API Key和路径信息
2. 将需要识别的图片放入指定文件夹
3. 点击运行页面的"开始识别"按钮
4. 识别结果将保存到指定文件中

技术支持：
430615396@qq.com"""

        info_label = tk.Label(about_card, text=info_text, bg="#161b22", fg="#8b949e", font=("Segoe UI", 10), justify="left", relief="flat")
        info_label.pack(anchor="center", pady=(24, 0))

    def _add_field(self, parent, key, label, show=None, browse=None):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=10)
        
        label_frame = ttk.Frame(frame)
        label_frame.pack(anchor="w", pady=(0, 6))
        ttk.Label(label_frame, text=label).pack(anchor="w")
        
        entry_frame = ttk.Frame(frame)
        entry_frame.pack(fill="x")
        entry_frame.columnconfigure(0, weight=1)
        
        entry = ttk.Entry(entry_frame, show=show)
        entry.pack(side="left", fill="x", expand=True)
        self.entries[key] = entry

        if browse == "folder":
            ttk.Button(entry_frame, text="浏览", style="Secondary.TButton", command=lambda: self._pick_folder(key)).pack(side="left", padx=(10, 0))
        elif browse == "file":
            ttk.Button(entry_frame, text="浏览", style="Secondary.TButton", command=lambda: self._pick_file(key)).pack(side="left", padx=(10, 0))

    def _add_combobox(self, parent, key, label, options):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=10)
        
        label_frame = ttk.Frame(frame)
        label_frame.pack(anchor="w", pady=(0, 6))
        ttk.Label(label_frame, text=label).pack(anchor="w")
        
        combobox = ttk.Combobox(frame, values=options, state="readonly")
        combobox.pack(fill="x")
        combobox.set(options[0])
        self.entries[key] = combobox

    def _pick_folder(self, key):
        path = filedialog.askdirectory(initialdir=self.base_dir)
        if path:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, path)

    def _pick_file(self, key):
        path = filedialog.asksaveasfilename(initialdir=self.base_dir, defaultextension=".txt")
        if path:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, path)

    def _load_config_into_fields(self, notify=False):
        try:
            _, raw_items, _ = read_config_file(self.base_dir)
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return

        for key, widget in self.entries.items():
            value = raw_items.get(key, "")
            if isinstance(widget, ttk.Combobox):
                if value.lower() == "skip":
                    widget.set("跳过")
                elif value.lower() == "retry":
                    widget.set("重试")
                elif value.lower() == "stop":
                    widget.set("结束")
                else:
                    widget.set("跳过")
            else:
                widget.delete(0, "end")
                widget.insert(0, value)
        log_success("配置已加载到界面")
        if notify:
            messagebox.showinfo("刷新成功", "配置已从文件重新加载")

    def _collect_config_from_fields(self):
        values = {}
        for key, widget in self.entries.items():
            if isinstance(widget, ttk.Combobox):
                value = widget.get()
                if value == "跳过":
                    values[key] = "skip"
                elif value == "重试":
                    values[key] = "retry"
                elif value == "结束":
                    values[key] = "stop"
                else:
                    values[key] = "skip"
            else:
                values[key] = widget.get().strip()
        
        if not values["BATCH_SIZE"].isdigit():
            raise ValueError("批处理数量必须是整数")
        config_items = {
            "API_KEY": values["API_KEY"],
            "API_URL": values["API_URL"],
            "MODEL_ID": values["MODEL_ID"],
            "IMG_FOLDER": resolve_path(self.base_dir, values["IMG_FOLDER"]),
            "RESULT_FILE": resolve_path(self.base_dir, values["RESULT_FILE"]),
            "BATCH_SIZE": int(values["BATCH_SIZE"]),
            "ERROR_HANDLING": values["ERROR_HANDLING"],
        }
        return values, config_items

    def _save_config(self, notify=False):
        try:
            raw_values, _ = self._collect_config_from_fields()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return

        config = configparser.ConfigParser()
        config["ModelScope"] = raw_values
        with open(self.config_path, "w", encoding="utf-8") as f:
            config.write(f)
        log_success("配置已保存")
        if notify:
            messagebox.showinfo("保存成功", "配置已保存到文件")

    def _open_result_file(self):
        path = self.entries["RESULT_FILE"].get().strip()
        if not path:
            messagebox.showwarning("提示", "请先填写结果文件路径")
            return
        full_path = resolve_path(self.base_dir, path)
        if not os.path.exists(full_path):
            messagebox.showwarning("提示", "结果文件不存在")
            return
        os.startfile(full_path)

    def _start_processing(self):
        try:
            raw_values, config_items = self._collect_config_from_fields()
        except Exception as e:
            messagebox.showerror("配置错误", str(e))
            return

        if not raw_values["API_KEY"]:
            messagebox.showwarning("提示", "请先填写API Key")
            return
        if not os.path.exists(config_items["IMG_FOLDER"]):
            messagebox.showerror("配置错误", "图片文件夹不存在")
            return

        self._save_config(notify=False)
        self.run_button.configure(state="disabled")
        self.pause_button.config(state="normal")
        log_info("任务开始执行")
        
        # 创建暂停事件
        self.pause_event = threading.Event()
        self.is_paused = False

        threading.Thread(target=self._run_task, args=(config_items,), daemon=True).start()

    def _run_task(self, config_items):
        try:
            # 传递暂停事件和进度回调函数
            process_batch(config_items, pause_event=self.pause_event, progress_callback=self._update_progress)
        except Exception as e:
            log_error(f"任务异常：{str(e)}")
        finally:
            self.run_button.configure(state="normal")
            self.pause_button.config(state="disabled")
            self.is_paused = False

    def _toggle_pause(self):
        if self.is_paused:
            # 继续
            self.pause_event.clear()
            self.is_paused = False
            self.pause_button.config(text="暂停识别")
            log_info("任务继续执行")
        else:
            # 暂停
            self.pause_event.set()
            self.is_paused = True
            self.pause_button.config(text="继续识别")
            log_info("任务已暂停")
    
    def _update_progress(self, current_batch, total_batches, total_images):
        progress_percent = (current_batch / total_batches) * 100
        self.progress_var.set(progress_percent)
        self.progress_label.config(text=f"{current_batch}/{total_batches} ({progress_percent:.1f}%)")
        self.root.update_idletasks()  # 立即更新UI
    
    def _poll_logs(self):
        self.log_sink.drain()
        self.root.after(120, self._poll_logs)

# ===================== 主程序 =====================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="图言 ImgSay")
    parser.add_argument("--cli", action="store_true", help="使用命令行模式运行")
    args = parser.parse_args()

    if args.cli:
        try:
            log_info("程序启动")
            config = load_config()
            process_batch(config)
        except Exception as e:
            log_error(f"程序异常：{str(e)}")
        input("\n按任意键退出...")
    else:
        root = tk.Tk()
        app = ImgSayApp(root)
        root.mainloop()