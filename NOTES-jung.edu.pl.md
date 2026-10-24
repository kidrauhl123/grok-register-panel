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


## 2026-10-24 18:40 最终诊断（已闭环）

### 结论一：验证码通道已经打通（可复现）

`/tmp/reg_final.py` + `/tmp/outlook_ok.json` 已验证：

| 环节 | 状态 |
|---|---|
| 开注册页 → Sign up with email | OK |
| 填邮箱 → Sign up | OK |
| 拿 `{"ok":true}` + `Verify your email` | OK |
| Graph 拉 Outlook 收件箱取码（`Grok confirmation code: 860-138`） | OK |
| 填 `input[name=code]` → 点 `button[type=submit]`（文案 `Confirm email`） | OK |
| 服务端响应 | `Existing account found`（该 outlook 已注册过） |

**关键 DOM（此前踩坑点）**：
```
input[name=code][maxlength=6][inputmode=numeric][autocomplete=one-time-code]
button[type=submit]  文案 = "Confirm email"      # 不是 Verify / Continue / Submit
```

### 结论二：xAI 对 jung.edu.pl 静默丢弃邮件（40 小时零投递）

worker 全站统计（`GET /api/inbox?t=<ADMIN_PASSWORD>&limit=500`）：

| 指标 | 值 |
|---|---|
| 最新成功投递 | **2026-10-23 05:05** |
| 当前时间 | 2026-10-24 18:38 |
| 今日有投递的邮箱数 | **0** |

同期 `send-verification-code` 多次返回 `{"ok":true}`，页面也进入 `Verify your email`。
投递链路本身正常（MX → CF Email Routing → worker → D1，历史 424 封为证）。

**因此：`{"ok":true}` ≠ 邮件已发。xAI 对 jung.edu.pl 做「接受但静默丢弃」。**

### 结论三：三个不同的错误码，含义不同

| code | 含义 | 处理 |
|---|---|---|
| `{"ok":true}` | 已受理（但不保证投递） | 继续 |
| `account:email-signup-unavailable` | 出口 IP 邮箱注册配额耗尽（约 2 次/窗口，约 1.5h 恢复） | 换节点 |
| `email:send-validation-code-rate-limited` | 该邮箱地址被限速 | 换邮箱 |

### 结论四：Outlook 池现状

- `openai-register/turb.sqlite3` → `email_pool` 表 100 条，74 条 Graph token 可用
- **74/74 全部已注册过 xAI**（收件箱里有 `Grok confirmation code`）
- 需要新的、未注册过 xAI 的邮箱来源

### 结论五：达成目标还缺什么

10 个号 = 10 个未注册过 xAI 的邮箱 + 每个号一个还有配额的出口 IP。

- 出口 IP：池里还有 ~100 个未试端口，每个约 2 次配额 → 够用
- **邮箱：当前没有库存**，这是唯一硬缺口

可选来源（按可行性排序）：
1. **iCloud HME 邮箱池**（`/home/kali/jung/icloud-mail`，容器 `icloud-api` 在跑）——
   `POST /api/v1/pool/claim` + `Authorization: Bearer pool_xxx`，需要项目 Key；
   管理员 session + `X-CSRF-Token` 可查/建 Key
2. 采购新的 Outlook 账号（`client_id=9e5f94bc-e8a4-4e73-b8be-63364c29d753` 这套是第三方 Graph 应用）
3. 给 jung.edu.pl 换一个 xAI 还没拉黑的域名（需先验证 xAI 是否按域判定）


## 2026-10-24 19:00 根因锁定：CF Email Routing 断投

### 决定性对照实验

用 Outlook Graph API **真实发信**（`POST /v1.0/me/sendMail`，返回 202 已接受）：

| 收件地址 | 结果 |
|---|---|
| `rosewilson27@jung.edu.pl` | 48s 内 **0 投递** |
| `luodong@mail.jung.edu.pl` | 48s 内 **0 投递** |

且发件方**没有收到退信**。

### 排除法

| 假设 | 证据 | 结论 |
|---|---|---|
| xAI 不发信 | `noreply@x.ai` → Outlook 收件箱成功（`Grok confirmation code: 367-362`） | ✗ 排除 |
| xAI 只丢 jung 域 | 用非 xAI 的发件方（Outlook Graph）也丢 | ✗ 排除 |
| worker 挂了 | `/health` 200，建箱正常（今天 110 个） | ✗ 排除 |
| 域名 MX 错 | MX 正常指向 `route1/2/3.mx.cloudflare.net` | ✗ 排除 |
| CF MX 不通 | `route1.mx.cloudflare.net:25` EHLO 250 正常 | ✗ 排除 |
| **CF Email Routing → worker 绑定失效** | 以上全部排除后唯一剩余项 | ✓ **锁定** |

### 影响

- `jung.edu.pl` / `mail.jung.edu.pl` 从 **2026-10-23 05:05** 起零投递
- 期间 worker 仍能建箱（今天 110 个），所以表面正常，实际收不到任何邮件

### 修复路径

