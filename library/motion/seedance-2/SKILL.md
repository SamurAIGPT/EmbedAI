---
slug: muapi-seedance-2
name: muapi-seedance-2
version: "0.2.0"
description: Expert Cinema Director skill for Seedance 2.0 (ByteDance) — high-fidelity video generation using technical camera grammar and multimodal references. Supports text-to-video, image-to-video, video extension, beat-matching, dialogue, and e-commerce patterns.
acceptLicenseTerms: true
---

# 🎬 Seedance 2.0 Cinema Expert

**The definitive skill for "Director-Level" AI video orchestration.**
Seedance 2.0 is not a descriptive model; it is an *instructional* model. It responds best to technical cinematography, physics directives, and precise camera grammar.

## Core Competencies

1.  **Text-to-Video (t2v)**: Generate cinematic video from a Director Brief using `seedance-v2.0-t2v`.
2.  **Image-to-Video (i2v)**: Animate 1–9 reference images into a video using `seedance-v2.0-i2v`.
3.  **Video Extension (extend)**: Seamlessly continue an existing Seedance 2.0 video using `seedance-v2.0-extend`.
4.  **Multimodal Referencing**: Utilize `@tag` system (`@Image1`, `@Video1`, `@Audio1`) for style, motion, rhythm, and sound locking.
5.  **Audio-Visual Sync**: Native high-fidelity sound generation synchronized with visual motion.
6.  **Temporal Consistency**: Maintain character, clothing, and environment stability across shots.

---

## 📥 Input Limits

| Input Type | Limit | Formats | Max Size |
|:---|:---|:---|:---|
| Images | ≤ 9 | jpeg, png, webp, bmp, tiff, gif | 30 MB each |
| Videos | ≤ 3 | mp4, mov | 50 MB each, total duration 2–15s |
| Audio | ≤ 3 | mp3, wav | 15 MB each, total duration ≤ 15s |
| **Total files** | **≤ 12** | — | — |

**Output**: 4–15 seconds, auto-generated sound effects / background music, 480p–720p.

---

## ⚠️ Restrictions

- **No realistic human faces** in uploaded images/videos — the platform will block such uploads.
- `--mode extend` requires a `request_id` from a previous `seedance-v2.0-t2v` or `seedance-v2.0-i2v` job.
- Aspect ratios: `16:9`, `9:16`, `4:3`, `3:4`.
- Duration: 4–15 seconds.
- Quality: `basic` (faster) or `high` (higher fidelity).

---

## 🔗 Core Syntax: The @ Reference System

Assign explicit roles to each uploaded asset. This is the most critical part of multimodal prompting.

### Reference Tags
```
@Image1   @Image2   @Image3  ...  (up to 9)
@Video1   @Video2   @Video3       (up to 3)
@Audio1   @Audio2   @Audio3       (up to 3)
```

### Role Assignment Table

| Purpose | Example Syntax |
|:---|:---|
| First frame | `@Image1 as the first frame` |
| Last frame | `@Image2 as the last frame` |
| Character appearance | `@Image1's character as the subject` |
| Scene / background | `scene references @Image3` |
| Camera movement | `reference @Video1's camera movement` |
| Action / motion | `reference @Video1's action choreography` |
| Visual effects | `completely reference @Video1's effects and transitions` |
| Rhythm / tempo | `video rhythm references @Video1` |
| Voice / tone | `narration voice references @Video1` |
| Background music | `BGM references @Audio1` |
| Sound effects | `sound effects reference @Video3's audio` |
| Outfit / clothing | `wearing the outfit from @Image2` |
| Product appearance | `product details reference @Image3` |

### Multi-Reference Combination
```
@Image1's character as the subject, reference @Video1's camera movement
and action choreography, BGM references @Audio1, scene references @Image2
```

---

## 🏗️ Technical Specification: The Director Brief

Structure prompts using this hierarchy for professional results:

| Component | Instruction Type | Example |
|:---|:---|:---|
| **Scene** | Environment + Lighting | "A rain-soaked cyberpunk street, magenta neon reflections on wet asphalt." |
| **Subject** | Identity + Detail | "A woman in a black trenchcoat, determined focus, cinematic skin textures." |
| **Action** | Fluid Interaction | "Walking forward through the crowd, coat billowing slightly in the wind." |
| **Camera** | Movement + Lens | "Medium tracking shot, 35mm lens, slow dolly backward. Subtle handheld jitter." |
| **Style** | Mood + Intent | "Cinematic epic, warm color grade, shallow DOF, rack focus to subject's face." |

