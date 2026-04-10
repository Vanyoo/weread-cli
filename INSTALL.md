# WeRead CLI 安装指南

## 快速安装

### 1. 安装依赖

```bash
cd ~/Developer/weread-cli
python3 -m pip install -e .
```

### 2. 添加命令到 PATH

由于命令安装在 `~/Library/Python/3.9/bin/`，你需要将其添加到 PATH：

#### 方法 1：临时添加（当前终端会话）
```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

#### 方法 2：永久添加（推荐）
将以下行添加到你的 shell 配置文件（`~/.zshrc` 或 `~/.bash_profile`）：

```bash
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### 3. 安装 Playwright

```bash
python3 -m playwright install chromium
```

### 4. 运行

```bash
weread-cli
```

或者使用完整路径：
```bash
~/Library/Python/3.9/bin/weread-cli
```

## 首次使用

1. 运行 `weread-cli`
2. 选择"浏览器登录"（选项 1）
3. 在打开的浏览器中登录微信读书
4. 登录后程序会自动保存 Cookie
5. 开始使用！

## 快捷方式

### 创建别名

在 `~/.zshrc` 或 `~/.bash_profile` 中添加：

```bash
alias weread='~/Library/Python/3.9/bin/weread-cli'
```

然后：
```bash
source ~/.zshrc
weread  # 直接运行
```

## 故障排除

### 问题：找不到 weread-cli 命令

**解决方案：**
```bash
# 使用完整路径
~/Library/Python/3.9/bin/weread-cli

# 或添加 PATH
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

### 问题：Python 版本过低

需要 Python 3.9+，检查版本：
```bash
python3 --version
```

### 问题：Playwright 未安装

```bash
python3 -m playwright install chromium
```

### 问题：Cookie 导入失败

使用浏览器登录方式（选项 1），这是最可靠的方式。
