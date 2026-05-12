#!/usr/bin/env python3
"""快速诊断 WeRead 阅读器 localStorage 和 DOM 结构"""
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
    # 导航到阅读器
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
    
    url = f"https://weread.qq.com/web/reader/{book_hash}"
    browser._page.goto(url, wait_until="domcontentloaded", timeout=15000)
    browser._page.wait_for_timeout(3000)
    
    # 点击开始阅读
    start_btn = browser._find_text('开始阅读')
    if start_btn:
        start_btn.click(force=True)
        browser._page.wait_for_timeout(3000)
    
    print("=== 阅读器页面诊断 ===\n")
    
    # 1. 所有 localStorage
    ls = browser._page.evaluate("""() => {
        const all = {};
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            try {
                const v = localStorage.getItem(k);
                if (v.length > 200) all[k] = v.substring(0, 200) + '...';
                else all[k] = v;
            } catch(e) { all[k] = '[error]'; }
        }
        return all;
    }""")
    print("--- localStorage (全部) ---")
    for k, v in sorted(ls.items()):
        print(f"  {k}: {v}")
    
    # 2. Canvas x 坐标分布
    captured = browser._page.evaluate("() => window.__CANVAS_TEXT__ || []")
    if captured:
        xs = [item.get("x", 0) for item in captured if isinstance(item.get("x"), (int, float))]
        if xs:
            print(f"\n--- Canvas x 分布 ---")
            print(f"  范围: {min(xs):.0f} ~ {max(xs):.0f}")
            print(f"  总数: {len(xs)}")
            
            # 20 bins
            num_bins = 20
            bin_w = (max(xs) - min(xs)) / num_bins
            bins = [0] * num_bins
            for x in xs:
                idx = min(int((x - min(xs)) / bin_w), num_bins - 1)
                bins[idx] += 1
            print(f"  直方图:")
            for i in range(num_bins):
                bar = "█" * (bins[i] * 30 // max(max(bins), 1))
                print(f"    {min(xs) + i*bin_w:6.0f}-{min(xs) + (i+1)*bin_w:6.0f}: {bar} ({bins[i]})")
    
    # 3. 检查 Aa 按钮和设置面板元素
    print("\n--- UI 元素搜索 ---")
    ui_info = browser._page.evaluate("""() => {
        // 搜索所有可能与设置/模式相关的元素
        const results = [];
        
        // 搜索 class 包含 reader 且包含 mode/settings/config/font 的元素
        document.querySelectorAll('[class]').forEach(el => {
            const classes = el.className.split(' ');
            for (const c of classes) {
                const cl = c.toLowerCase();
                if (cl.includes('mode') || cl.includes('settings') || cl.includes('config') || 
                    cl.includes('page') || cl.includes('flip') || cl.includes('scroll')) {
                    results.push({
                        tag: el.tagName,
                        classes: c,
                        text: (el.textContent || '').substring(0, 50).trim(),
                        visible: el.offsetParent !== null,
                        id: el.id || ''
                    });
                }
            }
        });
        
        // 搜索包含特定文本的元素
        const searchTexts = ['翻页', '滚动', '上下', '左右', '阅读模式'];
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while (node = walker.nextNode()) {
            for (const t of searchTexts) {
                if (node.textContent.includes(t)) {
                    const el = node.parentElement;
                    results.push({
                        tag: el.tagName,
                        text: node.textContent.substring(0, 50),
                        classes: el.className || '',
                        visible: el.offsetParent !== null,
                        id: el.id || ''
                    });
                }
            }
        }
        
        return results.slice(0, 30);
    }""")
    for item in ui_info:
        print(f"  <{item['tag']}> {item.get('text', '')[:30]} | cls: {item.get('classes', '')[:40]} | vis: {item.get('visible')}")
    
    # 4. 尝试通过 CDP 直接调用 WeRead 内部 API
    print("\n--- 内部 API 探测 ---")
    api_result = browser._page.evaluate("""() => {
        const found = [];
        // 检查 window 对象上的 WeRead 相关属性
        for (const key of Object.keys(window)) {
            if (key.toLowerCase().includes('reader') || key.toLowerCase().includes('weread')) {
                found.push({
                    key,
                    type: typeof window[key],
                    hasToggle: typeof window[key] === 'object' && 'toggle' in window[key],
                    hasMode: typeof window[key] === 'object' && 'mode' in window[key]
                });
            }
        }
        return found;
    }""")
    print(f"  内部 API: {api_result}")
    
finally:
    browser.close()