### Time-Segmented Prompts (Recommended for 10s+ videos)
Break prompts into timed segments for precise control:
```
0–3s: [opening scene, camera, action]
3–6s: [mid-section development]
6–10s: [climax or key action]
10–15s: [resolution, ending shot, final text/branding]
```

---

## 🎥 Camera Language Reference

### Basic Movements
| Term | Description |
|:---|:---|
| Push in / Slow push | Camera moves toward subject |
| Pull back / Pull away | Camera moves away from subject |
| Pan left/right | Camera rotates horizontally |
| Tilt up/down | Camera rotates vertically |
| Track / Follow shot | Camera follows subject movement |
| Orbit / Revolve | Camera circles around subject |
| One-take / Oner | Continuous shot with no cuts |

### Advanced Techniques
| Term | Description |
|:---|:---|
| Hitchcock zoom (dolly zoom) | Push in + zoom out — creates vertigo effect |
| Fisheye lens | Ultra-wide distorted lens |
| Low angle / High angle | Camera below/above subject |
| Bird's eye / Overhead | Top-down view |
| First-person POV | Subjective camera from character's eyes |
| Whip pan | Very fast horizontal pan with motion blur |
| Crane shot | Vertical movement like a crane arm |

### Shot Sizes
| Term | Description |
|:---|:---|
| Extreme close-up | Eyes, mouth, or small detail only |
| Close-up | Face fills frame |
| Medium close-up | Head and shoulders |
| Medium shot | Waist up |
| Full shot | Entire body |
| Wide / Establishing shot | Full environment |

---

## 🧠 Prompt Optimization Protocol

**The Agent MUST transform user intent into a technical "Director Brief" before execution.**

1.  **Technical Grammar**: Use camera terms: *Dolly In/Out, Crane Shot, Whip Pan, Tracking Shot, Anamorphic Lens, Shallow Depth of Field*.
2.  **Physics Directives**: Use "caustic patterns," "volumetric rays," or "subsurface scattering" instead of "good lighting."
3.  **Timecode Notation**: For multi-beat scenes, use `[00:00-00:05s]` format to specify timing.
4.  **Tag References**: If files provided, use: *"Replicate the camera movement of @Video1 while maintaining the visual style of @Image1."*
5.  **ORDER MATTERS**: Tokens at the start define composition; tokens at the end define texture and micro-motion.
6.  **Multi-Image i2v**: Provide up to 9 reference images. The model blends aspects (style, identity, environment) across all inputs.

---

## 🚀 Protocol: Using Seedance 2

### Mode 1: Text-to-Video (t2v)

```bash
# Epic reveal shot
bash scripts/generate-seedance.sh \
  --subject "a hidden temple in the Andes, mist rolling through the canopy" \
  --intent "epic" \
  --aspect "16:9" \
  --duration 10 \
  --quality high \
  --view

# Tense close-up, vertical for social
bash scripts/generate-seedance.sh \
  --subject "a detective examines a cryptic clue under harsh lamp light" \
  --intent "tense" \
  --aspect "9:16" \
  --duration 5
```

### Mode 2: Image-to-Video (i2v)

Animate one or more reference images. Up to 9 images can be supplied — the model synthesizes motion across all of them.

```bash
# Animate a single local image
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file hero.jpg \
  --subject "hero strides forward, coat billowing in slow motion" \
  --intent "epic" \
  --aspect "16:9" \
  --view

# Animate from a URL
bash scripts/generate-seedance.sh \
  --mode i2v \
  --image "https://example.com/scene.jpg" \
  --subject "camera slowly pulls back to reveal the full landscape" \
  --intent "reveal" \
  --duration 10

# Multi-image blending (character + environment + style reference)
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file character.jpg \
  --file environment.jpg \
  --image "https://example.com/style.jpg" \
  --subject "character walks through the environment in cinematic style" \
  --quality high

# With video and audio references (@-system)
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file character.jpg \
  --video-file reference_motion.mp4 \
  --audio-file bgm.mp3 \
  --subject "@Image1's character as the subject, reference @Video1's camera movement, BGM references @Audio1" \
  --quality high
```

### Mode 3: Extend Video

Continue an existing Seedance 2.0 video seamlessly, preserving visual style, motion, and audio.

```bash
# Extend with no new prompt (model continues naturally)
bash scripts/generate-seedance.sh \
  --mode extend \
  --request-id "abc-123-def-456" \
  --duration 10

# Extend with directional prompt
bash scripts/generate-seedance.sh \
  --mode extend \
  --request-id "abc-123-def-456" \
  --subject "camera continues to pull back, revealing the vast city below" \
  --intent "reveal" \
  --duration 10 \
  --quality high \
  --view
```

