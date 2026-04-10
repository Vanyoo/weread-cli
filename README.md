# WeRead CLI

命令行微信读书工具 - 在终端中阅读微信读书

## ✨ 功能特性

- 🔐 **登录/退出**
  - 浏览器登录（最可靠）
  - 从 Chrome/Firefox 导入 Cookie
  - Cookie 自动保存
  - 登录状态检测

- 📚 **书架管理**
  - 查看个人书架
  - 显示阅读进度
  - 支持搜索（按书名/作者）

- 📖 **阅读功能**
  - 章节导航
  - 上一章/下一章切换
  - VIP 章节标识
  - 使用 Playwright 获取内容

## 📦 安装

### 1. 安装依赖

```bash
cd ~/Developer/weread-cli
python3 -m pip install -e .
```

### 2. 安装 Playwright

```bash
python3 -m playwright install chromium
```

### 3. 添加 PATH（可选）

```bash
# 添加到 ~/.zshrc
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## 🚀 使用

### 启动程序

```bash
# 方式 1：使用完整路径
~/Library/Python/3.9/bin/weread-cli

# 方式 2：如果已添加 PATH
weread-cli

# 方式 3：使用启动脚本
./run.sh
```

### 首次登录

1. 运行 `weread-cli`
2. 选择"浏览器登录"（选项 1）
3. 在打开的浏览器中登录微信读书
4. 登录后程序会自动保存 Cookie

### 主菜单

```
主菜单：
  1 - 我的书架
  2 - 搜索书籍
  3 - 选择书籍阅读
  4 - 登录
  5 - 退出登录
  0 - 退出
```

### 阅读界面快捷键

- `n` - 下一章
- `p` - 上一章
- `c` - 章节列表
- `q` - 返回

## 💡 使用技巧

### 快速登录

```bash
# 使用独立登录脚本
python3 login.py
```

### 搜索书籍

在主菜单选择搜索，输入关键词即可查找书架中的书籍。

### 阅读进度

程序会显示每本书的阅读进度百分比。

## ⚠️ 注意事项

- **Cookie 有效期**：Cookie 会过期，过期后需要重新登录
- **VIP 内容**：部分章节需要微信读书会员才能阅读
- **网络要求**：需要稳定的网络连接
- **首次使用**：需要安装 Playwright Chromium（约 100MB）

## 🛠️ 故障排除

### 找不到命令

```bash
# 使用完整路径
~/Library/Python/3.9/bin/weread-cli
```

### Cookie 过期

```bash
# 删除旧 Cookie
rm ~/.weread_cookies.json

# 重新登录
weread-cli
```

### Playwright 问题

```bash
# 重新安装 Chromium
python3 -m playwright install chromium
```

## 📁 项目结构

```
weread-cli/
├── weread_cli/          # 主程序包
│   ├── api.py          # WeRead API
│   ├── login.py        # 登录管理
│   ├── main.py         # CLI 主程序
│   └── models.py       # 数据模型
├── login.py            # 独立登录脚本
├── run.sh              # 快速启动脚本
└── README.md           # 本文档
```

## 📄 License

MIT
