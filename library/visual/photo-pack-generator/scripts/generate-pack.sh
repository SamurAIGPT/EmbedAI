#!/bin/bash
# Photo Pack Generator Skill
# Generates a pack of images based on a reference image and a category.

IMAGE_URL=""
IMAGE_FILE=""
CATEGORY="LinkedIn"
MODEL="nano-banana-edit"
NUM_IMAGES=5
VIEW_FLAG_LOCAL=false
JSON_ONLY=false
LIKENESS=""

# Scripts directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
UPLOAD_SCRIPT="$SCRIPT_DIR/../../../../core/media/upload.sh"
GENERATE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"
OUTPUT_DIR="$SCRIPT_DIR/../../../../media_outputs"

while [[ $# -gt 0 ]]; do
    case $1 in
        --image-url) IMAGE_URL="$2"; shift 2 ;;
        --image) IMAGE_FILE="$2"; shift 2 ;;
        --category) CATEGORY="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --num) NUM_IMAGES="$2"; shift 2 ;;
        --view) VIEW_FLAG_LOCAL=true; shift ;;
        --json) JSON_ONLY=true; shift ;;
        --likeness) LIKENESS="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$IMAGE_URL" ] && [ -z "$IMAGE_FILE" ]; then
    echo "Error: Either --image-url or --image (local file) is required."
    exit 1
fi

# 1. Handle local file upload
if [ -n "$IMAGE_FILE" ]; then
    [ "$JSON_ONLY" = false ] && echo "Uploading local image: $IMAGE_FILE..." >&2
    IMAGE_URL=$(bash "$UPLOAD_SCRIPT" --file "$IMAGE_FILE")
    if [ -z "$IMAGE_URL" ]; then
        echo "Error: Upload failed."
        exit 1
    fi
    [ "$JSON_ONLY" = false ] && echo "Uploaded to: $IMAGE_URL" >&2
fi

# 2. Extract Prompts based on Category
# Improved prompts using "Perfect Prompt" formula: Subject + Action + Context + Composition + Lighting + Style
PROMPTS=()
SUBJECT="${LIKENESS:-the person}"
# Ultra-High Fidelity Likeness Prefix
LIKENESS_PREFIX="STRICTLY PRESERVE the EXACT facial geometry, PIXEL-PERFECT bone structure, and identical facial features from the reference image. Every detail of the eyes, nose, and jawline must remain unchanged. "

