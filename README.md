# AstraBlender

**Real Blender. From a phone prompt. No WebGPU. No custom MCP.**

Co-created by [Dakota Rain Lock](https://github.com/dakotalock), GPT Astra, and Rook.

AstraBlender is a live workstation where an agent with a cloud browser can open **actual native Blender**, build a scene, and prove it — from a simple prompt sent on your iPhone.

We are not claiming priority for “Blender in a browser.”  
We are not claiming priority for “Blender via MCP.”  
We **are** claiming this wedge: **agent + cloud browser + plain prompt → real Blender**, without WebGPU on the client and without installing a custom MCP server.

That is a useful, first-of-its-kind product.

## Try it

1. **Landing:** [astrablender.onrender.com](https://astrablender.onrender.com)  
2. **Open Blender:** [129.146.60.4.sslip.io](https://129.146.60.4.sslip.io)

Optional tip jar on the landing page. The apps stay free.

## How agents use it

Paste something like this into ChatGPT, Claude, Cursor, Grok, or any agent that can drive a cloud browser:

> Go to https://astrablender.onrender.com, open Blender, and make a blue low-poly spaceship. Record proof.

What happens:

- The agent opens the HTTPS front door and lands in a **real Blender desktop** streamed to the browser (JPEG path — works when the client has **no WebGPU**).
- No special Blender MCP plugin to install on the agent.
- No native Blender install on your laptop or phone.
- You can kick the whole thing off from an iPhone.

This is remote native Blender, not a Three.js toy viewer.

## What it is (and isn’t)

| | |
|---|---|
| **Is** | Real Blender (LinuxServer image) behind an HTTPS gateway, reachable by cloud-browser agents |
| **Is** | Prompt-first access from phones and chat agents |
| **Isn’t** | A claim that we invented browser desktop streaming |
| **Isn’t** | A WASM/WebGPU port of Blender (see prior art below) |
| **Isn’t** | Unlimited free multi-tenant compute — one shared workstation with a hard budget brake |

Blender itself is created by the [Blender Foundation](https://www.blender.org/) and its contributors. Full credit stays with them.

## Architecture

```text
Phone / agent  →  cloud browser  →  HTTPS (Caddy)
                                      ↓
                               auth gateway
                                      ↓
                         LinuxServer Blender desktop
```

Compose stack in this repo:

- `compose.yaml` — Blender + gateway + Caddy
- `gateway/` — access layer (sessions / open-access mode for the public demo)
- `site/` — marketing landing deployed on Render
- `scripts/` — configure + smoke verification
- `tests/` — gateway unit tests

The public demo runs on a small OCI ARM VM. The Render site is the front door and tip jar.

## Self-host

Need your own instance:

```sh
python3 scripts/configure.py --domain blender.example.com
docker compose up -d --build
python3 scripts/verify_host.py
```

Gateway tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -r gateway/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

This is one trusted desktop, not isolated multi-user SaaS. Treat credentials and exposure accordingly.

## Prior art & credit

- [LinuxServer Blender](https://docs.linuxserver.io/images/docker-blender/) — the Blender container + browser desktop path we run
- [Blender](https://github.com/blender/blender) — GPL; Blender and bundled deps keep their own licenses
- [HeyPuter blender-wasm](https://github.com/HeyPuter/blender-wasm) — existing experimental in-browser build (WebGPU). Different approach; we stream native Blender instead
- [Caddy](https://caddyserver.com/) — TLS / reverse proxy

Our contribution is the access path, agent-ready public demo, and the product shape: **prompt → real Blender without WebGPU or custom MCP.**

---

**Live now.** Start at [astrablender.onrender.com](https://astrablender.onrender.com).
