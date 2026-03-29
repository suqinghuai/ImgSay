import os
import sys
import base64
import requests
import time
import configparser
import datetime
import mimetypes
from natsort import natsorted
# 仅保留基础彩色（用于关键提示）
from colorama import init, Fore

# 初始化colorama（兼容Windows）
init(autoreset=True)

# ===================== 基础日志函数（简化版） =====================
def log_info(msg):
    """普通信息（默认颜色）"""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def log_success(msg):
    """成功信息（绿色）"""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.GREEN}[{timestamp}] {msg}")

def log_error(msg):
    """错误信息（红色）"""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.RED}[{timestamp}] {msg}")

# ===================== 加载配置（简化版） =====================
def load_config():
    """加载配置文件（保留核心校验）"""
    config = configparser.ConfigParser()
    
    # 识别运行环境
    if getattr(sys, '_MEIPASS', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(__file__)
    
    config_path = os.path.join(base_dir, "config.ini")
    log_info(f"读取配置文件：{config_path}")
    
    # 检查配置文件是否存在
    if not os.path.exists(config_path):
        log_error(f"错误：配置文件不存在，请确保config.ini与程序同目录")
        input("\n按任意键退出...")
        exit(1)
    
    try:
        config.read(config_path, encoding="utf-8")
        # 提取配置项
        config_items = {
            "API_KEY": config.get("ModelScope", "API_KEY", fallback=""),
            "API_URL": config.get("ModelScope", "API_URL", fallback="https://api-inference.modelscope.cn/v1/chat/completions"),
            "MODEL_ID": config.get("ModelScope", "MODEL_ID", fallback="Qwen/Qwen3-VL-8B-Instruct"),
            "IMG_FOLDER": os.path.join(base_dir, config.get("ModelScope", "IMG_FOLDER", fallback="图片/")),
            "RESULT_FILE": os.path.join(base_dir, config.get("ModelScope", "RESULT_FILE", fallback="识别结果.txt")),
            "BATCH_SIZE": config.getint("ModelScope", "BATCH_SIZE", fallback=4)
        }
        
        # 核心校验
        if not config_items["API_KEY"] or config_items["API_KEY"] == "YOUR API KEY":
            log_error("警告：请在config.ini中填写有效的API_KEY")
        if not os.path.exists(config_items["IMG_FOLDER"]):
            log_error(f"错误：图片文件夹不存在 {config_items['IMG_FOLDER']}")
            input("\n按任意键退出...")
            exit(1)
        
        log_success("配置加载完成")
        return config_items
    
    except Exception as e:
        log_error(f"配置读取失败：{str(e)}")
        input("\n按任意键退出...")
        exit(1)

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
def process_batch(config):
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
            batch_files = img_files[batch_idx*BATCH_SIZE : (batch_idx+1)*BATCH_SIZE]
            log_info(f"处理第{batch_idx+1}/{total_batches}批：{', '.join(batch_files)}")
            
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
            
            # 发送请求（带重试）
            max_retries = 3
            for retry in range(max_retries):
                try:
                    response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
                    response.raise_for_status()
                    result_text = response.json()['choices'][0]['message']['content']
                    
                    # 保存结果
                    f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n{result_text}\n---\n")
                    f_out.flush()
                    log_success(f"第{batch_idx+1}批处理完成")
                    break
                except Exception as e:
                    if retry == max_retries-1:
                        log_error(f"第{batch_idx+1}批处理失败：{str(e)}")
                        f_out.write(f"\n【第{batch_idx+1}批：{', '.join(batch_files)}】\n处理失败：{str(e)}\n---\n")
                    time.sleep(2)
    
    log_success(f"所有任务完成，结果保存至：{RESULT_FILE}")

# ===================== 主程序 =====================
if __name__ == "__main__":
    try:
        log_info("程序启动")
        config = load_config()
        process_batch(config)
    except Exception as e:
        log_error(f"程序异常：{str(e)}")
    
    input("\n按任意键退出...")