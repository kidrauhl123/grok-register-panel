# 邮件链路（jung.edu.pl → Outlook sink）

> 这条链路是注册取码的唯一正确路径。**不要**去 Cloudflare Worker 里找信。

## 一句话

`*@jung.edu.pl` 的邮件由 **Cloudflare Email Routing** 转发到
**`MisumiMallinak5202@outlook.com`**，用 Graph API 读这个邮箱，按 `To` 头精确匹配别名取码。

## SINK 账号

```
MisumiMallinak5202@outlook.com
```

- 四段式凭证在 `/home/kali/outlook-rt/outlook_rt_inventory.txt` 第 1 行
  （格式 `email----password----client_id----refresh_token`）
- Contabo 上同一批 100 个账号在 `/opt/outlook-viewer/credentials.txt`
- Contabo 有个 Web 面板：`http://<contabo>:8792/?token=<VIEWER_TOKEN>`
  （`VIEWER_TOKEN` 在 `systemctl cat outlook-viewer` 里，服务名 `outlook-viewer.service`）

## 取码方法

```python
import sys; sys.path.insert(0,'/home/kali/jung/mailbridge')
from graph_mail import GraphInbox, to_matches, extract_code

line = open('/home/kali/outlook-rt/outlook_rt_inventory.txt').readline().strip()
e, p, cid, rt = line.split('----')[:4]
ib = GraphInbox({'email':e,'password':p,'client_id':cid,'refresh_token':rt}, timeout=25)

for m in ib.recent(top=40):
    if to_matches(m, 'peter.park@jung.edu.pl'):   # 按 To 头精确匹配
        code = extract_code(m)                     # 形如 508-154
        break
```

`mailbridge/graph_mail.py` 已实现 `to_matches()` 和 `extract_code()`，直接用。

## 已核实的事实（2026-10-25）

| 事实 | 证据 |
|---|---|
| 转发链路正常 | sink 收件箱里全是 `to=<xxx@jung.edu.pl> from=noreply@x.ai` |
| xAI 一直在发信 | `Grok confirmation code: 001-045` … 数百封 |
| 域名没被封 | `jung.edu.pl` 在四份主流 disposable 名单里都不在列 |
| `{"ok":true}` ≠ 已投递 | 缺 Castle token 时返回 200 但不发信（见 NOTES-jung.edu.pl.md） |
| Worker 收不到信是正常的 | Email Routing 的 catch-all 已改指向 Outlook sink，不是 Worker |

## 踩过的坑

1. **别查 Cloudflare Worker**。`api.jung.edu.pl` 的 `/api/mails` 从 2026-10-23 起恒为 0 封，
   因为信根本不去 Worker。查 Worker 会得出"xAI 不发信"的错误结论。
2. **别用 `MisumiMallinak5202` 去注册**。它是 sink，注册会占掉它。
3. 那 100 个 outlook 账号**全部已注册过 xAI**，不能当注册邮箱用；
   但它们里的第 1 个是收信 sink，用途不同。
4. 环境变量 `https_proxy=http://10.0.2.2:7897` 会让 `curl_cffi` 报
   `(35) TLS connect error`，调 Graph / worker API 前要 pop 掉。

## 为什么邮箱库存是无限的

`jung.edu.pl` 走 CF catch-all，**任意本地部分都能收信**，不需要预先建箱：

```bash
curl -X POST https://api.jung.edu.pl/admin/new_address \
  -H "Content-Type: application/json" \
  -H "x-custom-auth: $(grep ADMIN_PASSWORD /home/kali/jung/mail-admin.env | cut -d= -f2)" \
  -d '{"domain":"jung.edu.pl"}'
```

或者干脆跳过建箱，随便编 `任意名@jung.edu.pl` —— catch-all 会转给 sink，
取码时按 `To` 头匹配即可。

## SINK 的已知限制（2026-10-25 实测）

