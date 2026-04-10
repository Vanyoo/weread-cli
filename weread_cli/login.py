# -*- coding: utf-8 -*-
"""二维码登录 —— 微信读书 Web 端"""

import json
import os
import time
import re
import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict

import httpx


class QRLogin:
    """微信读书登录管理器"""
    
    def __init__(self, cookie_file: Optional[Path] = None):
        self.cookie_file = cookie_file or Path.home() / ".weread_cookies.json"
    
    def _load_cookies(self) -> Dict[str, str]:
        """从文件加载 Cookie"""
        if self.cookie_file.exists():
            try:
                with open(self.cookie_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def _save_cookies(self, cookies: Dict[str, str]) -> None:
        """保存 Cookie 到文件（原子写入）"""
        import os
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = str(self.cookie_file) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cookies, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(self.cookie_file))
    
    def refresh_skey(self) -> bool:
        """刷新 wr_skey，延长 Cookie 有效期。
        
        原理：调用微信读书 /web/login/renewal 接口，
        从响应 Set-Cookie 头中提取新的 wr_skey。
        """
        cookies = self._load_cookies()
        if not cookies:
            return False
        
        try:
            resp = httpx.post(
                "https://weread.qq.com/web/login/renewal",
                cookies=cookies,
                json={"rq": "/web/book/read", "ql": True},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
                timeout=10,
            )
            
            # 从 Set-Cookie 提取新 wr_skey
            set_cookie = resp.headers.get("set-cookie", "")
            for part in set_cookie.split(";"):
                part = part.strip()
                if part.startswith("wr_skey="):
                    new_skey = part.split("=")[1]
                    cookies["wr_skey"] = new_skey
                    self._save_cookies(cookies)
                    print(f"✅ Cookie 刷新成功，新 wr_skey: {new_skey}")
                    return True
            
            # 响应码正常但没有新 skey，可能 cookie 本身还有效
            if resp.status_code == 200:
                return True
            
            return False
            
        except Exception as e:
            print(f"⚠️ Cookie 刷新失败: {e}")
            return False
    
    def _display_qr_terminal(self, qr_img_data: bytes) -> None:
        """在终端完整渲染二维码，不裁剪不缩放。"""
        from PIL import Image
        from io import BytesIO
        import shutil
        
        img = Image.open(BytesIO(qr_img_data)).convert('L')
        w, h = img.size
        pixels = img.load()
        
        # 终端可用宽度
        term_w = shutil.get_terminal_size().columns - 6
        
        print()
        
        # 逐行完整渲染，不截断
        for y in range(0, h, 2):
            line = "  "  # 左侧缩进
            for x in range(w):
                line += "██" if pixels[x, y] < 128 else "  "
            print(line)
    
    def _open_qr_image(self, qr_img_data: bytes) -> tuple:
        """保存并打开二维码图片，返回路径"""
        qr_path = tempfile.mktemp(suffix='.png', prefix='weread_qr_')
        with open(qr_path, 'wb') as f:
            f.write(qr_img_data)
        
        print(f"\n✅ 二维码图片: {qr_path}")
        
        # macOS: 用 open 打开图片
        subprocess.Popen(['open', '-a', 'Preview', qr_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("✅ 已在预览中打开二维码图片")
        return qr_path, None
    
    def _close_qr_image(self, qr_path: str, proc) -> None:
        """关闭打开的二维码图片"""
        try:
            import subprocess
            # macOS: 关闭 Preview 中打开的指定文件
            escaped_path = qr_path.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.run([
                'osascript', '-e',
                'tell application "Preview"',
                f'  close (every document whose POSIX path is "{escaped_path}")',
                'end tell'
            ], capture_output=True, timeout=3)
        except Exception:
            pass
        # 清理临时文件
        try:
            os.unlink(qr_path)
        except Exception:
            pass
    
    def login_via_qr(self) -> bool:
        """
        终端二维码登录流程：
        1. 访问微信读书首页
        2. 点击登录按钮
        3. 等待 QR 码弹窗出现
        4. 提取二维码，终端打印 + 打开图片
        5. 轮询登录状态，成功后关闭图片
        """
        print("\n" + "=" * 60)
        print("📱 微信读书 - 终端二维码登录")
        print("=" * 60)
        
        qr_path = None
        qr_proc = None
        
        try:
            from playwright.sync_api import sync_playwright
            
            print("\n[1/5] 启动浏览器...")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1200, "height": 900},
                )
                page = context.new_page()
                
                # 访问微信读书首页
                print("[2/5] 访问首页...")
                page.goto("https://weread.qq.com/", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                
                # 点击登录按钮
                print("[3/5] 点击登录...")
                login_btn = (
                    page.query_selector('.wr_header_login') or
                    page.query_selector('.wr_index_page_header_login') or
                    page.query_selector('text="登录"')
                )
                
                if not login_btn:
                    print("❌ 未找到登录按钮")
                    browser.close()
                    return False
                
                login_btn.click()
                page.wait_for_timeout(2000)
                
                # 等待 QR 码弹窗出现
                print("[4/5] 等待二维码加载...")
                page.wait_for_timeout(2000)
                
                has_modal = page.evaluate("""() => {
                    return !!document.querySelector('.wr_login_modal_wrapper');
                }""")
                
                if not has_modal:
                    print("❌ 登录弹窗未出现")
                    browser.close()
                    return False
                
                # 获取二维码
                print("[5/5] 提取二维码...")
                
                qr_data = page.evaluate("""() => {
                    const images = document.querySelectorAll('img');
                    for (const img of images) {
                        const src = img.getAttribute('src') || '';
                        if (src.startsWith('data:image/') && img.width > 100) {
                            return { type: 'base64', data: src };
                        }
                    }
                    return null;
                }""")
                
                if not qr_data:
                    print("❌ 无法获取二维码")
                    browser.close()
                    return False
                
                # 提取 base64 图片数据
                print("\n处理二维码图片...")
                qr_img_data = None
                
                if qr_data['data'].startswith('data:image'):
                    base64_match = re.search(r'data:image/\w+;base64,([^,]+)', qr_data['data'])
                    if base64_match:
                        qr_img_data = base64.b64decode(base64_match.group(1))
                        print(f"✅ 解码成功，图片大小：{len(qr_img_data)} 字节")
                    else:
                        print("❌ 无法解析 base64 图片")
                        browser.close()
                        return False
                else:
                    qr_img_resp = httpx.get(qr_data['data'], timeout=10)
                    if qr_img_resp.status_code == 200:
                        qr_img_data = qr_img_resp.content
                    else:
                        print(f"❌ 下载失败：{qr_img_resp.status_code}")
                        browser.close()
                        return False
                
                # 同时：终端打印 + 打开图片
                print()
                print("  请打开微信 → 扫一扫 → 扫描二维码：")
                print()
                
                # 1. 终端打印
                self._display_qr_terminal(qr_img_data)
                
                # 2. 打开图片
                qr_path, qr_proc = self._open_qr_image(qr_img_data)
                print()
                
                # 轮询登录状态
                print("\n" + "=" * 60)
                print("等待扫码... (最长 120 秒)")
                print("=" * 60)
                
                login_success = self._poll_login_status(page)
                
                if login_success:
                    # 扫码成功后关闭图片
                    if qr_path:
                        self._close_qr_image(qr_path, qr_proc)
                    final_cookies = context.cookies()
                    weread_cookies = {}
                    for c in final_cookies:
                        domain = c.get('domain', '')
                        if 'weread.qq.com' in domain or 'qq.com' in domain or c['name'].startswith('wr_'):
                            weread_cookies[c['name']] = c['value']
                    
                    if not weread_cookies:
                        for c in final_cookies:
                            weread_cookies[c['name']] = c['value']
                    
                    if weread_cookies:
                        self._save_cookies(weread_cookies)
                        print(f"\n✅ 登录成功！已保存 {len(weread_cookies)} 个 Cookie")
                        print(f"   文件：{self.cookie_file}")
                        browser.close()
                        return True
                
                browser.close()
                print("\n❌ 登录失败")
                return False
                
        except Exception as e:
            print(f"\n❌ 登录异常：{e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _poll_login_status(self, page) -> bool:
        """
        轮询登录状态。
        
        登录成功的判定条件：
        1. 登录弹窗消失（.wr_login_modal_wrapper 不存在）
        2. 页面出现用户昵称/头像
        3. URL 跳转到个人页面
        """
        start_time = time.time()
        timeout = 120
        
        print("\n等待扫码确认...")
        
        # 先确认弹窗已出现
        modal_appeared = False
        for _ in range(10):
            has_modal = page.evaluate("""() => {
                return !!document.querySelector('.wr_login_modal_wrapper');
            }""")
            if has_modal:
                modal_appeared = True
                break
            page.wait_for_timeout(500)
        
        if not modal_appeared:
            print("⚠️  登录弹窗未出现，但继续等待...")
        
        # 等待弹窗消失
        while time.time() - start_time < timeout:
            try:
                result = page.evaluate("""() => {
                    // 检查登录弹窗是否存在
                    const modal = document.querySelector('.wr_login_modal_wrapper');
                    const modalExists = !!modal;
                    
                    // 检查 body 是否存在（页面可能在跳转中）
                    const body = document.body;
                    if (!body) return { modalExists, hasUserInfo: false, hasLogout: false, url: '' };
                    
                    // 获取 body 文本判断是否有用户信息
                    const bodyText = body.innerText || '';
                    const hasLogout = bodyText.includes('退出登录') || bodyText.includes('书架');
                    const hasUserInfo = hasLogout;
                    
                    // 检查 URL
                    const url = window.location.href;
                    
                    return {
                        modalExists,
                        hasUserInfo,
                        hasLogout,
                        url
                    };
                }""")
                
                # 弹窗消失 + 页面有用户内容
                if not result['modalExists'] and modal_appeared:
                    # 再等 2 秒确认不是闪烁
                    page.wait_for_timeout(2000)
                    confirm = page.evaluate("""() => {
                        return !!document.querySelector('.wr_login_modal_wrapper');
                    }""")
                    if not confirm:
                        print(f"\n✅ 登录弹窗消失，登录成功！")
                        return True
                
                elapsed = int(time.time() - start_time)
                print(f"  等待扫码中... {elapsed}s", end='\r')
                page.wait_for_timeout(2000)
                
            except Exception as e:
                print(f"\n检测错误：{e}")
                page.wait_for_timeout(2000)
        
        print(f"\n\n⏳ 等待超时（{timeout} 秒）")
        return False
    
    def check_login(self) -> bool:
        """检查是否已登录（仅检查 Cookie 文件存在）"""
        cookies = self._load_cookies()
        return 'wr_vid' in cookies and 'wr_skey' in cookies
    
    def logout(self) -> None:
        """退出登录"""
        if self.cookie_file.exists():
            self.cookie_file.unlink()
            print("✅ 已退出登录")
        else:
            print("⚠️  未找到登录信息")
