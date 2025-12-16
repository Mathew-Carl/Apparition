# 幻影显形 - WPS 自动打卡服务

> Apparition - 多用户 WPS 自动打卡系统

一个支持多用户的 WPS 自动打卡服务，用户通过扫码授权登录，系统自动定时执行打卡任务。

## 功能特性

- **扫码登录**：用户扫描二维码授权，自动获取登录凭证
- **多用户支持**：支持多个用户同时使用，互不干扰
- **定时打卡**：支持配置多个打卡时间，自动执行
- **自定义时间**：用户可设置个人专属打卡时间
- **打卡通知**：支持 Server酱 推送打卡结果通知
- **管理后台**：管理员可管理用户、配置打卡时间
- **打卡记录**：完整的打卡历史记录查询

## 技术栈

- **后端**：Python + FastAPI
- **浏览器自动化**：Playwright
- **数据库**：SQLite
- **定时任务**：APScheduler

## 快速开始

### 环境要求

- Python 3.9+
- Ubuntu / Windows / macOS

### 本地安装

```bash
# 克隆项目
git clone https://github.com/yourusername/Apparition.git
cd Apparition

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
playwright install-deps chromium  # Linux 需要

# 启动服务
python run.py
```

### Ubuntu 服务器部署

```bash
# 安装 Python
sudo apt update
sudo apt install python3 python3-venv python3-pip -y

# 进入项目目录
cd /home/ubuntu/Apparition

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装浏览器
playwright install chromium
playwright install-deps chromium

# 测试运行
python run.py
```

### 配置开机自启动（systemd）

创建服务文件：

```bash
sudo nano /etc/systemd/system/apparition.service
```

写入以下内容（修改路径和用户名）：

```ini
[Unit]
Description=Apparition WPS Auto Checkin
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Apparition
Environment="PATH=/home/ubuntu/Apparition/venv/bin"
ExecStart=/home/ubuntu/Apparition/venv/bin/python run.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable apparition
sudo systemctl start apparition

# 查看状态
sudo systemctl status apparition

# 查看日志
sudo journalctl -u apparition -f
```

## 使用说明

### 访问地址

- **用户页面**：`http://服务器IP:8080`
- **管理后台**：`http://服务器IP:8080/admin`
- **API 文档**：`http://服务器IP:8080/docs`

### 管理员账户

- 用户名：`admin`
- 密码：`123456`

> 建议首次使用后修改密码（在 `app.py` 中修改 `ADMIN_PASSWORD`）

### 用户使用流程

1. 访问首页，点击「获取登录二维码」
2. 使用微信扫描二维码，完成 WPS 授权
3. 登录成功后，配置个人信息：
   - **打卡填写内容**：打卡时填写的文本（如 `1000张三`）
   - **经纬度**：打卡位置坐标
   - **自定义打卡时间**：可选，留空使用系统默认时间
   - **Server酱 SendKey**：可选，用于接收打卡通知
4. 保存设置，等待系统自动打卡

### 管理员操作

1. 访问 `/admin`，登录管理后台
2. **打卡时间配置**：添加/删除/启用/禁用打卡时间
3. **用户管理**：查看用户、手动触发打卡、启用/禁用用户

## 配置说明

### 修改端口

编辑 `run.py`，修改 `port` 参数：

```python
uvicorn.run(
    "app:app",
    host="0.0.0.0",
    port=8080,  # 修改端口
    ...
)
```

### 修改打卡目标 URL

编辑 `checkin.py`，修改 `TARGET_URL`：

```python
TARGET_URL = "https://f.kdocs.cn/g/xxxxx/"  # 你的打卡链接
```

### 修改管理员密码

编辑 `app.py`，修改以下变量：

```python
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "your_new_password"
```

## Server酱通知配置

1. 访问 [sct.ftqq.com](https://sct.ftqq.com/) 注册/登录
2. 获取 SendKey：[sct.ftqq.com/sendkey](https://sct.ftqq.com/sendkey)
3. 在用户页面填入 SendKey 并保存
4. 打卡成功/失败时会收到微信通知

## 项目结构

```
Apparition/
├── app.py              # FastAPI 主应用
├── run.py              # 启动入口
├── database.py         # 数据库模块
├── wps_auth.py         # WPS 认证模块
├── checkin.py          # 打卡执行模块
├── scheduler.py        # 定时任务模块
├── requirements.txt    # 依赖列表
├── templates/
│   ├── index.html      # 用户页面
│   └── admin.html      # 管理后台
└── data/
    └── apparition.db   # SQLite 数据库（自动创建）
```

## 常见问题

### Q: 扫码后显示「登录失败」？

WPS 登录流程较复杂，可能是网络问题或 WPS 接口变化。查看服务日志排查：

```bash
sudo journalctl -u apparition -f
```

### Q: 打卡失败「Timeout」？

打卡页面元素可能有变化，需要检查 `checkin.py` 中的元素选择器是否正确。

### Q: 如何查看打卡日志？

- 用户可在个人页面查看打卡记录
- 管理员可在后台查看所有用户状态
- 服务端日志：`sudo journalctl -u apparition -f`

## 免责声明

本项目仅供学习交流使用，请勿用于违反相关服务条款的行为。使用本项目所造成的任何后果由使用者自行承担。

## License

MIT License