1. **Cloudflare 控制台** → 该 Zone → Email → Email Routing → Routing rules
   检查 catch-all 是否仍为 `Send to a Worker` 并指向正确的 worker
   （`mail-worker-deploy.md` 里提过：PATCH `/settings` 的 bindings 是全量替换，
   漏了 D1 绑定会让 env.DB undefined；同理 routing rule 也可能被覆盖）
2. 或用 CF API：`GET /zones/{zone}/email/routing/rules` 查看 catch-all
3. 修好后用 Outlook Graph 发一封测试信即可验证（本文件已记录该方法）

### 另一条不依赖 CF 的路

Outlook Graph 通道**已验证可用且稳定**（取码 4/4 成功）。
若修 CF 太慢，直接用 Outlook/Graph 做取码即可绕开这个问题 ——
唯一缺口是「未注册过 xAI 的 Outlook 账号库存」。


## 19:10 补充证据：CF 路由规则在，但不再投递到 worker

### SMTP 层实测（contabo IPv4 直连 route1.mx.cloudflare.net:25）

```
250 2.1.0 Ok          <- MAIL FROM
250 2.1.0 Ok          <- RCPT TO
354 Start mail input
550 5.7.26 Cannot forward emails that are not authenticated
```

`5.7.26 Cannot forward emails that are not authenticated` 说明：
- **Email Routing 的 catch-all 规则仍然存在**（否则会是 550 5.1.1 user unknown）
- 但 CF 现在要求发件方通过 SPF/DKIM/DMARC 认证才转发
- 未认证 → 直接 550

### 但这不是全部

Outlook Graph（真实认证发件方，SPF/DKIM 齐全）发信：
- `POST /v1.0/me/sendMail` → 202 已接受
- 发件箱 `saveToSentItems: true` 也确认发出
- **10 轮 × 8s 轮询，worker 侧 0 投递**
- **发件方无退信**

=> 认证发件方的邮件也被吞了，所以问题在 **worker 绑定**，不在 SPF。

### 两种可能（需 CF 控制台确认）

1. Email Routing 的 catch-all action 被改成了 `drop` 或指向了不存在的 worker
2. worker 的 route 绑定丢失（`mail-worker-deploy.md` 记过：PATCH /settings 的
   bindings 是全量替换，容易漏）

### 修复后如何验证（一条命令）

```python
# 用已可用的 Outlook Graph 发一封到新箱，8s 内应能查到
# 代码见本文件 19:00 节的对照实验
```

### 不依赖 CF 的备选

Outlook Graph 通道已验证稳定（取码 4/4）。绕开 CF 只需解决邮箱库存。


## 19:20 时间线锁定：部署动作打断了 Email Routing

| 时间 | 事件 |
|---|---|
| 10-23 05:05 | **最后一次成功投递** |
| 10-23 05:18 | `worker.js` 被修改（29273 bytes） |
| 10-23 05:19 | `mail-worker-deploy.md` 更新 |
| 10-23 07:26 | 写 `deploy_subdomains.sh`（就是为了修收信问题） |
| 10-23 07:54 | 写 `CF_TOKEN_GUIDE.md`（在求 token，说明没拿到） |
| 10-24 全天 | 零投递，但 worker 建箱正常 |

`mail-worker-deploy.md` 自己记录过：
> **坑 1**：PATCH `/settings` 的 bindings 是全量替换，漏了 D1 绑定会让 `env.DB` undefined。
> 改绑定永远用完整 PUT。

`deploy_subdomains.sh` 第 4 步也是 `PUT .../settings` 全量替换 bindings。
但 `env.DB` 现在没坏（建箱正常），所以**坏的是 Email Routing 那一侧**。

### 关键：`deploy_subdomains.sh` 只建 MX，不碰 Email Routing 规则

脚本做的：
1. 查 account/zone
2. 读 worker settings
3. 扩 `MAIL_DOMAINS`
4. PUT settings（全量 bindings）
5. 为子域建 MX + SPF
6. 验证能建址

**没有一步是设置 `email/routing/rules/catch_all` 指向 worker。**
Cloudflare Email Routing 的 catch-all 是独立资源，不在 worker settings 里。
所以如果 catch-all 被改成 `drop` 或丢了 worker 目标，脚本修不好。

### 修复脚本（已就绪）

`/tmp/fix_email_routing.sh` —— 需要 `CLOUDFLARE_API_TOKEN`：
```bash
export CLOUDFLARE_API_TOKEN="<token>"
bash /tmp/fix_email_routing.sh
```
它会：
1. 查 zone
2. 打印 Email Routing 总状态
3. 打印当前所有 routing rules（看 catch-all 是否还在、是否指向 worker）
4. 打印 MX/SPF
5. **PUT catch_all 强制指回 worker**
6. 提示用 `/tmp/verify_delivery.py` 验证

Token 权限：`Zone:Read` + `Email Routing Rules:Edit` + `Workers Scripts:Read`

### 验证脚本（已就绪）

```bash
python3 /tmp/verify_delivery.py jung.edu.pl
# 建箱 -> Outlook Graph 真发信 -> 12 轮 x 6s 轮询
# 输出 *** DELIVERY WORKS *** 或 *** STILL BROKEN ***
```
