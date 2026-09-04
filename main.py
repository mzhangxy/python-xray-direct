import os
import shutil
import subprocess
import http.server
import socketserver
import threading
import requests
from flask import Flask
import json
import time
import base64

app = Flask(__name__)

# Set environment variables
FILE_PATH = os.environ.get('FILE_PATH', './temp')
PROJECT_URL = os.environ.get('URL', '') # 填写项目分配的url可实现自动访问，留空即不启用该功能
INTERVAL_SECONDS = int(os.environ.get("TIME", 120))                   # 访问间隔时间，默认120s，单位：秒
UUID = os.environ.get('UUID', '0ac02acc-698c-42f9-aa1d-5fe356bb6f4d')
NEZHA_SERVER = os.environ.get('NEZHA_SERVER', '')        # 哪吒3个变量不全不运行
NEZHA_PORT = os.environ.get('NEZHA_PORT', '')                  # 哪吒端口为443时开启tls
NEZHA_KEY = os.environ.get('NEZHA_KEY', '')
DOMAIN = os.environ.get('DOMAIN', 'linkbot-tkpql.puratya.com')                 # 分配的域名或反代的域名，不带前缀
NAME = os.environ.get('NAME', 'Purayta')

# 核心修改：分离主端口与回落端口
MAIN_PORT = int(os.environ.get('PORT', 3000))        # 平台分配的主外部监听端口 (供 Xray 监听)
FALLBACK_PORT = 3001                                 # Python 网页使用的内部回落端口

# Create directory if it doesn't exist
if not os.path.exists(FILE_PATH):
    os.makedirs(FILE_PATH)
    print(f"{FILE_PATH} has been created")
else:
    print(f"{FILE_PATH} already exists")

# Clean old files
paths_to_delete = ['list.txt', 'sub.txt', 'swith', 'web']
for file in paths_to_delete:
    file_path = os.path.join(FILE_PATH, file)
    try:
        os.unlink(file_path)
        print(f"{file_path} has been deleted")
    except Exception as e:
        print(f"Skip Delete {file_path}")

# http server
class MyHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            try:
                # 尝试读取当前目录下的 index.html 文件
                with open('index.html', 'rb') as file:
                    content = file.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                # 如果没有找到 index.html，自动回落显示纯文本
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'Hello, world!')
        elif self.path == '/sub':
            try:
                with open(os.path.join(FILE_PATH, 'sub.txt'), 'rb') as file:
                    content = file.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Error reading file')
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')

# 绑定回落端口
httpd = socketserver.TCPServer(('', FALLBACK_PORT), MyHandler)
server_thread = threading.Thread(target=httpd.serve_forever)
server_thread.daemon = True
server_thread.start()

# Generate xr-ay config file
def generate_config():
    # 动态解析平台生成的 proxychains 配置，用于 Xray 出站
    proxy_address, proxy_port = "10.201.0.1", 40001
    try:
        if os.path.exists('.pc.conf'):
            with open('.pc.conf', 'r') as f:
                for line in f:
                    if line.startswith('socks5'):
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            proxy_address, proxy_port = parts[1], int(parts[2])
    except:
        pass

    config = {
        "log": {"access": "/dev/null", "error": "/dev/null", "loglevel": "none"},
        "inbounds": [
            {
                "port": MAIN_PORT,
                "listen": "0.0.0.0",
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": UUID, "flow": "xtls-rprx-vision"}],
                    "decryption": "none",
                    "fallbacks": [
                        {"dest": FALLBACK_PORT},
                        {"path": "/vless", "dest": 3002}
                    ]
                },
                "streamSettings": {"network": "tcp"}
            },
            {
                "port": 3002,
                "listen": "127.0.0.1",
                "protocol": "vless",
                "settings": {"clients": [{"id": UUID, "level": 0}], "decryption": "none"},
                "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/vless"}},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "metadataOnly": False}
            }
        ],
        "dns": {"servers": ["https+local://8.8.8.8/dns-query"]},
        "outbounds": [
            {
                # 强制 Xray 走平台的内网 Socks5 代理出站，突破防火墙封锁
                "protocol": "socks",
                "tag": "platform-proxy",
                "settings": {
                    "servers": [{"address": proxy_address, "port": proxy_port}]
                }
            },
            {"protocol": "freedom"}
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": []
        }
    }

    with open(os.path.join(FILE_PATH, 'config.json'), 'w', encoding='utf-8') as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2)

generate_config()

# Determine system architecture
def get_system_architecture():
    arch = os.uname().machine
    if 'arm' in arch or 'aarch64' in arch or 'arm64' in arch:
        return 'arm'
    else:
        return 'amd'

# Download file
def download_file(file_name, file_url):
    file_path = os.path.join(FILE_PATH, file_name)
    with requests.get(file_url, stream=True) as response, open(file_path, 'wb') as file:
        shutil.copyfileobj(response.raw, file)

