# Clawdi Fork — Customization Guide

This fork of [ringhyacinth/Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI)
adds Clawdi branding, marine-life sprites, topic-bridge multi-agent support, and
Phala CVM compatibility fixes.

## How to Merge Upstream Updates

```bash
git fetch upstream
git merge upstream/master      # or: git rebase upstream/master
# Resolve conflicts using sections below as reference
```

If upstream has a major rewrite (like v1→v2), prefer hard-reset + re-patch:
```bash
git tag backup-pre-merge
git reset --hard upstream/master
# Then re-apply patches listed below
```

---

## Clawdi-Only Files (not in upstream — just keep them)

| File | Purpose |
|---|---|
| `bridge.py` | Polls OpenClaw logs → maps to pixel office states |
| `topic-bridge.py` | Syncs Telegram thread topics as multi-agent characters |
| `daily-memo-gen.py` | Generates daily memo content |
| `topics.sample.json` | Sample topics config for topic-bridge |
| `CLAWDI.md` | This file |

These files don't exist upstream, so merges won't touch them.

## Files Modified from Upstream

### 1. `.gitignore` — 1 line added
```
topics.json        # after the agents-state.json line
```

### 2. `backend/app.py` — Clawdi patches

**a) Branding (DEFAULT_AGENTS, ~line 307)**
```python
"agentId": "clawdi",        # upstream: "star"
"name": "Clawdi",           # upstream: "Star"
"detail": "Ready and standing by",  # upstream: "待命中，随时准备为你服务"
```

**b) FUSE compatibility — gemini python fallback (~line 34)**
```python
GEMINI_PYTHON_VENV = os.path.join(WORKSPACE_DIR, "skills", "gemini-image-generate", ".venv", "bin", "python")
try:
    _venv_ok = os.path.isfile(GEMINI_PYTHON_VENV) and os.access(GEMINI_PYTHON_VENV, os.X_OK)
except OSError:
    _venv_ok = False
GEMINI_PYTHON = GEMINI_PYTHON_VENV if _venv_ok else shutil.which("python3") or "python3"
```
Upstream has: `GEMINI_PYTHON = os.path.join(..., ".venv", "bin", "python")`

**c) Relaxed script existence check (~line 619)**
```python
if not os.path.exists(GEMINI_SCRIPT):   # upstream also checks GEMINI_PYTHON
```

**d) Model IDs — replace upstream internal codenames**
| Upstream | Clawdi |
|---|---|
| `nanobanana-pro` | `nano-banana-pro-preview` |
| `nanobanana-2` | `gemini-3.1-flash-image-preview` |

Search-replace all occurrences in app.py (appears ~6 times).

**e) Guest avatar pool extended to 10 (~lines 971, 992)**
```python
random.choice(["guest_role_1", ..., "guest_role_10"])  # upstream: 1-6
```

### 3. `frontend/index.html` — Clawdi patches

**a) Branding**
- `<title>Clawdi's Pixel Office</title>` (upstream: `Star 的像素办公室`)
- Loading text: `Loading Clawdi's pixel office...`
- I18N zh: `controlTitle: 'Clawdi Status'`, `officeTitle: 'Clawdi\'s Office'`, etc.
- I18N en: same pattern
- I18N ja: same pattern

**b) Model IDs** — same replacements as app.py (`nanobanana-pro` → `nano-banana-pro-preview`)

**c) Sprite frame size (preload section)**
```javascript
// upstream: frameWidth: 32, frameHeight: 32
this.load.spritesheet(`guest_anim_${_gi}`, ..., { frameWidth: 64, frameHeight: 64 });
```

**d) Sprite scale (guest sprite creation)**
```javascript
// upstream: setScale(4.0) for 32x32 frames
sprite = game.add.sprite(p.x, p.y, animKey).setOrigin(0.5, 1).setScale(2.0);
```

**e) Demo sprite fix**
```javascript
sprite = game.add.sprite(p.x, p.y, animKey, f).setOrigin(0.5, 1).setScale(2.0);
sprite.anims.play('guest_anim_1_idle', true);  // upstream plays 'guest_anim_1' (wrong key)
```

**f) IDLE_SPOTS + topic bridge integration**
- `getAreaPoint()` override with 9 fixed IDLE_SPOTS positions
- `_lastArea` tracking for sprite repositioning on state change
- Avatar parsing: `typeof agent.avatar === 'number'` instead of `parseInt()`
- Avatar range: `animIdx > 10`, `hash % 10` (upstream: 6)
- `GUEST_AVATARS` array extended to 10

### 4. `frontend/guest_anim_*.webp` — Marine life sprites

10 original marine-life character spritesheets replacing upstream's LimeZu assets:

| File | Creature | Frame size |
|---|---|---|
| `guest_anim_1.webp` | Lobster | 64x64 (256x128 sheet) |
| `guest_anim_2.webp` | Octopus | 64x64 |
| `guest_anim_3.webp` | Pufferfish | 64x64 |
| `guest_anim_4.webp` | Jellyfish | 64x64 |
| `guest_anim_5.webp` | Sea Turtle | 64x64 |
| `guest_anim_6.webp` | Seahorse | 64x64 |
| `guest_anim_7.webp` | Clownfish | 64x64 |
| `guest_anim_8.webp` | Crab | 64x64 |
| `guest_anim_9.webp` | Shark | 64x64 |
| `guest_anim_10.webp` | Starfish | 64x64 |

Upstream uses 32x32 LimeZu sprites (6 characters). Our sprites are 64x64 (10 characters).
Generator script: `/tmp/gen_marine_sprites.py` (PIL-based, zero external deps).

### 5. `topic-bridge.py` — Clawdi-specific changes

**a) Avatar cycle range**
```python
avatar = (avatar % 10) + 1   # upstream equivalent cycles 1-6
```

**b) Raw log file parsing (bypasses `openclaw logs` CLI)**
```python
# Reads /tmp/openclaw/openclaw-YYYY-MM-DD.log directly instead of
# subprocess.run(["openclaw", "logs", "--max-bytes", "12000"])
# because the CLI output is flooded with config warnings.
# Parses JSON lines, extracts "1" field for activity text.
```

**c) FUSE-compatible writes (no atomic rename)**
```python
# Direct writes instead of tmp→os.replace() which fails on FUSE
with open(AGENTS_FILE, "w") as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)
```

**d) Active topic state propagation**
```python
# Mirrors main agent state onto the active topic character
if active_topic and name and active_topic == name and main_state != "idle":
    state = main_state
```

---

## CVM Deployment Notes (Phala Cloud)

- **FUSE mount** at `/data/` — no symlinks, no shebangs, no inotify
- **Gunicorn** must use `--timeout 300` for image generation
- **runtime-config.json** (gitignored) must exist with Gemini API key
- **gemini-image-generate** skill lives at `/data/skills/gemini-image-generate/`
- **post-boot.sh** should install `pip3`, start gunicorn + topic-bridge
