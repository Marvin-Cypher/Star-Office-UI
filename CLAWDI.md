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
| `daily-memo-gen.py` | Generates daily memo (runs 4x/day, accepts optional date arg) |
| `topics.sample.json` | Sample topics config for topic-bridge |
| `CLAWDI.md` | This file |

These files don't exist upstream, so merges won't touch them.

## Files Modified from Upstream

### 1. `.gitignore` — 1 line added
```
topics.json        # after the agents-state.json line
```

### 2. `backend/app.py` — Clawdi patches

**a) Branding (DEFAULT_AGENTS)**
```python
"agentId": "clawdi",        # upstream: "star"
"name": "Clawdi",           # upstream: "Star"
"detail": "Ready and standing by",  # upstream: "待命中，随时准备为你服务"
```

**b) FUSE compatibility — gemini python fallback**
```python
GEMINI_PYTHON_VENV = os.path.join(WORKSPACE_DIR, "skills", "gemini-image-generate", ".venv", "bin", "python")
try:
    _venv_ok = os.path.isfile(GEMINI_PYTHON_VENV) and os.access(GEMINI_PYTHON_VENV, os.X_OK)
except OSError:
    _venv_ok = False
GEMINI_PYTHON = GEMINI_PYTHON_VENV if _venv_ok else shutil.which("python3") or "python3"
```
Upstream has: `GEMINI_PYTHON = os.path.join(..., ".venv", "bin", "python")`

**c) Relaxed script existence check**
```python
if not os.path.exists(GEMINI_SCRIPT):   # upstream also checks GEMINI_PYTHON
```

**d) MEMORY_DIR — env var override for CVM deployment**
```python
MEMORY_DIR = os.environ.get("STAR_OFFICE_MEMORY_DIR") or os.path.join(os.path.dirname(ROOT_DIR), "memory")
```
On CVM, set `STAR_OFFICE_MEMORY_DIR=/data/openclaw/workspace/memory`

**e) Guest avatar pool extended to 10**
```python
random.choice(["guest_role_1", ..., "guest_role_10"])  # upstream: 1-6
```

Note: Upstream uses `nanobanana-pro`/`nanobanana-2` canonical model names with mapping tables.
We keep upstream's model naming system as-is (no custom model ID overrides needed).

### 3. `frontend/index.html` — Clawdi patches

**a) Branding**
- `<title>Clawdi's Pixel Office</title>` (upstream: `Star 的像素办公室`)
- Loading text: `Loading Clawdi's pixel office...`
- I18N zh/en/ja: `controlTitle`, `officeTitle`, `loadingOffice` all "Clawdi"

**b) Sprite preload (64x64, 10 characters)**
```javascript
// upstream: 6 individual loads at 32x32
for (let _gi = 1; _gi <= 10; _gi++) {
    this.load.spritesheet(`guest_anim_${_gi}`, ..., { frameWidth: 64, frameHeight: 64 });
}
```

**c) Animation creation loop — 10 instead of 6**
```javascript
for (let i = 1; i <= 10; i++) { ... }
```

**d) Sprite scale for guest agents**
```javascript
// upstream: setScale(4.0) for 32x32 frames
sprite = game.add.sprite(p.x, p.y, animKey).setOrigin(0.5, 1).setScale(2.0);
```

**e) Demo sprite fix**
```javascript
sprite.anims.play('guest_anim_1_idle', true);  // upstream plays 'guest_anim_1' (wrong key)
```

**f) Avatar parsing and range**
- `typeof agent.avatar === 'number'` instead of `parseInt()` regex
- Range: `animIdx > 10`, `hash % 10` (upstream: 6)
- `GUEST_AVATARS` array extended to 10

**g) Asset descriptions** — extended to include guest_anim_7 through 10 in all 3 languages

### 4. `frontend/game.js` — Clawdi patches

**a) AREA_POSITIONS breakroom — custom positions**
```javascript
breakroom: [
    { x: 540, y: 310 },   // living room, near sofa left
    { x: 720, y: 350 },   // living room, center
    { x: 810, y: 290 },   // living room, near sofa right
    { x: 630, y: 430 },   // living room, near desk
    { x: 900, y: 520 },   // bedroom, entry
    { x: 1150, y: 420 },  // bedroom, by nightstand
    { x: 950, y: 600 }    // bedroom floor
],
```
Upstream has 8 positions clustered near the sofa. Ours spread across the full room.

**b) renderAgent() — animated sprites instead of emoji**
Upstream uses `game.add.text(0, 0, '⭐', ...)` in a container.
Our fork uses `game.add.sprite(baseX, baseY, animKey).setScale(2.0)` with animated marine creatures.
- Hash-based avatar selection (1-10 range)
- `_lastArea` tracking to only move sprite when area changes
- Separate `sprite` + `nameTag` objects instead of container

**c) fetchAgents() cleanup — adapted for sprite objects**
```javascript
if (agents[id].sprite) agents[id].sprite.destroy();
if (agents[id].nameTag) agents[id].nameTag.destroy();
```
Upstream calls `agents[id].destroy()` (container method).

### 5. `frontend/guest_anim_*.webp` — Marine life sprites

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

### 6. `topic-bridge.py` — Clawdi-specific changes

**a) Avatar cycle range**
```python
avatar = (avatar % 10) + 1   # upstream equivalent cycles 1-6
```

**b) Raw log file parsing (bypasses `openclaw logs` CLI)**

**c) FUSE-compatible writes (no atomic rename)**

**d) Active topic state propagation**

---

## CVM Deployment Notes (Phala Cloud)

- **FUSE mount** at `/data/` — no symlinks, no shebangs, no inotify
- **Gunicorn** must use `--timeout 300` for image generation
- **runtime-config.json** (gitignored) must exist with Gemini API key
- **gemini-image-generate** skill lives at `/data/skills/gemini-image-generate/`
- **post-boot.sh** should install `pip3`, start gunicorn + topic-bridge
- **STAR_OFFICE_MEMORY_DIR** env var should point to `/data/openclaw/workspace/memory`