# Download and run files
def download_files_and_run():
    architecture = get_system_architecture()
    files_to_download = get_files_for_architecture(architecture)

    if not files_to_download:
        print("Can't find a file for the current architecture")
        return

    for file_info in files_to_download:
        try:
            download_file(file_info['file_name'], file_info['file_url'])
            print(f"Downloaded {file_info['file_name']} successfully")
        except Exception as e:
            print(f"Download {file_info['file_name']} failed: {e}")

    # Authorize and run
    files_to_authorize = ['./swith', './web']
    authorize_files(files_to_authorize)

    # Run ne-zha
    NEZHA_TLS = ''
    if NEZHA_SERVER and NEZHA_PORT and NEZHA_KEY:
        NEZHA_TLS = '--tls' if NEZHA_PORT == '443' else ''
        command = f"nohup {FILE_PATH}/swith -s {NEZHA_SERVER}:{NEZHA_PORT} -p {NEZHA_KEY} {NEZHA_TLS} >/dev/null 2>&1 &"
        try:
            subprocess.run(command, shell=True, check=True)
            print('swith is running')
            subprocess.run('sleep 1', shell=True)  # Wait for 1 second
        except subprocess.CalledProcessError as e:
            print(f'swith running error: {e}')
    else:
        print('NEZHA variable is empty, skip running')

    # Run xr-ay
    command1 = f"nohup {FILE_PATH}/web -c {FILE_PATH}/config.json >/dev/null 2>&1 &"
    try:
        subprocess.run(command1, shell=True, check=True)
        print('web is running')
        subprocess.run('sleep 1', shell=True)  # Wait for 1 second
    except subprocess.CalledProcessError as e:
        print(f'web running error: {e}')

    subprocess.run('sleep 3', shell=True)  # Wait for 3 seconds

# Return file information based on system architecture (按需下载探针)
def get_files_for_architecture(architecture):
    files = []
    if architecture == 'arm':
        files.append({'file_name': 'web', 'file_url': 'https://arm64.oooen.com/web'})
        if NEZHA_SERVER and NEZHA_KEY:
            files.append({'file_name': 'swith', 'file_url': 'https://arm64.oooen.com/v1'})
    elif architecture == 'amd':
        files.append({'file_name': 'web', 'file_url': 'https://amd64.oooen.com/web'})
        if NEZHA_SERVER and NEZHA_KEY:
            files.append({'file_name': 'swith', 'file_url': 'https://amd64.oooen.com/v1'})
    return files

# Authorize files
def authorize_files(file_paths):
    new_permissions = 0o775
    for relative_file_path in file_paths:
        absolute_file_path = os.path.join(FILE_PATH, relative_file_path)
        if os.path.exists(absolute_file_path):
            try:
                os.chmod(absolute_file_path, new_permissions)
                print(f"Empowerment success for {absolute_file_path}: {oct(new_permissions)}")
            except Exception as e:
                print(f"Empowerment failed for {absolute_file_path}: {e}")

# 获取服务器地区与 ISP 信息
def get_meta_info() -> str:
    try:
        response = requests.get('https://api.ip.sb/geoip', timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get('country_code') and data.get('isp'):
                return f"{data['country_code']}-{data['isp']}".replace(' ', '_')
    except:
        pass
    
    try:
        response = requests.get('http://ip-api.com/json', timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success' and data.get('countryCode') and data.get('org'):
                return f"{data['countryCode']}-{data['org']}".replace(' ', '_')
    except:
        pass
    
    return 'Unknown'

# Generate list and sub info
def generate_links():
    ISP = get_meta_info()
    time.sleep(1)
 
    list_txt = f"""
vless://{UUID}@{DOMAIN}:443?encryption=none&security=tls&sni={DOMAIN}&type=ws&host={DOMAIN}&path=%2Fvless%3Fed%3D2048#{NAME}-{ISP}
    """
    
    with open(os.path.join(FILE_PATH, 'list.txt'), 'w', encoding='utf-8') as list_file:
        list_file.write(list_txt.strip())

    sub_txt = base64.b64encode(list_txt.strip().encode('utf-8')).decode('utf-8')
    with open(os.path.join(FILE_PATH, 'sub.txt'), 'w', encoding='utf-8') as sub_file:
        sub_file.write(sub_txt)
        
    try:
        with open(os.path.join(FILE_PATH, 'sub.txt'), 'rb') as file:
            sub_content = file.read()
        print(f"\n{sub_content.decode('utf-8')}")
    except FileNotFoundError:
        print(f"sub.txt not found")
    
    print(f'{FILE_PATH}/sub.txt saved successfully')
    time.sleep(2)

    # 修复：移除 config.json 的删除逻辑，防止节点崩溃
    files_to_delete = ['list.txt']
    for file_to_delete in files_to_delete:
        file_path_to_delete = os.path.join(FILE_PATH, file_to_delete)
        try:
            os.remove(file_path_to_delete)
            print(f"{file_path_to_delete} has been deleted")
        except Exception as e:
            print(f"Error deleting {file_path_to_delete}: {e}")

    print('\033c', end='')
    print('App is running')
    print('Thank you for using this script, enjoy!')
         
# Run the callback
def start_server():
    download_files_and_run()
    generate_links()
start_server()

# auto visit project page
has_logged_empty_message = False

def visit_project_page():
    try:
        if not PROJECT_URL or not INTERVAL_SECONDS:
            global has_logged_empty_message
            if not has_logged_empty_message:
                print("URL or TIME variable is empty, Skipping visit web")
                has_logged_empty_message = True
            return

        response = requests.get(PROJECT_URL)
        response.raise_for_status() 

        print("Page visited successfully")
    except requests.exceptions.RequestException as error:
        print(f"Error visiting project page: {error}")

if __name__ == "__main__":
    while True:
        visit_project_page()
        time.sleep(INTERVAL_SECONDS)
