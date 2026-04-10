# 微信读书登录 API 研究报告

## 测试的 API

### 1. POST /api/auth/getLoginUid

**请求：**
```http
POST https://weread.qq.com/api/auth/getLoginUid
Content-Type: application/json
Referer: https://weread.qq.com/
```

**响应（404）：**
```json
{
  "url": "/api/auth/getLoginUid",
  "statusCode": 404,
  "statusMessage": "Page not found: /api/auth/getLoginUid",
  "message": "Cgi Request Error",
  "stack": "",
  "data": {"path": "/api/auth/getLoginUid"}
}
```

**结论：** ❌ 无法直接使用，可能需要：
- 特定的请求头（如特殊的 User-Agent）
- 先访问某些页面获取必要的 Cookie/Token
- 或者是微信读书客户端专用 API（非网页版）

---

### 2. GET /api/auth/getLoginInfo

**请求：**
```http
GET https://weread.qq.com/api/auth/getLoginInfo?uid={uid}&otp={otp}
```

**JS 代码中的调用：**
```javascript
sP("/api/auth/getLoginInfo", {
  query: {uid: f.value, otp: b.value},
  timeout: 60000
}).then((e) => {
  if (e.succeed) R(e)
})
```

**参数说明：**
- `uid`: 登录会话 ID（从 getLoginUid 获取）
- `otp`: 一次性密码（来源未知，可能是时间戳或随机数）

**结论：** ⚠️ API 存在但依赖 getLoginUid，需要进一步研究

---

## 可行的登录方案

### 方案 A：微信开放平台二维码登录 ✅

**流程：**

1. **获取二维码 URL**
   ```
   GET https://open.weixin.qq.com/connect/qrconnect?
     appid=wxd80042f4d6cfc9a6&
     redirect_uri=https://weread.qq.com/&
     response_type=code&
     scope=snsapi_login&
     state=STATE
   ```
   
2. **提取 UUID**
   - 从响应 HTML 中提取 `uuid=xxx`

3. **显示二维码**
   - 使用上述 URL 生成二维码

4. **轮询登录状态**
   ```
   GET https://long.open.weixin.qq.com/connect/l/qrconnect?uuid={uuid}&_={timestamp}
   ```

5. **处理回调**
   ```javascript
   window.callback={
     "wx_errcode": 0,
     "status": 1,  // 1=已扫码，2=已过期，3=等待确认
     "code": "xxx" // 扫码成功后获取的 code
   }
   ```

6. **使用 code 换取 token**
   - 需要调用微信 OAuth 接口
   - 由于是跨域请求，可能需要在浏览器环境中执行

**优点：**
- 不依赖 Playwright
- 纯 HTTP 请求

**缺点：**
- code 换 token 步骤复杂
- 可能需要微信 AppID 的 secret

---

### 方案 B：Playwright 浏览器自动化 ✅（当前使用）

**流程：**

1. 启动无头浏览器
2. 访问 https://weread.qq.com/
3. 等待用户扫码登录
4. 提取 Cookie
5. 保存 Cookie 到文件

**优点：**
- 100% 可靠
- 模拟真实用户行为
- 不需要研究复杂的 API

**缺点：**
- 需要安装 Chromium（~100MB）
- 首次启动较慢

---

### 方案 C：从浏览器导入 Cookie ✅

**流程：**

1. 用户在 Chrome/Firefox 中登录微信读书
2. 使用 `browser_cookie3` 库读取浏览器 Cookie
3. 导入到 weread-cli

**优点：**
- 简单快速
- 不需要额外依赖

**缺点：**
- 需要先在浏览器中登录
- Cookie 加密可能导致读取失败

---

## JS 代码分析

从 `https://cdn.weread.qq.com/web/wrweb-next/_nuxt/WDXGFRt7-legacy.js` 中发现：

### 登录流程关键代码：

```javascript
// 获取 UID
function getLoginUid() {
  return post("/api/auth/getLoginUid").then((e) => {
    if (e.uid) {
      f.value = e.uid  // 保存 UID
      B()              // 开始监听登录状态
    }
  })
}

// 监听登录状态
function B() {
  d.value = "LISTENING"
  sP("/api/auth/getLoginInfo", {
    query: {uid: f.value, otp: b.value}
  }).then((e) => {
    if (e.succeed) R(e)  // 登录成功
  })
}

// 登录成功后的处理
function R(e) {
  if (e.accessToken && e.webLoginVid) {
    n.value.vid = e.webLoginVid
    n.value.skey = e.accessToken
    n.value.hasLogin = true
    // 保存用户信息
  }
}
```

### 关键发现：

1. **UID 来源**：从 `/api/auth/getLoginUid` 获取
2. **OTP 来源**：`b.value`（未知，需要进一步分析）
3. **登录成功后**：获取 `accessToken` 和 `webLoginVid`

---

## 下一步研究方向

1. **OTP 参数**
   - 分析 `b.value` 的来源
   - 可能是时间戳、随机数或从其他 API 获取

2. **getLoginUid 的 404 问题**
   - 尝试不同的请求头组合
   - 使用 Puppeteer/Playwright 拦截真实请求
   - 检查是否需要特定的 Cookie

3. **微信 OAuth code 换 token**
   - 研究微信开放平台的 token 接口
   - 可能需要 AppID 和 Secret

---

## 当前最佳实践

**继续使用 Playwright 方案**，原因：
- 已经实现且稳定工作
- 不依赖未文档化的 API
- 用户体验良好（浏览器扫码）

**优化方向：**
- 减少 Chromium 体积（使用系统浏览器）
- 加快首次启动速度
- 改进 Cookie 持久化

---

## 参考链接

- 微信开放平台：https://open.weixin.qq.com/
- 微信 OAuth 文档：https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html
- 微信读书网页版：https://weread.qq.com/

---

*最后更新：2026-04-04*