case $CATEGORY in
    "LinkedIn")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} high-resolution professional profile photo of ${SUBJECT}, chest up, looking directly at the camera with a confident smile. Wearing a premium navy business suit and white shirt. Solid neutral studio background. 85mm f/1.8 lens, exquisite focus on eyes, realistic skin texture, soft diffused studio lighting, cinematic corporate color grading."
            "Using reference image, ${LIKENESS_PREFIX} corporate headshot of ${SUBJECT}, professional business casual attire, clean soft gray studio backdrop. Sharp focus, natural skin tones, Rembrandt lighting, professional magazine quality portrait, 8k resolution."
            "Using reference image, ${LIKENESS_PREFIX} professional portrait of ${SUBJECT}, executive presence, wearing a charcoal blazer, modern glass office background blurred (bokeh). Soft daylight from side window, high-end commercial photography, meticulous detail, photorealistic."
            "Using reference image, ${LIKENESS_PREFIX} professional headshot of ${SUBJECT}, friendly and approachable expression, wearing a smart casual sweater. Clean minimalist office setting, bright and airy lighting, crisp detail, realistic textures, shot on Sony A7R IV."
            "Using reference image, ${LIKENESS_PREFIX} business portrait of ${SUBJECT}, suit refinement, clear sharp facial features, subtle studio lighting with hair light, polished and trustworthy expression, blurred professional environment."
        )
        ;;
    "Tinder"|"Bumble")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} natural high-resolution dating profile photo of ${SUBJECT} with a warm genuine smile. Relaxed posture, wearing a stylish knit sweater. Soft natural daylight, outdoor cafe setting with blurred background bokeh (f/1.4). Authentic friendly energy, realistic skin tones, 35mm film grain."
            "Using reference image, ${LIKENESS_PREFIX} candid dating app photo of ${SUBJECT} in a stylish leather jacket, leaning against a brick wall on a vibrant city street at dusk. Warm soft streetlights, engaging slightly mysterious expression, shallow depth of field, urban lifestyle aesthetic."
            "Using reference image, ${LIKENESS_PREFIX} lifestyle photo of ${SUBJECT} at a rooftop bar at sunset, holding a drink, city skyline bokeh. Warm string lights, casual style, natural expression, cinematic photography, Kodak Portra 400 colors."
            "Using reference image, ${LIKENESS_PREFIX} place ${SUBJECT} on a tropical beach at sunset, casual linen shirt, warm golden light. Adventurous confident vibe, travel lifestyle photo, high detail skin texture, Leica M11 look."
            "Using reference image, ${LIKENESS_PREFIX} cozy coffee shop interior, ${SUBJECT} holding a latte, casual homey outfit. Warm ambient lighting, relaxed genuine smile, lifestyle portrait, shallow depth of field, realistic textures."
        )
    ;;
    "OldMoney")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} transform to old money aesthetic. ${SUBJECT} wearing a tailored beige cashmere sweater and cream trousers, standing on a gravel path outside a British country estate. Overcast soft light, effortless elegance, Vogue editorial photography, 35mm film texture."
            "Using reference image, ${LIKENESS_PREFIX} portrait of ${SUBJECT} at an ivy league campus entrance, classic navy blazer over oxford shirt. Autumnal background with golden leaves, warm sunlight, understated confidence, preppy refined aesthetic, 85mm lens."
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} on a luxury yacht deck, wearing ivory linen co-ords and minimal gold jewelry. Mediterranean afternoon light, turquoise sea bokeh, windswept relaxed hair, aspirational quiet luxury lifestyle."
            "Using reference image, ${LIKENESS_PREFIX} equestrian setting, ${SUBJECT} in classic riding attire with leather boots. Green countryside estate background, golden hour light, European aristocracy feel, high-detail textures, equestrian chic."
            "Using reference image, ${LIKENESS_PREFIX} grand private library interior, ${SUBJECT} sitting in a leather armchair wearing a tweed blazer. Warm amber candlelight, sophisticated intellectual presence, timeless refined photography, cinematic lighting."
        )
        ;;
    "Cyberpunk")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} standing in a rain-soaked neon-lit Tokyo alley at night. Vibrant pink and blue neon reflections on the face and a sleek black tech-wear trench coat. Moody atmospheric lighting, dramatic shadows, wet pavement reflections, Blade Runner 2049 aesthetic."
            "Using reference image, ${LIKENESS_PREFIX} cyberpunk transformation. ${SUBJECT} with subtle glowing cybernetic implants, futuristic geometric fashion. Dark industrial background with volumetric fog, electric blue rim lighting, cinematic sci-fi atmosphere, 8k resolution."
            "Using reference image, ${LIKENESS_PREFIX} neon Shibuya crossing background. Massive screens glowing, crowd motion blur. ${SUBJECT} in futuristic street style, electric urban energy, cyan and magenta color grade, high-end concept art style."
            "Using reference image, ${LIKENESS_PREFIX} cyberpunk underground club. Neon UV lighting, haze and smoke, futuristic accessories. ${SUBJECT} has an intense look, immersive sci-fi nightlife atmosphere, sharp focus, vibrant color contrast."
        )
        ;;
    "CEO")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} modern glass office with city view background. ${SUBJECT} in a tailored premium power suit, confident pose. Dramatic side lighting, executive portrait, Fortune 500 magazine cover style, sharp focus, ultra-professional."
            "Using reference image, ${LIKENESS_PREFIX} tech founder aesthetic. ${SUBJECT} wearing a premium black turtleneck, minimalist white gallery background. Intellectual confident vibe, tech CEO energy, soft box lighting, high detail, realistic skin."
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} speaking on a stage at a tech conference, blurred audience in background. Business casual, engaged confident expression, thought leader energy, professional event photography lighting."
            "Using reference image, ${LIKENESS_PREFIX} modern boardroom setting, ${SUBJECT} at the head of the table. Natural window light, commanding presence, professional confidence, photorealistic, 8k detail."
            "Using reference image, ${LIKENESS_PREFIX} outdoor urban corporate setting. ${SUBJECT} in a walking pose, holding a smartphone, blurred skyscraper background. Dynamic editorial feel, natural daylight, sharp textures, high-quality professional lifestyle."
        )
        ;;
    "CleanGirl")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} transform to clean girl aesthetic. ${SUBJECT} with a slicked back bun, minimal natural makeup, white ribbed tank top. Soft window daylight, fresh dewy skin, minimalist gold jewelry, airy neutral tones, Pinterest aesthetic."
            "Using reference image, ${LIKENESS_PREFIX} morning routine aesthetic. White bathroom tiles background, glass of water. ${SUBJECT} with a fresh no-makeup look, soft diffused natural lighting, clean minimal vibe, high-resolution photography."
            "Using reference image, ${LIKENESS_PREFIX} minimalist home interior, neutral palette. ${SUBJECT} in casual elevated lounge wear, morning light, soft shadows, healthy radiant skin, effortless lifestyle portrait."
            "Using reference image, ${LIKENESS_PREFIX} pilates studio background, ${SUBJECT} in seamless neutral activewear. Fresh skin, minimal accessories, clean health-focused lifestyle, bright airy lighting, realistic texture."
        )
        ;;
    "DarkAcademia")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} old library with floor-to-ceiling bookshelves. ${SUBJECT} in a tweed blazer and turtleneck outfit, warm candlelight tones. Intellectual brooding expression, 35mm film grain, moody dark academia aesthetic."
            "Using reference image, ${LIKENESS_PREFIX} Oxford-style stone corridor, overcast autumn setting. ${SUBJECT} in a wool coat and scarf, leather book bag. Fallen leaves, moody atmospheric photography, cinematic film look."
            "Using reference image, ${LIKENESS_PREFIX} vintage study interior, rain-streaked window. ${SUBJECT} in a knit sweater, round glasses, contemplative expression. Dark earthy tones, warm lamp glow, literary atmospheric portrait."
            "Using reference image, ${LIKENESS_PREFIX} autumn botanical garden, ${SUBJECT} in a long wool coat. Moody overcast light, golden leaves, cinematic dark academia film photography, rich textures."
        )
        ;;
    "Anime")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} convert ${SUBJECT} to high-quality anime illustration. Studio Ghibli aesthetic, expressive large eyes, soft pastel watercolor colors, clean line art, magical atmosphere, masterpiece quality."
            "Using reference image, ${LIKENESS_PREFIX} transform ${SUBJECT} to a stylish shonen anime character. Dynamic pose, bold cinematic lines, vibrant colors, manga-inspired effects, high-detail digital art style."
            "Using reference image, ${LIKENESS_PREFIX} dark fantasy anime aesthetic for ${SUBJECT}. Dramatic lighting, detailed metallic armor, epic sky background, intricate design, professional anime key visual style."
            "Using reference image, ${LIKENESS_PREFIX} cute chibi character version of ${SUBJECT}. Super deformed proportions, big expressive eyes, pastel colors, kawaii expression, clean vector art style."
        )
        ;;
    "Doctor")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} add white lab coat and stethoscope to ${SUBJECT}. Clean hospital hallway blurred background, approachable professional smile, soft clean lighting, medical portrait, trustworthy vibe."
            "Using reference image, ${LIKENESS_PREFIX} place ${SUBJECT} in a modern clinic environment. Wearing scrubs, calm reassuring expression, clean professional lighting, high-resolution medical photography."
        )
        ;;
    "Lawyer")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} in a dark suit and tie, law office mahogany bookshelf background. Authoritative yet approachable expression, Rembrandt lighting, sharp professional portrait, legal magazine style."
            "Using reference image, ${LIKENESS_PREFIX} law firm lobby background, ${SUBJECT} in business formal attire. Commanding presence, professional confidence, cinematic lighting, sharp detail, 8k resolution."
        )
        ;;
    "MobWife")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} in an oversized faux fur coat, bold red lips, large dark sunglasses. Manhattan street background, high contrast dramatic lighting, editorial fashion, power pose, mob wife aesthetic."
            "Using reference image, ${LIKENESS_PREFIX} luxury restaurant background, ${SUBJECT} in a full fur coat, heavy gold jewelry. Statement makeup, cinematic dramatic lighting, old Hollywood glamour, rich textures."
        )
        ;;
    "Bali")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} iconic Bali rice terraces background at sunrise. ${SUBJECT} in a flowy linen outfit, golden hour lighting, lush tropical greens, travel influencer lifestyle photography."
            "Using reference image, ${LIKENESS_PREFIX} luxury infinite pool overlooking Ubud jungle. ${SUBJECT} in tropical swimwear, golden afternoon light, aspirational travel aesthetic, high-detail water and skin textures."
        )
        ;;
    "90s")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} 90s fashion transformation for ${SUBJECT}. Slip dress with cardigan, butterfly clips, choker. Faded film grain, warm nostalgic tones, 90s yearbook photo style."
            "Using reference image, ${LIKENESS_PREFIX} grunge 90s aesthetic for ${SUBJECT}. Plaid flannel shirt, ripped jeans, band t-shirt. Seattle back-alley backdrop, overcast moody light, authentic retro film texture."
        )
        ;;
    "Fitness")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} modern premium gym setting. ${SUBJECT} in performance activewear, dramatic studio lighting highlighting muscle definition. Strong confident pose, realistic sweat sheen, high-end fitness photography."
            "Using reference image, ${LIKENESS_PREFIX} outdoor running trail, ${SUBJECT} in athletic gear. Morning golden light, dynamic motion energy, healthy active lifestyle, sharp focus, realistic nature background."
        )
        ;;
    "Christmas")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} by a sparkling decorated Christmas tree. Cozy knit sweater, warm fireplace glow, snow falling outside window. Golden bokeh lights, festive holiday atmosphere, cinematic lighting."
        )
        ;;
    "Halloween")
        PROMPTS=(
            "Using reference image, ${LIKENESS_PREFIX} ${SUBJECT} in a haunted Victorian mansion background. Foggy atmosphere, elaborate vampire or witch costume, moody orange and purple lighting, cinematic horror aesthetic."
        )
        ;;
    *)
        echo "Error: Unknown category '$CATEGORY'."
        echo "Supported: LinkedIn, Tinder, Bumble, OldMoney, Cyberpunk, CEO, CleanGirl, DarkAcademia, Anime, Doctor, Lawyer, MobWife, Bali, 90s, Fitness, Christmas, Halloween"
        exit 1
        ;;
