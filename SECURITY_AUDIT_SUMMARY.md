# T-Music: Security Audit Executive Summary
**Date:** 2026-10-19  
**Target:** T-Music Audio Streaming Application (`K:\test\tmusic`)  
**Auditor:** Principal Application Security Engineer  
**Release Gate Verdict:** **BLOCKED FOR PUBLIC INTERNET DEPLOYMENT**

---

## 1. High-Level Summary for Project Owner

T-Music is an exceptionally well-engineered personal music streaming application. The backend exhibits mature defensive engineering:
- **Argon2id KDF** with bounded workers and anti-timing equalizers.
- **Fail-closed CSRF** protection with automatic cookie refreshing.
- **SSRF defense** with IP-pinning and cloud metadata blocking (`169.254.169.254`).
- **Encrypted Telegram sessions** using Fernet keys derived from environment secrets.
- **Strict path normalization** preventing directory traversal across both static assets and temporary transcoder files.

However, when evaluating the application under the threat model of a **public internet-facing service**, two specific vulnerabilities block immediate release.

---

## 2. Release Blockers Requiring Owner Decisions

### Blocker 1: Fixed Admin Password `123` (`SEC-001`)
- **Situation:** Per the 2026-10-02 owner decision, the admin password is fixed to `123` in `music.env` to simplify private home usage. Automated tests strictly enforce this value.
- **Risk on Public Internet:** Any internet user or automated scanner testing standard dictionary credentials will immediately obtain full administrative access to T-Music, allowing access to SQL backup restoration, user database records, and server logs.
- **Action Required for Owner:**
  - *Option A (Recommended for Public Deployment):* In the production environment, run `python scripts/init_admin.py` to set a strong random password, keeping `123` exclusively in private local test sandboxes.
  - *Option B (Recommended if Owner Wishes to Keep `123`):* Put T-Music behind an upstream reverse proxy (e.g. Nginx, Cloudflare Zero Trust, or Tailscale) and block public access to `/api/admin/*` and the admin login endpoint, allowing only private LAN/VPN access to the panel.

---

### Blocker 2: Unauthenticated Subsonic Account Lockout (`SEC-002`)
- **Situation:** The Subsonic API (`/rest/*`) allows unauthenticated requests and counts failed logins against the primary web account's failure counter.
- **Risk on Public Internet:** An anonymous attacker sending 5 failed requests to `/rest/ping.view?u=admin&p=wrong` immediately locks the administrator out of the web panel for 15 minutes. This can be repeated continuously to permanently deny service to the owner.
- **Action Required:** Apply a lightweight code patch to decouple Subsonic authentication failures from the main web account lockout before public exposure.

---

## 3. Summary of Findings

| ID | Severity | Title | Impact | Resolution Effort |
|---|---|---|---|---|
| **SEC-001** | **CRITICAL** | Default Admin Password `123` | Full admin compromise via brute-force | Config / Proxy rule (10 min) |
| **SEC-002** | **HIGH** | Subsonic Account Lockout DoS | Remote denial of service against admin | Code fix (15 min) |
| **SEC-003** | **HIGH** | Historical DB Password in Git | Database compromise if credentials reused | DB password rotation |
| **SEC-004** | **HIGH** | Mobile App Cleartext & Navigation | Session interception on untrusted Wi-Fi | Config update in mobile |
| **SEC-005** | **MEDIUM** | Public Exposure of UDP 8211 | Internal IP disclosure / UDP amplification | Firewall / Docker config |
| **SEC-006** | **MEDIUM** | Insecure Cookie Flags (HTTP) | Session token theft without HTTPS | Set `COOKIE_SECURE=true` |
| **SEC-007** | **MEDIUM** | Reverse Proxy IP Spoofing / Lockout | Legitimate users locked out behind proxy | Proxy header configuration |
| **SEC-008** | **LOW** | Outdated `source-map-js` | ReDoS vulnerability in build tooling | Run `npm audit fix` |

---

## 4. Next Steps
1. Review the full technical findings in [`SECURITY_AUDIT_REPORT.md`](file:///K:/test/tmusic/SECURITY_AUDIT_REPORT.md).
2. Execute the prioritized steps in [`SECURITY_REMEDIATION_PLAN.md`](file:///K:/test/tmusic/SECURITY_REMEDIATION_PLAN.md).
3. Validate changes with [`SECURITY_TEST_PLAN.md`](file:///K:/test/tmusic/SECURITY_TEST_PLAN.md).
4. Once P0 items are resolved, the Release Gate will transition to **PASSED (PRODUCTION READY)**.
