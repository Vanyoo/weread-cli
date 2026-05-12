#!/usr/bin/env python3
"""诊断微信读书阅读模式"""
import json, time, sys, os

sys.path.insert(0, os.path.dirname(__file__))

from weread_cli.login import QRLogin
from weread_cli.browser import CDPBrowserManager

def main():
    # 加载 cookie
    cookie_file = os.path.expanduser("~/.weread_cookies.json")
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            cookies = json.load(f)
        if isinstance(cookies, list):
            cookies = {c.get("name", ""): c.get("value", "") for c in cookies if c.get("name")}
    else:
        print("❌ 未找到 cookie 文件")
        return

    browser = CDPBrowserManager(cookies, mode="headed")
    if not browser.start():
        print("❌ 浏览器启动失败")
        return

    try:
        # 导航到一本书（使用最近的书）
        from weread_cli.api import WeReadAPI
        api = WeReadAPI(cookies)
        shelf = api.sync_shelf()
        if not shelf or len(shelf) == 0:
            print("❌ 书架为空")
            return
        
        book = shelf[0]
        book_id = book.book_id if hasattr(book, 'book_id') else book.get('bookId', '')
        book_title = book.title if hasattr(book, 'title') else book.get('title', '')
        
        print(f"📖 打开书籍: {book_title} ({book_id})")
        browser.navigate_to_book(book_id)
        time.sleep(2)
        
        # 诊断 1: localStorage 检查
        print("\n=== localStorage 检查 ===")
        config = browser._page.evaluate("""() => {
            const keys = [];
            for (let i = 0; i < localStorage.length; i++) {
                keys.push(localStorage.key(i));
            }
            return keys;
        }""")
        print(f"所有 localStorage keys: {config}")
        
        for key in config:
            if 'config' in key.lower() or 'mode' in key.lower() or 'reader' in key.lower():
                val = browser._page.evaluate(f"""() => localStorage.getItem('{key}')""")
                print(f"  {key}: {val}")
        
        # 诊断 2: 页面可滚动性
        print("\n=== 滚动信息 ===")
        scroll_info = browser._page.evaluate("""() => ({
            scrollHeight: document.documentElement.scrollHeight,
            clientHeight: window.innerHeight,
            scrollTop: document.documentElement.scrollTop,
            scrollable: document.documentElement.scrollHeight > window.innerHeight + 10,
            bodyScrollHeight: document.body.scrollHeight,
        })""")
        print(json.dumps(scroll_info, indent=2, ensure_ascii=False))
        
        # 诊断 3: 翻页按钮检测
        print("\n=== 翻页按钮 ===")
        has_next = browser._find_text('下一页') is not None
        has_prev = browser._find_text('上一页') is not None
        print(f"  下一页: {has_next}, 上一页: {has_prev}")
        
        # 诊断 4: Canvas x 坐标分布
        print("\n=== Canvas x 坐标分布 ===")
        captured = browser._page.evaluate("() => window.__CANVAS_TEXT__ || []")
        if captured:
            xs = [item.get("x", 0) for item in captured if isinstance(item.get("x"), (int, float))]
            if xs:
                print(f"  x 范围: {min(xs):.1f} ~ {max(xs):.1f}")
                print(f"  视口宽度: {scroll_info.get('clientHeight', 0)} (注意: 这里应该是 viewport width)")
                
                viewport_width = browser._page.evaluate("() => window.innerWidth")
                print(f"  视口宽度 (innerWidth): {viewport_width}")
                
                # 分析 x 分布，找大空隙
                sorted_xs = sorted(set(round(x, 0) for x in xs))
                if len(sorted_xs) > 2:
                    gaps = []
                    for i in range(1, len(sorted_xs)):
                        gap = sorted_xs[i] - sorted_xs[i-1]
                        if gap > 50:
                            gaps.append((sorted_xs[i-1], sorted_xs[i], gap))
                    if gaps:
                        print(f"  找到的大空隙 (>50px):")
                        for left, right, gap in gaps:
                            print(f"    {left:.0f} → {right:.0f} (gap: {gap:.0f}px)")
                        # 最大的空隙
                        best = max(gaps, key=lambda g: g[2])
                        print(f"  推荐 mid_x: {(best[0] + best[1]) / 2:.0f}")
                    else:
                        print(f"  未找到大空隙 (>50px)，可能是单栏模式")
                        
                # 统计左右分布
                mid = viewport_width / 2
                left_count = sum(1 for x in xs if x < mid)
                right_count = sum(1 for x in xs if x >= mid)
                print(f"\n  以 mid_x={mid:.0f} 分割:")
                print(f"    左侧 (x < {mid:.0f}): {left_count} 个字符")
                print(f"    右侧 (x >= {mid:.0f}): {right_count} 个字符")
        else:
            print("  Canvas 文本为空")
        
        # 诊断 5: 尝试切换模式
        print("\n=== 尝试切换模式 ===")
        old_config = browser._page.evaluate("""() => {
            try { return JSON.parse(localStorage.getItem('readerConfig')); } 
            catch(e) { return null; }
        }""")
        print(f"  切换前 readerConfig: {old_config}")
        
        result = browser.toggle_reading_mode()
        print(f"  toggle_reading_mode() 返回: {result}")
        
        new_config = browser._page.evaluate("""() => {
            try { return JSON.parse(localStorage.getItem('readerConfig')); } 
            catch(e) { return null; }
        }""")
        print(f"  切换后 readerConfig: {new_config}")
        
        # 检查切换后的 UI 变化
        has_next_after = browser._find_text('下一页') is not None
        scroll_info_after = browser._page.evaluate("""() => ({
            scrollable: document.documentElement.scrollHeight > window.innerHeight + 10,
        })""")
        print(f"  切换后 - 下一页按钮: {has_next_after}, 可滚动: {scroll_info_after.get('scrollable')}")
        
    finally:
        browser.close()

if __name__ == "__main__":
    main()
