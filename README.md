# WeRead CLI - 微信读书命令行工具

在终端中阅读微信读书，通过 CDP 浏览器实时渲染内容并提取文本。

## ✨ 功能特性

- 🔐 **登录管理** — 终端二维码登录 / 从浏览器导入 Cookie
- 🔄 **Cookie 自动刷新** — 每次登录检查自动刷新 `wr_skey`，主菜单支持手动刷新
- 📚 **书架管理** — 查看完整书架，在线搜索
- 📖 **CDP 阅读模式** — 通过 Chrome DevTools Protocol 连接浏览器，实时渲染提取文本
- ⚡ **Loading 动画** — 所有 API 调用显示终端旋转动画
- 💾 **阅读进度持久化** — 自动保存/恢复上次阅读进度

## 📦 安装

```bash
cd ~/Desktop/weread-cli
pip install -e .
python -m playwright install chromium  # 仅二维码登录需要
```

## 🚀 快速开始

```bash
# 登录（终端显示二维码）
weread-cli login

# 进入交互模式
weread-cli
```

## 📋 命令

| 命令 | 说明 |
|------|------|
| `weread-cli` | 进入交互模式 |
| `weread-cli login` | 登录 |
| `weread-cli logout` | 退出登录 |

## 🎮 交互菜单

```
主菜单：
  1 - 我的书架（输入序号直接阅读）
  2 - 搜索书籍
  3 - 继续阅读
  4 - 刷新 Cookie        ← 手动刷新登录凭证
  5 - 登录
  6 - 退出登录
  0 - 退出
```

### 阅读模式快捷键

| 按键 | 功能 |
|------|------|
| `n` | 下一章 |
| `p` | 上一章 |
| `f` | 下一页 |
| `b` | 上一页 |
| `r` | 刷新页面 |
| `c` | 章节列表 |
| `q` | 返回主菜单 |

## 🔄 Cookie 刷新机制

| Cookie | 说明 | 有效期 |
|--------|------|--------|
| `wr_skey` | 短期 Session Key | ~1.5 小时 |
| `wr_rt` | 长期 Refresh Token | ~1 年 |

**自动刷新**：每次检查登录状态时，如果 Cookie 过期会自动调用 `/web/login/renewal` 刷新 `wr_skey`。

**手动刷新**：主菜单选项 `4 - 刷新 Cookie`，或代码调用 `api.refresh_cookies()`。

只要 `wr_rt` 有效，无需重新登录。

## 📁 项目结构

```
weread-cli/
├── weread_cli/
│   ├── api.py          # WeRead API 封装（含 Cookie 管理/刷新）
│   ├── browser.py      # CDP 浏览器管理器
│   ├── canvas_extractor.py  # Canvas 文本提取
│   ├── loading.py      # Terminal Loading 动画
│   ├── login.py        # 二维码登录管理
│   ├── main.py         # CLI 主程序
│   └── models.py       # 数据模型
├── pyproject.toml      # 项目配置
├── INSTALL.md          # 安装说明
├── QUICK_START.md      # 快速入门
└── README.md
```

### 核心模块

**`api.py` — WeReadAPI 类**

| 方法 | 说明 |
|------|------|
| `check_login()` | 检查登录状态（GET /web/user） |
| `get_bookshelf()` | 获取书架（GET /web/shelf/sync） |
| `get_book_progress()` | 获取单本书阅读进度 |
| `get_chapters()` | 获取章节列表（POST /web/book/chapterInfos） |
| `get_chapter_content()` | 通过 CDP 浏览器获取章节内容 |
| `refresh_cookies()` | 刷新 Cookie（POST /web/login/renewal） |
| `search_books()` | 搜索书架 |
| `import_from_browser()` | 从浏览器导入 Cookie |
| `next_page()` / `prev_page()` | 翻页 |
| `next_chapter()` / `prev_chapter()` | 上下章 |
| `close()` | 关闭浏览器 |

**`browser.py` — CDPBrowserManager 类**

通过 Chrome DevTools Protocol 连接运行中的 Chrome/Chromium 浏览器，实现：
- 导航到书籍页面
- 跳转章节
- 翻页（上一页/下一页）
- 通过 Canvas 钩子提取渲染后的文本

**`canvas_extractor.py` — 文本提取**

从微信读书 Canvas 渲染的页面中重建文本内容。

**`loading.py` — LoadingSpinner 类**

终端 Loading 动画，4 帧循环：`Loading   ` → `Loading.  ` → `Loading.. ` → `Loading...`

## 🛠️ 故障排除

### Cookie 失效

```bash
weread-cli  # 选 4 - 刷新 Cookie

# 或完全重新登录
rm ~/.weread_cookies.json
weread-cli login
```

### Playwright 问题

```bash
python -m playwright install chromium
```

### 找不到命令

```bash
cd ~/Desktop/weread-cli
python -m weread_cli
```

## 📄 License

MIT
