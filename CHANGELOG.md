# Changelog

## Unreleased（本 fork 自研，未发布）

### Added

- **RoxyChrome 浏览器引擎**：Chromium 155 魔改内核 + `lumi.conf` 指纹注入，
  绕过 Turnstile `600010`。注入 UA / navigator / screen / **WebGL** / Canvas /
  AudioContext / ClientRects / WebRTC / 时区 / 语言 / 代理。新增 `roxy_browser.py`。
- `browser_engine` 配置项与 `GROK_BROWSER_ENGINE` 环境变量：`roxy` / `chromium` / `camoufox`。
- `ROXY_CHROME_EXECUTABLE` 环境变量（默认自动探测 `~/.config/RoxyBrowser/chrome-bin/*/RoxyChrome`）。
- Import VLESS / VMess / Trojan / Hysteria2 / TUIC / Shadowsocks (and Base64 subscriptions). HTTP 代理仍走 `camoufox`/`chromium`；`sing-box` 在本地启动。
- Optional chenyme grok2api remote import: `grok2api_auto_add_remote` plus admin credentials push Grok Web SSO to the reverse proxy.
- 代理节点 KDA 评分、连胜/连败计分板、加权流量计费（1x–10x 倍率）、复合排序与 MVP 徽章。
- 人化操作引擎：键盘动力学、鼠标漂移、hover、Turnstile 抖动、表单真实停顿。
- Grok 账号订阅/试用优惠状态检查与客户端版本号保底（`GROK_VERSION = 1.0.46`）。
- 恢复链路：远端 Premsir 导入与 SSO 转换时的质量闸联动。

### Fixed

- **Turnstile token 为空导致资料页提交无网络请求**：`ex()` 里 `if(!B) return`
  直接短路。根因是 Xvfb 下 WebGL 为 `SwiftShader Device (Subzero)`，Cloudflare
  判为自动化并返回 `600010`。改用 RoxyChrome 注入真实 GPU 指纹后 token 长度
  770~800 字节，提交正常。
- **`Cannot run the event loop while another loop is running`**：浏览器重试时
  上一轮 Playwright 连接与 asyncio loop 未释放。新增
  `browser_session._teardown_pw_connection()`，在每次启动前清理。
- **`TLS connect error: invalid library`**：`curl_cffi` 隐式读取
  `HTTP(S)_PROXY` 环境变量，与显式 `proxies=` 叠加破坏 BoringSSL 握手。
  `sso_to_auth_json.py` 在模块加载时清除环境代理。
- **RoxyChrome 忽略 `--proxy-server`**：代理只认 `lumi.conf` 的 `fproxy` 字段，
  `roxy_browser.py` 自动把 `config.proxy` 写入。

### Changed

- 降智测试只按有没有 thinking **文本**判定：有 thinking 为正常，没有为降智。账单里的 `reasoning_tokens` 单独出现不算思考。
- `quality_probe_on_register` 默认打开；但仅当 `cpa_auto_add=true` 时才会真正执行
  （需要 access_token 才能打 grok.com 质检接口）。
- `fill_email_and_submit` 超时保持 60s（人化流程较慢）；注册页壳等待放宽到 20s（对齐上游）。
- README 按当前实现重写：移除上游代理服务商推广，补充 RoxyChrome 引擎章节与 Turnstile 排障 FAQ。

## 0.6.0 - 2026-10-24（已合入上游）

### Added

- 新增 Outlook/Hotmail 本地卡池 provider：支持 `email----password----client_id----refresh_token` 格式、IMAP XOAUTH2 收信，以及桌面 GUI / CLI 配置。
- 新增 20 项离线回归测试，覆盖跨平台导入、旧编码控制台、线程/进程并发取号、共享已用文件、写盘异常、旧邮件过滤和注册阶段回收边界；接入现有 Windows 测试入口。

### Changed

- 页面导航默认等待 `domcontentloaded`，并放宽注册页与邮箱表单等待时间。
- CPA chat 请求头默认使用客户端版本 `1.0.13`，可通过 `GROK_CLIENT_VERSION` 覆盖；token 交换链路继续使用独立版本常量。
- 降智测试只按有没有 thinking 判定：有 thinking 为正常，没有为降智。不再用 Token/s / `burst` / `soft` 分档。

### Fixed