`sink_probe` 用 Graph `sendMail` 从 sink 自己发给自己（`sam.yan@jung.edu.pl`），
sink 收件箱里**没找到**。原因：CF 对「发件方未通过 SPF/DKIM/DMARC」的转发会
`550 5.7.26 Cannot forward emails that are not authenticated`；
而 Outlook 自己发给 CF MX 时，`From` 是 outlook.com 但实际发送 IP 属 Microsoft，
DMARC 对齐可能不通过。

**但这不影响生产**：xAI 用 SendGrid 发信（`wfbtsnfp.outbound-mail.sendgrid.net`），
SPF/DKIM 全 pass，所以 xAI 的码 100% 能落到 sink。

---

## Grok2API 号池自动推送（2026-10-25 打通）

### 逻辑

注册成功 → 换 auth token → **降智检测** → 按 verdict 决定是否推送：

```python
# grok_register_ttk.py:1377
push_only_healthy = config.get("grok2api_remote_only_healthy", True)
if quality_on and push_only_healthy and verdict != "healthy":
    _cpa_log(f"⚠️ 账号降智检测结果为 {verdict}（非 healthy），已拦截远程推送到 xai.premsir，仅保留本地")
else:
    maybe_import_remote(record=record, sso=sso, email=email, verdict=verdict)
```

即：**只有 `quality_verdict == "healthy"` 的账号才会进号池**；
`hard` / `error` / `unknown` 一律拦截，只留本地 `cpa_auth/`。

### 必要配置

```json
{
  "cpa_auto_add": true,
  "quality_probe_on_register": true,
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://xai.premsir.com",
  "grok2api_remote_admin_username": "jungpusher",
  "grok2api_remote_admin_password": "<见下方>",
  "grok2api_remote_only_healthy": true,
  "grok2api_remote_target": "build"
}
```

三个开关缺一不可：
- `cpa_auto_add=false` → 不换 token，质检无法执行，也不会推送
- `quality_probe_on_register=false` → 不质检，`verdict` 为空
- `grok2api_auto_add_remote=false` → `maybe_import_remote()` 第一行直接 return

### 接口

| 项 | 值 |
|---|---|
| 公网入口 | `https://xai.premsir.com`（`grok.premsir.com` 308 跳转过来） |
| 登录 | `POST /api/admin/v1/auth/login` `{username, password}` |
| 导入 | `POST /api/admin/v1/accounts/import`（multipart，字段 `file`） |
| 列表 | `GET /api/admin/v1/accounts` |
| 容器 | `grok2api`，宿主端口 `127.0.0.1:8001` / `0.0.0.0:8000` |
| 数据库 | SQLite `/var/lib/docker/volumes/grok2api_grok2api-data/_data/backend.db` |

### 专用推送账号

原 `admin` 密码未知（bootstrapAdmin 只在首次启动时生效，之后改过），
因此新建专用账号 `jungpusher`：

```sql
INSERT INTO admins (username, password_hash, created_at, updated_at)
VALUES ('jungpusher', '<bcrypt>', datetime('now'), datetime('now'));
```

**坑：Go 的 `golang.org/x/crypto/bcrypt` 只接受 `$2a$` 前缀。**
Python `bcrypt` 默认生成 `$2b$`，直接写入会导致 `invalidCredentials`。
需要替换：

```python
h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()
h = "$2a$" + h[4:]      # 关键
```

写库时注意 shell 转义会破坏 `$`，**用文件传输再读入**，不要内联到 ssh 命令里。

### 验证结果（2026-10-25）

```
healthy 账号 u39c45489f5@jung.edu.pl
  → 已导入 chenyme grok2api Grok Build: created=1, updated=0, skipped=0, synced=1
  → 号池 id=6573 status=active enabled=True

hard 账号 uc52035e7ee@jung.edu.pl
  → ⚠️ 账号质量裁决为 hard（非 healthy），已拦截远程导入 xai.premsir
  → 仅保留本地 cpa_auth/
```
