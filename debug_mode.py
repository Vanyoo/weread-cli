#!/usr/bin/env python3
"""
诊断微信读书阅读器模式切换和双栏渲染问题。
通过 CDP 直接检查页面状态、localStorage、Canvas 数据。
"""
import json, sys, os, time

sys.path.insert(0, os.path.expanduser("~/Desktop/weread-cli"))

from weread_cli.browser import CDPBrowserManager

# 加载 cookie
cookie_file = os.path.expanduser("~/.weread_cookies.json")
with open(cookie_file) as f:
    cookies = json.load(f)
if isinstance(cookies, list):
    cookies = {c.get("name", ""): c.get("value", "") for c in cookies if c.get("name")}

browser = CDPBrowserManager(cookies, mode="headed")
if not browser.start():
    print("❌ 浏览器启动失败")
    sys.exit(1)

try:
    # 导航到微信读书阅读器（用默认书 hash）
    # 先打开书架获取第一本书
    print("📚 打开书架...")
    browser._page.goto("https://weread.qq.com/web/shelf", wait_until="networkidle", timeout=15000)
    browser._page.wait_for_timeout(2000)
    
    book_hash = browser._page.evaluate("""() => {
        const links = document.querySelectorAll('a[href*="/web/reader/"]');
        for (const a of links) {
            const href = a.getAttribute('href');
            if (href && href.includes('reader') && !href.includes('shelf')) {
                return href.split('/').pop().split('k')[0];
            }
        }
        return null;
    }""")
    
    if not book_hash:
        print("❌ 无法获取书籍")
        sys.exit(1)
    
    print(f"📖 书籍 hash: {book_hash}")
    
    # 打开书籍
    url = f"https://weread.qq.com/web/reader/{book_hash}"
    browser._page.goto(url, wait_until="domcontentloaded", timeout=15000)
    browser._page.wait_for_timeout(3000)
    
    # 点击开始阅读
    start_btn = browser._find_text('开始阅读')
    if start_btn:
        start_btn.click(force=True)
        browser._page.wait_for_timeout(3000)
        time.sleep(2)
    
    print("\n" + "="*60)
    print("诊断信息")
    print("="*60)
    
    # 全面诊断
    info = browser._page.evaluate("""() => {
        const info = {};
        
        // 1. localStorage 完整列表
        info.localStorage = {};
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            try {
                const val = localStorage.getItem(key);
                if (val.length > 300) {
                    info.localStorage[key] = val.substring(0, 300) + '...[truncated]';
                } else {
                    info.localStorage[key] = val;
                }
            } catch(e) {
                info.localStorage[key] = '[error]';
            }
        }
        
        // 2. 页面尺寸
        info.viewport = {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            scrollWidth: document.documentElement.scrollWidth,
            scrollHeight: document.documentElement.scrollHeight,
            bodyScrollHeight: document.body.scrollHeight,
        };
        
        // 3. Canvas 元素
        const canvases = document.querySelectorAll('canvas');
        info.canvases = [];
        for (const c of canvases) {
            info.canvases.push({
                width: c.width,
                height: c.height,
                styleWidth: c.style.width,
                styleHeight: c.style.height,
                rect: c.getBoundingClientRect(),
                class: c.className,
                id: c.id,
            });
        }
        
        // 4. 阅读器相关 DOM
        info.readerContent = document.querySelector('.readerContent') ? 
            document.querySelector('.readerContent').className : 'not found';
        info.readerChapterContent = document.querySelector('.readerChapterContent') ?
            document.querySelector('.readerChapterContent').className : 'not found';
        info.readerFooter = document.querySelector('.readerFooter') ? 'found' : 'not found';
        
        // 5. 所有包含 'mode' 或 'page' 的 class
        const allClasses = new Set();
        document.querySelectorAll('[class]').forEach(el => {
            el.className.split(' ').forEach(c => {
                if (c.match(/mode|page|scroll|flip|reader|content/i)) {
                    allClasses.add(c);
                }
            });
        });
        info.relevantClasses = [...allClasses].slice(0, 30);
        
        // 6. body 文本前 300 字符
        info.bodyTextSnippet = document.body.innerText.substring(0, 300);
        
        return info;
    }""")
    
    print(json.dumps(info, indent=2, ensure_ascii=False))
    
    # 7. Canvas x 坐标分析
    captured = browser._page.evaluate("() => window.__CANVAS_TEXT__ || []")
    if captured:
        xs = [item.get("x", 0) for item in captured if isinstance(item.get("x"), (int, float))]
        ys = [item.get("y", 0) for item in captured if isinstance(item.get("y"), (int, float))]
        if xs:
            print(f"\n--- Canvas 坐标分析 ---")
            print(f"x 范围: {min(xs):.0f} ~ {max(xs):.0f}")
            print(f"y 范围: {min(ys):.0f} ~ {max(ys):.0f}")
            print(f"总字符数: {len(xs)}")
            
            # 直方图分析
            sorted_xs = sorted(set(round(x, 0) for x in xs))
            gaps = [(sorted_xs[i-1], sorted_xs[i], sorted_xs[i] - sorted_xs[i-1]) 
                    for i in range(1, len(sorted_xs)) if sorted_xs[i] - sorted_xs[i-1] > 20]
            if gaps:
                print(f"\n大空隙 (>20px):")
                for l, r, g in sorted(gaps, key=lambda x: -x[2])[:5]:
                    print(f"  {l:.0f} → {r:.0f} (gap={g:.0f}px)")
    
    print("\n--- 请手动点击 Aa 按钮切换模式，然后告诉我 ---")
    input("按 Enter 继续...")
    
    # 切换后再检查
    info2 = browser._page.evaluate("""() => {
        return {
            scrollHeight: document.documentElement.scrollHeight,
            innerHeight: window.innerHeight,
            scrollable: document.documentElement.scrollHeight > window.innerHeight + 10,
            readerConfig: (() => {
                try { return JSON.parse(localStorage.getItem('readerConfig')); } 
                catch(e) { return null; }
            })(),
        };
    }""")
    print(f"\n切换后状态: {json.dumps(info2, indent=2, ensure_ascii=False)}")

finally:
    browser.close()