- Outlook 卡池复用跨平台文件锁和私有原子写入，修复 Windows 无法导入 `fcntl` 的启动错误及旧编码控制台中文日志异常。
- 取号时先保存已用记录，再更新待用池；写盘与回滚异常保留账号记录，已用路径留空使用默认值，多个卡池共用已用文件时共同加锁。
- 仅读取基线 UID 之后的新邮件；基线探测失败时归还邮箱并终止该次取号，防止误用旧验证码。
- 并发 worker 进入建号资料提交阶段后保留邮箱为已用状态，防止 SSO 超时、建号结果不明或本地保存失败导致重复注册。

## 0.5.0 - 2026-09-06

### Added

- Add a panel **降智测试** that batch-probes CPA / Grok2API accounts with a real streamed reply over configured 家宽 proxies. Missing thinking or inflated Token/s is degraded; 401/403 / permission-denied is risk. CLI: `scripts/check_quality.py`.
- Optional register-time short probe via `quality_probe_on_register` (default **off**). When enabled, SSO→OAuth write stamps `quality_*` onto CPA / Grok2API auth so a later panel scan is not required for new accounts.
- Add an `inbucket` email provider for self-hosted Inbucket instances: generate addresses under a configured receive domain and poll the v1 mailbox API for the xAI verification code. Root domains accept a comma-separated rotation list, and `inbucket_random_levels` can stack 1-3 random subdomain labels per address (wildcard MX required).
- Treat Windows as a first-class runtime: PowerShell setup/batch/panel scripts, Playwright `node.exe` + EPIPE guard (no bash wrapper), default headless batches, and SOCKS5 `PySocks` as a direct dependency so the panel can import remote residential URLs without Linux mixed ports.
- Add a GitHub Pages landing page and link Discussions from the README.

### Changed

- Stop using grok.com `botFlagSource` / `policy=deny` as a registration risk gate or operational verdict. SSO scan remains as a deprecated diagnostic only.
- Recommend residential (家宽) exits and Outlook-class mailboxes; domain emails are no longer the suggested default.
- 降智测试改为短题（时钟夹角 `3:27` + `QUALITY_OK`）、`max_tokens=48`，见到 thinking 约 800ms 后掐流。面板批量扫描用于存量复测。

### Fixed

- Stop assigning `scripts/playwright-node` (a POSIX shell wrapper) to `PLAYWRIGHT_NODEJS_PATH` on Windows, which previously made Camoufox fail to spawn.
- Keep POSIX `GROK_PLAYWRIGHT_NODE` on a real node binary so the wrapper cannot `exec` itself. Quote Windows `NODE_OPTIONS --require` paths that contain spaces.
- Treat a mid-stream proxy/upstream disconnect as a transport error (or keep a partial sample) instead of crashing the quality scan.

## 0.4.4 - 2026-08-16

### Added

- Persist owner-only batch traffic history and show rolling average traffic per batch and per successful account in the live panel.
- Add an SSO risk panel and CLI that check `botFlagSource` / `policy=deny` from existing SSO cookies without exchanging tokens; panel exports contain redacted state only, while reusable clean SSO remains host-local.
- Pin Camoufox/Playwright to system Node 22 plus an EPIPE socket guard so a dead browser pipe no longer kills the whole batch.
- Default browser fingerprints to Windows (UA / WebGL / Segoe) even when the host is Linux Xvfb.

### Fixed

- After x.ai shows the Next.js “error loading this page” overlay, keep waiting for the SSO redirect instead of hard-reloading `/sign-up` (that reload was aborting createAccount).
- Treat Playwright `EPIPE` / `TargetClosed` as a retryable browser failure and rotate the sticky exit.

## 0.4.2 - 2026-08-11

### Fixed

- Validate managed proxies against the actual xAI registration page, immediately retire proxies that fail the batch precheck, and require an explicit retest after network/TLS cooldowns instead of reviving stale health from an old exit IP.

## 0.4.1 - 2026-08-11

### Fixed

- Fix Windows state, progress, proxy, email, panel, orchestrator, and recovery file writes when `os.fchmod` is unavailable; use `msvcrt` byte-range locks for cross-process state protection.
- Add `GROK_HEADLESS` / `GROK_HEADED` controls and software-rendering preferences for Windows sessions where headed Camoufox GPU processes fail.
- Preserve configured virtual-environment Python symlink paths so panel-launched jobs keep their installed dependencies.
- Ship `tzdata` in both direct and locked dependency manifests for Windows Beijing-time support.

