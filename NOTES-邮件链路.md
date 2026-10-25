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
