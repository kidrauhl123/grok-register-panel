# Upstream & Lineage Notes

This repository is an independently maintained and enhanced version derived from two upstreams:
1. **Root Upstream**: [AaronL725/grok-register](https://github.com/AaronL725/grok-register) (MIT License) - Original Camoufox registration pipeline.
2. **Direct Upstream**: [lij768423-svg/grok-register-panel](https://github.com/lij768423-svg/grok-register-panel) (MIT License) - Web dashboard, batch task supervisor, sing-box external proxy bridge, and BFS detection.

## Key Enhancements in this Repository

- **RoxyChrome Browser Engine (Turnstile bypass)**:
  - Chromium 155 modified kernel with compile-time `fingerprint_inject_service`.
  - Reads `<user-data-dir>/lumi.conf` (AES-256-GCM encrypted JSON) and injects
    UA / navigator / screen / **WebGL** / Canvas / AudioContext / ClientRects /
    WebRTC / timezone / locale / proxy.
  - Solves Cloudflare Turnstile error `600010` (bot behavior detected) caused by
    `SwiftShader Device (Subzero)` software rendering under Xvfb. After injection
    the widget issues a 770~800 byte token and the sign-up submit proceeds.
  - Configurable via `browser_engine` (`roxy` / `chromium` / `camoufox`) and
    `ROXY_CHROME_EXECUTABLE`.
- **Proxy Intelligence & KDA Scoring**:
  - Battle-tested KDA rating system based on successes, failures, and consecutive blocks.
  - Dynamic weighted sorting, MVP rank 1 badge, and real-time win-rate tracking.
- **Weighted Traffic Metering**:
  - Multiplier-weighted accounting (1x–10x node billing multiplier) on proxy traffic.
  - Per-batch and cumulative upstream/downstream bandwidth tracking without exposing node credentials.
- **Account Lifecycle & Subscription Probing**:
  - Automatic detection of Grok subscription tier, trial offer eligibility, and client version fallback protection.
- **Advanced Proxy Node Protocol Support**:
  - Support for `vless://`, `ss://`, VMess, Trojan, Hysteria2, TUIC, and Base64 subscription import via local `sing-box`.
- **Remote Ecosystem Integration (chenyme grok2api)**:
  - Automated push of Grok Build accounts (OAuth) or Web SSO into remote gateways with admission control.
- **Strict Anti-Downgrade (降智) Probing**:
  - Mandatory reasoning text validation during registration quality checks.
- **Modernized UI & Target Tracker**:
  - Re-anchored batch dashboard with strict health target tracking, non-destructive pagination, and streamlined controls.