## 0.4.0 - 2026-08-11

### Added

- Add post-registration sign-in recovery so workers can rebuild an SSO session with the account credentials when the normal redirect loses the cookie.
- Add bounded Cloudflare address-collision retries and a configurable random-subdomain mode for wildcard mail routing.
- Add per-batch HTTP/HTTPS proxy byte metering with authenticated upstream forwarding and a live upload/download KPI in the panel.
- Add Windows-safe supervisor pipe streaming, recursive Camoufox process-tree shutdown, and user-local browser profiles.

### Changed

- Display panel, proxy, blacklist, and worker timestamps in Beijing time regardless of the server timezone.
- Keep up to 80 recent success/failure records and paginate them in the panel while refreshing full statistics every 30 seconds.
- Enable the guarded static-asset cache for panel-launched batches and reduce browser, proxy-rotation, slot, and supervisor retry defaults.
- Stop the batch and orchestrator without retrying when the xAI registration-page precheck fails.

### Fixed

- Preserve BFS `unknown` handling and `bfs_skip_cpa` behavior instead of treating undecodable tokens as clean.
- Redact Cloudflare fallback errors, avoid retrying unrelated HTTP 400 responses, and fall back to fixed UTC+8 when system tzdata is unavailable.
- Remove the narrow-layout horizontal overflow in the panel's proxy and email views.
- Bound Windows proxy exit-IP probes by a total timeout and reject malformed or stale cached IP candidates.

## 0.3.0 - 2026-08-09

### Added

- Add a managed external proxy pool with single/bulk HTTP proxy import, enable/disable controls, health tests, cooldown state, and panel APIs.
- Add configurable email providers, including MoeMail, plus an authenticated provider editor and connection test in the panel.
- Add a managed email-domain pool with rotation rules, failure thresholds, reset controls, and worker integration.
- Add **JWT `bfs` claim detection** to registration and OAuth output. Flagged accounts are recorded in `accounts/sso_bfs_flagged.txt`, and CPA records receive `bfs` metadata.
- Add the panel **BFS 检测** card, `/api/bfs` endpoints, `scripts/check_bfs.py`, and the `bfs_check`, `bfs_skip_cpa`, and `bfs_disable_cpa` settings.
- Add an opt-in shared browser static-asset cache for scripts, stylesheets, fonts, and public images, with size limits, TTL handling, and private-response safeguards.
- Add a self-updating GitHub Star History chart to the project documentation.

### Changed

- Supervise headless batches and atomically persist completed slots so Playwright/Camoufox driver crashes or stalls resume only the remaining work.
- Make process discovery and batch launch platform-aware with `psutil`, Linux auto-Xvfb, macOS direct launch, Windows virtualenv paths, and actionable missing-procfs errors.
- Support external runtime roots while keeping process control scoped to the configured project.
- Move GitHub Actions to Node.js 24-compatible action versions and expand release checks for proxy, email, BFS, cache, platform, and supervisor behavior.
- Document deployment guidance, the LINUX DO community link, and the related Grok2API egress project.

### Fixed

- Fix BFS unknown-token handling, stale metadata precedence, merged auth scanning, CLI config loading, and configured relative/absolute auth-directory resolution.
- Detect buffered Playwright crash markers immediately instead of waiting for the supervisor idle timeout.
- Avoid false Cloudflare email preflight failures when `/admin/new_address` uses `x-admin-auth` but `/api/domains` expects mailbox authentication.
- Preserve full success statistics and per-day jsonl results across the panel's two-second status polling.

## 0.2.0 - 2026-07-30

- Redesign the live panel with responsive light and dark themes.
- Add a dedicated usage and troubleshooting view.
- Add pending SSO and account-file recovery with success dequeue.
- Move learned ASN rules from Python source into locked JSON state.
- Scope process discovery and termination to one project root.
- Require monitor authentication for operational read and write APIs.
- Add security headers, bounded request bodies, and redacted log output.
- Create runtime credentials, account data, logs, state, and PID files owner-only.
- Add release tests, CI, a systemd service template, and deployment checks.