### Async Pattern (for long jobs)

```bash
# Submit and get request_id immediately
RESULT=$(bash scripts/generate-seedance.sh --mode i2v --file photo.jpg --async --json)
REQUEST_ID=$(echo "$RESULT" | jq -r '.request_id')

# Check later
bash ../../../../core/media/generate-video.sh --result "$REQUEST_ID"
```

---

## 🎭 Capability-Specific Patterns

### 1. Character Consistency
```
The man in @Image1 walks tiredly down the hallway, slowing his steps,
finally stopping at his front door. Close-up on his face — he takes a
deep breath, replaces the weariness with a relaxed expression.
Maintain high character consistency, zero facial flicker, persistent clothing details.
```

### 2. Camera Movement Replication
```
Reference @Image1's male character. He is in @Image2's elevator.
Completely reference @Video1's camera movements and facial expressions.
Hitchcock zoom during the fear moment, then orbit shots of the interior.
Elevator doors open, follow shot walking out.
```

### 3. Video Extension (Forward)
```
Extend @Video1 by 10 seconds.
1–5s: Light and shadow slowly slide across table through venetian blinds.
6–10s: A coffee bean drifts down. Camera pushes in toward it until screen goes black.
English text gradually appears — "Lucky Coffee", "Breakfast", "AM 7:00-10:00".
```

### 4. Video Extension (Reverse / Prepend)
```
Extend backward 10s. In warm afternoon light, the camera starts from
the corner with awning fluttering in the breeze, slowly tilting down
to flowers peeking out at the wall base, building anticipation for the main scene.
```

### 5. Video Editing (Modify Existing)
```
Subvert @Video1's plot — the character's expression shifts from warmth to
cold determination. The action is decisive, without hesitation.
Maintain all other visual elements (scene, lighting, timing).
```

### 6. Music Beat-Matching
```bash
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file img1.jpg --file img2.jpg --file img3.jpg \
  --video-file reference_edit.mp4 \
  --audio-file track.mp3 \
  --subject "@Image1 @Image2 @Image3 — match the keyframe positions and rhythm of @Video1 for beat-synced cuts. BGM references @Audio1. More dynamic movement, dreamlike visual style." \
  --duration 15 --quality high
```

### 7. Dialogue / Voice Acting
```
In the "Cat & Dog Roast Show" — emotionally expressive comedy segment:
Cat host (licking paw, rolling eyes): "Who understands my suffering?"
Dog host (head tilted, tail wagging): "You're one to talk? You sleep 18 hours a day..."
Sound: lively studio ambience, audience laughter, punchy transitions.
```

### 8. One-Take / Long Take
```
@Image1 @Image2 @Image3 — one-take tracking shot following a runner
from the street up stairs, through a corridor, onto a rooftop,
finally overlooking the city. No cuts throughout.
```

### 9. E-commerce / Product Showcase
```bash
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file product.jpg \
  --subject "Deconstruct the product. Static camera. Hamburger suspended mid-air, rotating slowly. Ingredients separate and reassemble. Cheese continues to melt and drip. Ultimate food aesthetics." \
  --intent "product" \
  --aspect "9:16" \
  --duration 15 --quality high
```

### 10. Science / Educational Visualization
```bash
bash scripts/generate-seedance.sh \
  --subject "15-second health educational clip. 0–5s: Transparent blue human upper body, camera pushes into a clear artery, blood flows smoothly. 5–10s: Sugar and fat particles enter bloodstream, lipid deposits form on vessel walls. 10–15s: Vessel narrows, before/after comparison. 4K medical CGI, semi-transparent visualization." \
  --intent "educational" \
  --duration 15 --quality high
```

---

## 🎨 Prompt Templates

### Cinematic Film
```
[SCENE] Rain-soaked cyberpunk alley, neon signs reflected on wet cobblestones.
[SUBJECT] A lone figure in a weathered trench coat, face obscured by a wide-brim hat.
[ACTION] Walking slowly, each step splashing neon color into the puddles.
[CAMERA] Low-angle tracking shot, anamorphic lens, slow dolly in. Rack focus to face.
[STYLE] Denis Villeneuve aesthetic, high contrast, desaturated blues and magentas. 24fps.
```

### Product Ad (15s)
```
Reference @Video1's editing style. Replace @Video1's product with @Image1 as hero.
0–3s: Product enters with dynamic rotation, close-up on surface texture and logo.
4–8s: Multiple angle transitions — front, side, back — with highlight scanning light.
9–12s: Product in lifestyle context showing usage.
13–15s: Hero shot with brand tagline, background music builds to resolution.
Sound: Reference @Video1's BGM. Add product interaction sound effects.
```

