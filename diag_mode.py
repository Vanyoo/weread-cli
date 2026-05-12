#!/usr/bin/env python3
"""
诊断 WeRead 阅读器的模式切换机制和双栏 Canvas 渲染。
通过 CDP 直接检查页面状态。
"""
import json, sys, os

sys.path.insert(0, os.path.expanduser("~/Desktop/weread-cli"))

from weread_cli.browser import CDPBrowserManager

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
    # 打开书架
    print("📚 打开书架...")
    browser._page.goto("https://weread.qq.com/web/shelf", wait_until="domcontentloaded", timeout=15000)
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
    browser.navigate_to_book(book_hash)
    browser._page.wait_for_timeout(2000)
    
    # 点击开始阅读
    start_btn = browser._find_text('开始阅读')
    if start_btn:
        start_btn.click(force=True)
        browser._page.wait_for_timeout(3000)
    
    print("\n" + "="*60)
    print("诊断：WeRead 阅读器页面状态")
    print("="*60)
    
    # 全面检查页面
    info = browser._page.evaluate("""() => {
        const info = {};
        
        // 1. 所有 localStorage key（完整）
        info.allLocalStorageKeys = [];
        for (let i = 0; i < localStorage.length; i++) {
            info.allLocalStorageKeys.push(localStorage.key(i));
        }
        
        // 2. readerConfig 完整值
        try {
            info.readerConfig = JSON.parse(localStorage.getItem('readerConfig'));
        } catch(e) { info.readerConfig = null; }
        
        // 3. 检查是否有其他模式相关的存储
        const modeKeys = {};
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (k.includes('reader') || k.includes('mode') || k.includes('page') || 
                k.includes('config') || k.includes('style') || k.includes('font') ||
                k.includes('pref') || k.includes('theme')) {
                try { modeKeys[k] = JSON.parse(localStorage.getItem(k)); } 
                catch(e) { modeKeys[k] = localStorage.getItem(k); }
            }
        }
        info.modeRelatedKeys = modeKeys;
        
        // 4. 页面尺寸和可滚动性
        info.pageMetrics = {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            scrollWidth: document.documentElement.scrollWidth,
            scrollHeight: document.documentElement.scrollHeight,
            bodyScrollHeight: document.body.scrollHeight,
            scrollable: document.documentElement.scrollHeight > window.innerHeight + 10,
        };
        
        // 5. Canvas 元素信息
        const canvases = document.querySelectorAll('canvas');
        info.canvasElements = [];
        for (const c of canvases) {
            info.canvasElements.push({
                width: c.width,
                height: c.height,
                styleWidth: c.style.width,
                styleHeight: c.style.height,
                rect: {
                    x: c.getBoundingClientRect().x,
                    y: c.getBoundingClientRect().y,
                    w: c.getBoundingClientRect().width,
                    h: c.getBoundingClientRect().height,
                },
                className: c.className,
                id: c.id,
                parentClass: c.parentElement ? c.parentElement.className : null,
            });
        }
        
        // 6. 阅读器容器信息
        const readerContainers = document.querySelectorAll('.readerContent, .readerChapterContent, .readerContainer');
        info.readerContainers = [];
        for (const el of readerContainers) {
            info.readerContainers.push({
                className: el.className,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                scrollTop: el.scrollTop,
                style: {
                    overflow: getComputedStyle(el).overflow,
                    overflowY: getComputedStyle(el).overflowY,
                    display: getComputedStyle(el).display,
                    width: getComputedStyle(el).width,
                }
            });
        }
        
        // 7. body text 前 200 字符
        info.bodyTextSnippet = document.body.innerText.substring(0, 200);
        
        // 8. 全局 window 对象中与 reader 相关的属性
        info.windowReaderKeys = Object.keys(window).filter(k => 
            k.toLowerCase().includes('reader') || 
            k.toLowerCase().includes('weread') ||
            k.toLowerCase().includes('wr')
        ).slice(0, 20);
        
        // 9. 所有包含特定 class 的元素
        const modeClasses = new Set();
        document.querySelectorAll('[class*="mode"], [class*="page"], [class*="flip"], [class*="scroll"], [class*="readerConfig"]')
            .forEach(el => {
                el.className.split(' ').forEach(c => {
                    if (c.match(/mode|page|flip|scroll|config/i)) {
                        modeClasses.add(c);
                    }
                });
            });
        info.relevantClasses = [...modeClasses].slice(0, 30);
        
        return info;
    }""")
    
    print(json.dumps(info, indent=2, ensure_ascii=False))
    
    # Canvas 坐标分析
    captured = browser._page.evaluate("() => window.__CANVAS_TEXT__ || []")
    if captured:
        xs = [item.get("x", 0) for item in captured if isinstance(item.get("x"), (int, float))]
        ys = [item.get("y", 0) for item in captured if isinstance(item.get("y"), (int, float))]
        if xs:
            print(f"\n--- Canvas 坐标分析 ---")
            print(f"x 范围: {min(xs):.0f} ~ {max(xs):.0f} (跨度 {max(xs)-min(xs):.0f}px)")
            print(f"y 范围: {min(ys):.0f} ~ {max(ys):.0f} (跨度 {max(ys)-min(ys):.0f}px)")
            print(f"总字符数: {len(xs)}")
            
            # 40 bin 直方图
            num_bins = 40
            bin_w = (max(xs) - min(xs)) / num_bins
            bins = [0] * num_bins
            for x in xs:
                idx = min(int((x - min(xs)) / bin_w), num_bins - 1)
                bins[idx] += 1
            print(f"\n直方图 ({num_bins} bins):")
            max_bin = max(bins)
            for i in range(num_bins):
                bar = "█" * (bins[i] * 30 // max(max_bin, 1))
                print(f"  {min(xs) + i*bin_w:6.0f}-{min(xs) + (i+1)*bin_w:6.0f} {bar} ({bins[i]})")
    
    # 诊断模式切换
    print(f"\n--- 模式切换诊断 ---")
    print(f"当前 readerConfig: {info.get('readerConfig')}")
    print(f"下一页按钮: {browser._find_text('下一页') is not None}")
    print(f"上一页按钮: {browser._find_text('上一页') is not None}")
    
    # 尝试切换模式
    print(f"\n--- 尝试模式切换 ---")
    result = browser.toggle_reading_mode()
    print(f"切换结果: {result}")

finally:
    browser.close()
