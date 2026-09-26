# Fork notes

This is a fork of [lij768423-svg/grok-register-panel](https://github.com/lij768423-svg/grok-register-panel) customized for kidrauhl123.

## What this fork adds

- **Advanced proxy nodes**: import `vless://` / `ss://` / VMess / Trojan / Hysteria2 / TUIC (and Base64 subscriptions). Camoufox still talks HTTP; `sing-box` is started locally when needed.
- **chenyme grok2api push**: set `grok2api_auto_add_remote` plus `grok2api_remote_base` / admin user / password to import Grok Web SSO into `https://xai.premsir.com`.
- **降智**: thinking **text** is required. Billed `reasoning_tokens` without a reasoning delta is `hard` / 降智. `quality_probe_on_register` defaults on.

Install `sing-box` on PATH (or set `proxy_singbox_path`) for VLESS/SS nodes.