### Short Drama (15s)
```
Scene (0–5s): Close-up on character's reddened eyes, finger pointing accusingly.
Dialogue 1: "What exactly are you trying to take from me?"
Scene (6–10s): Other character trembles, holding up evidence, steps forward.
Dialogue 2: "I'm not deceiving you! This is what he entrusted to me!"
Scene (11–15s): Evidence revealed, first character freezes — anger shifts to shock.
Sound: Urgent piano + static interference, sobbing, muffled voice blending in.
Duration: Precise 15 seconds, every frame tight, no filler.
```

### Dance / Beat-Sync (13s)
```
Have the character in @Image1 replicate the dance moves and beat-synced
music from @Video1. Generate a 13-second video. Movements should be
smooth with no stuttering or freezing.
```

### Scenery Montage (15s)
```
@Image1 @Image2 @Image3 @Image4 @Image5 @Image6 — landscape scene images.
Reference @Video1's visual rhythm, inter-scene transitions, visual style,
and music tempo for beat-synced editing.
```

### Advertising / Product Motion
```
[SCENE] Minimalist white studio, single product on a rotating pedestal.
[ACTION] Subtle 360° rotation, product details catching specular highlights.
[CAMERA] Tight medium shot, macro lens pass over surface texture, slow orbit.
[STYLE] Commercial grade, perfect exposure, zero background distraction.
```

### Action / Physics
```
[SCENE] Desert canyon at sunrise, sandy terrain, long shadows.
[SUBJECT] High-performance sports car accelerating through a turn.
[ACTION] Rear wheels spinning with dust plume, chassis flexing under g-force.
[CAMERA] Low hero angle dolly tracking alongside, then whip pan to lead car.
[STYLE] Hollywood racing film, warm golden grade, motion blur on wheels. 24fps.
```

### Character Consistency (Martial Arts)
```
[SUBJECT] Same fighter throughout: young woman, white gi, black belt, determined expression.
[ACTION] Fluid kata sequence — rising block, stepping side kick, spinning back fist.
[CAMERA] Full-body wide shot, then cut to close-up of fist impact in slow motion.
[STYLE] Maintain identical lighting, clothing, and facial features in every frame. Zero flicker.
```

---

## 🎚️ Style & Quality Modifiers

### Visual Style
- `Cinematic quality, film grain, shallow depth of field`
- `2.35:1 widescreen, 24fps`
- `Ink wash painting style` / `Anime style` / `Photorealistic`
- `High saturation neon colors, cool-warm contrast`
- `4K medical CGI, semi-transparent visualization`

### Mood / Atmosphere
- `Tense and suspenseful` / `Warm and healing` / `Epic and grand`
- `Comedy with exaggerated expressions`
- `Documentary tone, restrained narration`

### Audio Direction
- `Background music: grand and majestic`
- `Sound effects: footsteps, crowd noise, car sounds`
- `Voice tone reference @Video1`
- `Beat-synced transitions matching music rhythm`

---

## ❌ Common Mistakes to Avoid

1. **Vague references**: Don't say "reference @Video1" — specify WHAT to reference (camera? action? effects? rhythm?)
2. **Conflicting instructions**: Don't ask for "static camera" and "orbit shot" in the same segment.
3. **Overloading**: Don't pack too many scenes into 4–5 seconds — keep it physically plausible.
4. **Missing @ assignments**: If you upload 5 images, make sure each one is referenced with a clear purpose.
5. **Ignoring audio**: Sound design dramatically improves output — always include audio direction.
6. **Forgetting duration**: Match prompt complexity to the selected generation length.
7. **Real faces**: Don't upload real human photos — the system will block them.
8. **Keyword soup**: DO NOT use "8k, masterpiece, trending." Use technical descriptions instead.
9. **Discontinuous action**: Avoid "The man runs and then he stops." Use fluid transitional language.

---

## ⚙️ Implementation Details

| Model | Endpoint | Use Case |
|:---|:---|:---|
| `seedance-v2.0-t2v` | Text-to-Video | Generate from Director Brief |
| `seedance-v2.0-i2v` | Image-to-Video | Animate 1–9 reference images + video/audio refs |
| `seedance-v2.0-extend` | Extend Video | Continue a v2.0 generated video |

This skill acts as a **Cinematographic Wrapper** that translates low-level creative intent into high-fidelity technical instructions for the `muapi` core.