esac

# 3. Generate Pack
LIMIT=$((NUM_IMAGES > ${#PROMPTS[@]} ? ${#PROMPTS[@]} : NUM_IMAGES))
[ "$JSON_ONLY" = false ] && echo "Generating $LIMIT images for category: $CATEGORY using $MODEL..." >&2

RESULTS_JSON="[]"
LOCAL_FILES=()

for (( i=0; i<$LIMIT; i++ )); do
    PROMPT="${PROMPTS[$i]}"
    [ "$JSON_ONLY" = false ] && echo "Generating image $((i+1))/$LIMIT..." >&2
    
    # Run the generation script and capture JSON result
    # We don't pass --view to generate-image.sh here because we want to handle the batch opening at the end
    GEN_OUTPUT=$(bash "$GENERATE_SCRIPT" --prompt "$PROMPT" --model "$MODEL" --image-url "$IMAGE_URL" --json)
    
    # Append to our results array
    RESULTS_JSON=$(echo "$RESULTS_JSON" | jq ". + [$GEN_OUTPUT]")
    
    # 4. Handle Downloading for --view
    if [ "$VIEW_FLAG_LOCAL" = true ]; then
        URL=$(echo "$GEN_OUTPUT" | jq -r '.outputs[0]')
        EXT="${URL##*.}"
        [ -z "$EXT" ] || [[ "$EXT" == http* ]] && EXT="jpg"
        mkdir -p "$OUTPUT_DIR"
        TEMP_FILE="$OUTPUT_DIR/muapi_${CATEGORY}_$((i+1))_$(date +%s).$EXT"
        [ "$JSON_ONLY" = false ] && echo "Downloading $URL to $TEMP_FILE..." >&2
        curl -s -o "$TEMP_FILE" "$URL"
        LOCAL_FILES+=("$TEMP_FILE")
    fi
done

# 5. Final Output Formatting
if [ "$JSON_ONLY" = true ]; then
    echo "$RESULTS_JSON"
else
    echo ""
    echo "---"
    echo "## ✨ Your $CATEGORY Photo Pack is Ready!"
    echo "Total Images: $LIMIT"
    echo ""
    
    # Extract URLs for markdown display
    URLS=$(echo "$RESULTS_JSON" | jq -r '.[] | .outputs[0]')
    
    # Display as Markdown Images (Small Preview) then big links
    COUNTER=1
    for URL in $URLS; do
        echo "### Photo $COUNTER"
        echo "![$CATEGORY Photo $COUNTER]($URL)"
        echo "[View Full Size]($URL)"
        echo ""
        COUNTER=$((COUNTER + 1))
    done
    
    echo "---"
    echo "Files saved to MuAPI CDN. You can now use these links anywhere."
fi

# 6. Open all images at once if --view was requested (macOS only)
if [ "$VIEW_FLAG_LOCAL" = true ] && [[ "$OSTYPE" == "darwin"* ]]; then
    [ "$JSON_ONLY" = false ] && echo "Opening all generated images..." >&2
    open "${LOCAL_FILES[@]}"
fi
