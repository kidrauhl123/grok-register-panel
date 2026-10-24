# grok-register-panel 生产运行笔记（jung.edu.pl）

## 结论：xAI 已对邮箱注册做出口 IP 级限制

`POST /api/auth/send-verification-code` 的响应：
- `200` + 空体 → 成功，页面前进到 `Verify your email`
- `400` + 空体 → 页面显示 `Email sign-up isn't available right now. Sign up another way.`

**这不是邮箱域名问题**：gmail.com / outlook.com / jung.edu.pl 表现完全一致。
**这也不是浏览器指纹问题**：headed + humanize + camoufox 156 全部无效。

实测同一个节点会在几分钟内从 `VERIFY` 翻转成 `BLOCKED`，说明是按出口 IP 配额/信誉动态判定。
可用节点是少数，且不稳定，必须**每号轮换 + 用前实测**。

## 已验证可用的判定方法

```python
# 只有页面出现这句才算真正可用
'Verify your email' in page.inner_text('body')
```

## 本机代理池

- mihomo 230 个监听端口，`/home/kali/jung/proxypool/`
- `pool_state.json` 有每个端口的 `hosting`/`proxy_flag`/`ms`/`isp`
- 104 个住宅节点（`hosting=false and proxy_flag=false`）
- **不要用 IPRoyal**（用户明确要求）
- 倍率标签 `Nx` 影响流量消耗，1x 最省

## panel 已修的缺陷

1. `random_subdomain_domain('jung.edu.pl') -> 'obiorm.edu.pl'` 吃根域 → 已修（`fada906`）
2. `pick_list_payload` 过滤掉字符串域名数组 → 已修（`bc5cb87`）
3. `detect_email_domain_rejection` 不认识 `Email sign-up isn't available` → 已修
   （原来会无脑 reload 到空白页，最终报"未找到邮箱输入框"）
4. 拒绝后的提示语误导向"换邮箱域名"，实际应"换 IP" → 已改为轮换节点

## 环境要点

- camoufox 需 `browsers/official/<ver>/` + `version.json` + `.0.5_FLAG` + `config.json{active_version}`
- 缺 `.0.5_FLAG` 会被 `camoufox_path()` 判定为旧格式并 **rmtree 整个缓存目录**
- 必须 `pip install "camoufox[geoip]"`，否则启动报 `Please install the geoip extra`
- `GROK_HEADLESS=1` 强制无头；有头需 `DISPLAY`


## 2026-10-24 关键突破与最终诊断

### 1. 真正的错误码（服务端 JSON，之前只看页面文案）

```json
{"error":"Failed to send verification code","code":"account:email-signup-unavailable"}
```
HTTP 400。成功时是 `{"ok":true}` + HTTP 200，页面出现 `Verify your email`。

### 2. 配额规律：**按出口 IP 计数，约 2 次/时间窗口**

实测节点 21171：
- 第 1 次 send-verification-code → `{"ok":true}`
- 第 2 次 → `{"ok":true}`
- 第 3 次 → `account:email-signup-unavailable`
- 约 1.5h 后 → 恢复 `{"ok":true}`（**配额会随时间恢复**）

**推论：探测本身消耗配额。**「先探测再注册」= 白扔一次，注册时正好耗尽。
这是之前 30+ 次全失败的核心原因。

### 3. 域名无辜

同一节点同一时刻，`@gmail.com` 与 `@jung.edu.pl` 都返回 `{"ok":true}`。
之前「gmail 成功 / jung 失败」的差异，是前者先跑吃掉了配额。
**jung.edu.pl 完全可用。**

### 4. 环境变量 https_proxy 是隐性炸弹

`https_proxy=http://10.0.2.2:7897` 会让 curl_cffi 抛：
```
CurlError: (35) TLS connect error: ... OPENSSL_internal:invalid library (0)
```
影响面：
- panel 启动预检 `xAI注册页` 误判 FAIL → 整批中止
- `wait_for_code` 拉邮件列表失败 → 报 `NO_CODE`

**修法**：调用 curl_cffi 前 pop 掉 http_proxy/https_proxy/all_proxy（大小写都要）。

### 5. 当前唯一真实阻塞：xAI 根本没发信

worker 全站统计（`GET /api/inbox?t=<ADMIN_PASSWORD>`）：
- 500 个邮箱中 282 个有信，共 424 封
- **最新一封：2026-10-23 05:05**
- 此后 40 小时，尽管 `send-verification-code` 多次返回 `{"ok":true}`，**零投递**

投递链路本身正常（MX → Cloudflare Email Routing → worker → D1，历史 424 封为证）。
结论：`{"ok":true}` 只代表 xAI 接受了请求，**并不保证发信**；
xAI 对这些 IP + 临时域的组合做了「接受但静默丢弃」。

### 6. panel 代码改动（本轮）

| 文件 | 改动 |
|---|---|
| `grok_register_ttk.py` | 新增 `_proxy_signup_banned` 封禁表 + `ban_proxy_for_signup()` / `_is_proxy_signup_banned()` / `active_signup_bans()`，TTL 900s |
| 同上 | `pick_proxy_for_worker` 三处过滤封禁节点；全封时抛明确错误 |
| 同上 | 3 处 `except EmailDomainRejected` 自动拉黑当前出口 |
| 同上 | 启动预检失败不再中止整批，改为换节点重试 6 次；仍失败则降级为警告 |
| 同上 | `FAIL_LABELS[FAIL_DOMAIN]` 文案改为「出口受限」，提示语改为换 IP 而非换域名 |
| `register_flow.py` | `detect_email_domain_rejection` 新增 3 条 pattern（`Email sign-up isn't available` 等） |

测试：225 passed / 6 failed（6 个均为既有失败：custom dashboard HTML 与 upstream 断言不符）。
注意：`tests/test_proxy_worker_integration.py` 会因为仓库根存在真实 `proxies.txt` 而失败，跑测试前先移走。

### 7. 下一步可行方向

1. **换邮箱提供商**：xAI 对自建临时域可能整体静默；改用 Outlook/Graph 真实邮箱池（`mailbridge/graph_mail.py` 已具备 `to_matches` + `extract_code`）
2. **换出口**：需要能真正更换 IP 的住宅代理（当前 mihomo 228 节点只有 3 个 server，IP 固定）
3. 若要继续用 jung.edu.pl，需先确认 xAI 是否已把该域列入静默丢弃名单
