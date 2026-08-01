# Cloudflare HTTPS Tunnel Operational Guide
### Starkville Korean Church (PCA) — Live Translation System

This document outlines the setup, testing, operational checklists, and security policies for exposing the SKC Live Translation service to attendees over public HTTPS and local Wi-Fi.

---

## 1. Operational Overview

* **Public URL**: `https://live.starkvillekoreanchurch.org/live`
* **Public Hostname**: `live.starkvillekoreanchurch.org`
* **Cloudflare Tunnel Name**: `skc-live-translation`
* **Tunnel Status**: Must be `Healthy` in Cloudflare Zero Trust Dashboard
* **Service Type**: `HTTP`
* **Origin Service Target**: `127.0.0.1:8080` (SKC Live Translation App fixed at port 8080)
* **Default Catch-All Route**: `http_status:404`

> [!IMPORTANT]
> **No Router Port Forwarding Required**:
> Cloudflare Tunnel operates as an outbound connection from the translation PC to Cloudflare's edge servers. Never expose port 8080 directly to the public internet or configure router port forwarding.

---

## 2. Local & LAN Access Architecture

* **In-Person Primary URL**: `http://skc-live.local:8080/live`
* **mDNS/Zeroconf**: The application automatically advertises `skc-live.local` on startup and dynamically follows the host PC's current DHCP-assigned IP address.
* **Operator Fallback IP**: The raw private IP address (e.g., `http://<local-ip>:8080/live`) is retrieved dynamically via `/api/status` for operator troubleshooting only. Never hard-code private `192.168.x.x` IP addresses in permanent attendee documentation or printed materials.
* **Public HTTPS Role**: Public HTTPS is used for remote stream viewers, attendees on cellular data (LTE/5G), and devices that do not support `.local` mDNS resolution.

---

## 3. Operational Tests & Health Monitoring

Run these commands on the translation PC to verify system health:

```bash
# 1. Test local application listening on port 8080
curl -I http://127.0.0.1:8080/live

# 2. Test local mDNS resolution on sanctuary Wi-Fi
curl -I http://skc-live.local:8080/live

# 3. Test public HTTPS route via Cloudflare Tunnel
curl -I https://live.starkvillekoreanchurch.org/live

# 4. Check application API status payload
curl http://127.0.0.1:8080/api/status
```

### Expected Normal Result
- `/live` endpoints return `HTTP 200 OK`.
- `/api/status` returns `"service_running": true` and `"tunnel_ready": true`.

---

## 4. Failure Interpretation Guide

| Symptom | Probable Cause | Action |
|---|---|---|
| **Local check fails (`HTTP 000` / Connection Refused)** | Translation app is not running or port 8080 is blocked by another process. | Launch `SKC_translation.exe` or `python main.py` and confirm server startup logs. |
| **Local works, but Public fails** | `cloudflared` service is stopped, disabled, or unauthenticated. | Check Windows service: `sc query cloudflared`. Confirm status is `RUNNING` and dashboard shows `Healthy`. |
| **Public DNS fails (NXDOMAIN / Cannot resolve host)** | Published hostname route missing or DNS CNAME unassigned in Cloudflare. | Verify `live.starkvillekoreanchurch.org` in Cloudflare Zero Trust Dashboard → Public Hostnames. |
| **Public returns `502 Bad Gateway`** | `cloudflared` is running but cannot connect to local port 8080. | Verify local app is listening on `http://127.0.0.1:8080/live`. |
| **Public returns `404 Not Found`** | Path mapping error or request hit default catch-all route. | Confirm Public Hostname route path is correct (`/` or `/live`) and default catch-all is set to `http_status:404`. |

---

## 5. Protocol URL Scheme Rules

- **Public URL**: Must ALWAYS use **`HTTPS`** (`https://live.starkvillekoreanchurch.org/live`).
- **LAN / Local URL**: Uses **`HTTP`** (`http://skc-live.local:8080/live`).

---

## 6. Operational Checklists

### 📋 Before Sunday Service Checklist
1. Start the translation application (`SKC_translation.exe` or `python main.py`).
2. Verify local health: `curl -I http://127.0.0.1:8080/live` returns `HTTP 200`.
3. Verify local mDNS: `http://skc-live.local:8080/live` loads from a mobile phone connected to church Wi-Fi.
4. Verify public HTTPS: `curl -I https://live.starkvillekoreanchurch.org/live` returns `HTTP 200`.
5. Confirm Cloudflare Tunnel `skc-live-translation` status is `Healthy` in dashboard or via `sc query cloudflared`.

### 🔄 After PC Restart / Network Change Checklist
1. Confirm mDNS zeroconf registration is restored (`skc-live.local`).
2. Confirm `cloudflared` service automatically reconnects and tunnel status returns to `Healthy`.
3. Retest both local (`http://127.0.0.1:8080/live`) and public (`https://live.starkvillekoreanchurch.org/live`) URLs.

---

## 7. Attendee QR-Code Policy

To ensure optimal performance and user experience:

- 🏛️ **In-Person Sanctuary Attendees**:
  - Target URL: `http://skc-live.local:8080/live`
  - Purpose: Ultra-low latency (1–2ms TTFB) for live sanctuary audience on church Wi-Fi.
- 📺 **Remote / Cellular Stream Viewers**:
  - Target URL: `https://live.starkvillekoreanchurch.org/live`
  - Purpose: Public HTTPS security for YouTube stream viewers and mobile cellular devices.

> [!TIP]
> Always scan and test both QR codes using actual physical mobile phones (iOS & Android) before printing bulletin inserts or displaying slides.

---

## 8. Security Rules & Secret Isolation

- **Zero Secret Commits**: Never commit Cloudflare tunnel tokens, API keys, credentials JSON, `.env` files, or screenshots containing secrets to Git.
- **Token Placeholder Format**: Always use `<CLOUDFLARE_TUNNEL_TOKEN>` in documentation and sample code.
- **Topology Isolation**: Do not publish private IP addresses (`192.168.x.x`), Wi-Fi passwords, or router configuration details in public documentation.
- **Outbound Architecture**: Never recommend port forwarding; Cloudflare Tunnel establishes secure outbound tunnels to Cloudflare edge nodes.
