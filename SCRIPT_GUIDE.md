# Smart Village Boy script standard

How to write a script for the AI Video Editor (`make_video.py`). A script written to this standard renders into a narrated,
captioned horizontal video and a vertical Short with no hand fixes. Every rule here matches what the tool's parser and
`--check` actually do. The example at the end is `Scripts/TEMPLATE.txt`, and a test checks that it still passes `--check`
with no flags, so the guide stays accurate when the tool changes.

**Deliver:** one plain-text `.txt` script in the format below, plus the fact-check notes from section 5 as a separate list.
Don't add anything else to the script file: no Markdown, no bold, no bullet lists, no "Narration:" labels.


## 1. The format

A script is plain text. **Each paragraph is one scene**, and paragraphs are separated by one blank line. In each scene, every
line that doesn't start with `#` is spoken aloud and captioned, word for word.

### 1.1 Header block (first paragraph, comments only)

```text
# Five Village Water Hacks That Beat Drought
# note: anything for the editor; never spoken
# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile
```

- **Line 1, the title:** `#`, a space, then the working title in Title Case, at least 12 characters, with no `word:` label in
  front. If the AI title writer is unavailable, this becomes the YouTube title.
- **Comments:** any other `#` line is a comment for the editor (see 1.8).
- **Characters:** `# character:` lines (see 1.2).
- The header block must contain **no spoken lines**. A paragraph made only of `#` lines doesn't become a scene. Put a blank line
  after it.

### 1.2 `# character:` lines: invented characters only

```text
# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile
```

- The format is `# character: Name = description`. The tool also accepts `:` or ` - ` between the name and the description. With
  none of the three, the line is skipped as a comment and `--check` flags it.
- **Only invented characters, never real people.** A real person the story is about is named in the narration and described by
  role in the Visuals lines (see 2.4).
- The description is for the image model: age, origin, build, hair, clothing and colours. Use short, concrete words ("red cap,
  yellow T-shirt"), not impressions ("cheerful-looking").
- **Use the exact same line in every script.** The channel host is always:
  `# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile`
- A scene shows a character only when its narration or Visuals line uses the character's name. Write "Smart Village Boy" in
  full in the Visuals line of every scene he appears in, and never in a scene he shouldn't be in.
- Scenes that name a declared character always get an AI image, never stock footage, so the character stays consistent.

### 1.3 `# Visuals:` lines: what each scene shows

```text
# Visuals: Two Kenyan women filling yellow jerrycans from a hand pump beside the sand-filled riverbed on a hot afternoon.
```

- Give every scene one `# Visuals:` line, inside the paragraph, before the narration. It's never spoken, but it's the main input
  for that scene's image prompt and stock search.
- The tool recognises a Visuals line by the word `Visuals:`, `Visual:`, `Shot:` or `Description:` anywhere after the `#`. Never
  use those words followed by a colon in a plain comment, or that comment becomes the scene's description.
- Rules for writing Visuals lines are in section 2.2.

### 1.4 Section headings: YouTube chapters

```text
# HOOK - Visuals: Smart Village Boy kneeling on a cracked, dry riverbed ...
# HACK 1 KENYA - Visuals: A wide seasonal riverbed of pale sand in Kitui, Kenya ...
# OUTRO - Visuals: Smart Village Boy sitting beside the wooden globe ...
```

- **Format:** a heading is an ALL-CAPS label right after the `#`, followed by ` - Visuals: ...`, a colon, or the end of the line.
  Combine it with the scene's Visuals line as shown.
- **Characters:** capital letters, digits, spaces and `& ' / ,` only. At least 3 characters, and not starting with NOTE, TODO,
  FIXME or TIP.
- **Scope:** a heading applies to its scene and every later scene until the next heading.
- **Chapter names:** `HACK 1 KENYA` becomes the chapter "Hack 1: Kenya", and `HACK 1` and `HACK 1 KENYA` count as the same
  chapter.
- **YouTube's rules:** chapters only show if the first is at 0:00, there are at least 3, and each is at least 10 seconds long.
  So start scene 1 with `# HOOK`, and give every section at least 2 scenes. Shorter sections are merged into the previous one.
- **Standard sections:** HOOK, HACK 1 to HACK 5 (each with its place), then OUTRO.

### 1.5 `[video]`: action scenes

```text
[video] When the rains come, the floodwater carries sand that piles up behind the wall.
```

- `[video]` at the very start of a narration line gives that scene a stronger camera move. It's never spoken.
- It doesn't make a video clip. Use it on the 4–5 most dynamic scenes: water rushing, a reveal, a bridge being crossed.
- Tags only work at the start of a line. A bracket anywhere else is read aloud and captioned, and `--check` flags it. Never write
  `[pause]`, `[music]`, `[B-roll]` and so on.

### 1.6 `# media:`: force stock or AI for one scene

```text
# media: stock
# media: ai
```

- By default, scenes use free stock footage (Pexels, Pixabay) when a clip matches well, and an AI image otherwise.
- Write exactly `# media: stock` or `# media: ai` on its own line in the paragraph, with nothing after it.
- **`# media: stock`:** for generic real places (a desert plain, a city street, a coastline) where real footage looks best.
- **`# media: ai`:** for shots stock can't match (inside a tunnel, a particular invention, a specific moment).
- Most scenes need neither. Use them sparingly.

### 1.7 `# location:`: place labels and the map

```text
# location: Kitui, Kenya
# location: none
```

- The video shows a place label and a world-map pin when the narration reaches a new place. The tool finds places by itself
  when the narration names a country ("Burkina Faso", "Iran"), a direction plus a country ("northern Nigeria"), or a known
  region ("Meghalaya", "Ladakh").
- Add `# location: Place, Country` only when the label would be missing or wrong (for example, a town the narration doesn't
  name). `# location: none` removes a wrong label.
- Name the country or region in the narration of each hack's first scene, so the label appears where the hack starts.

### 1.8 Comments

- Any other line starting with `#` is a comment: never spoken, captioned or sent to the image model. It can be in the header
  block or inside a scene.
- `--check` lists every comment it skipped. Use `# note:` for editor notes.

### 1.9 Things the tool doesn't support

Don't write these into a script; the tool can't do them:
- on-screen text or captions you write yourself
- sound effects or music cues
- split screens or picture-in-picture
- your own timing
- several images per scene

A scene is exactly one picture (or one stock clip) held for as long as its narration lasts.


## 2. Writing rules

### 2.1 Narration
- **One scene per paragraph, 2–3 spoken sentences, about 20–35 words.** `--check` flags a scene over 60 words, or one with more
  than 3 sentences once it passes 40 words. Short punchy fragments are fine: "No fridge. No power. No problem."
- **One idea per scene.** When the idea changes, start a new paragraph, because the picture changes with it.
- **Write for the ear:**
  - Use short sentences and everyday words.
  - Write numbers the way they should be said where a voice could stumble ("40 to 60 watt", "two thousand litres").
  - Don't use abbreviations with full stops ("Dr.", "St.", "e.g."). The tool counts every full stop as the end of a sentence.
- **Spell names the same way every time.** Captions use the script's spelling.
- **Narrator voice:** the channel ("we", "you"), not the boy speaking. Smart Village Boy appears on screen; he doesn't narrate.
- **Keep descriptions out of the narration.** "At sunrise on a dusty hilltop, a boy in a red cap..." would be read aloud. Put it
  on the Visuals line.

### 2.2 Visuals lines
- **One picturable moment.** One place, one time, one action, the way a single photograph would show it. Not a sequence ("he
  digs, then plants, then waters"). Choose the one moment that best shows the narration.
- **Say who, what, where and when:**
  - the people, with their role, rough age and clothing
  - the main object
  - the place, with its region and country
  - the time of day or light ("at sunrise", "on a hot afternoon")
- **Simple, clear poses for people:** standing, kneeling, walking, pointing, holding one object, or lifting something with both
  hands. Avoid complex hand work, interlaced fingers, crowds of hands, and several people touching the same object. For detail
  shots of a technique, write "Close view of a hand ..." with one hand and one object.
- **No text of any kind:** no signs, labels, logos, brand names, writing on objects, screens, books, maps, diagrams or arrows.
  Image models garble text.
- **No montages, split screens, before-and-after shots, time-lapses, slow motion or "fast cuts".** Also no camera directions
  ("pan across", "zoom in"). The tool adds the camera motion itself.
- **No impossible or cutaway views,** such as roots seen through soil or water inside a pipe. Show what a camera could see.
- **Keep a recurring place consistent.** Reuse the same words for a setting that appears again ("the Kenyan hilltop", "the same
  schoolyard"). Describe a recurring real person the same way each time.

### 2.3 Consistency
- Each character's name is identical everywhere: the `# character:` line, the narration and the Visuals lines.
- Repeat the same descriptive words for the same person, place and object across scenes.
- A section heading's place matches the narration ("HACK 2 BURKINA FASO" with "In Burkina Faso, ...").

### 2.4 Real people
- **Narration:** name real people when the story needs it ("a farmer named Yacouba Sawadogo"), with facts you can source.
- **`# character:` lines:** never declare a real person.
- **Visuals lines:**
  - Describe a real person by role, rough age and simple clothing ("an elderly farmer in a long robe and a knitted cap").
  - Don't describe their face or claim a likeness.
  - Prefer shots of their work, their hands or the invention.
- **Dignity:** never put words in a real person's mouth, and never show them in a way that could embarrass them.


## 3. Channel style: Smart Village Boy

The channel visits the world's villages for clever, low-cost ideas, presented with curiosity and respect.

- **Hook (scenes 1–2):**
  - Scene 1 opens on a vivid problem or a surprising fact in the first sentence, with no greeting before it.
  - Scene 2 welcomes viewers to Smart Village Boy and opens a loop: "Stay to the end for ...", a payoff that really comes last.
- **Curiosity:**
  - Each hack follows **place and problem → the idea → how it works → the result**, usually over 2–3 scenes.
  - Explain the "why" in plain words: evaporation cools, gravity carries water, sand stops evaporation.
  - Ask the question the viewer is about to ask, then answer it.
- **Respect:**
  - Show people as skilled and inventive, never as poor, helpless or exotic.
  - Use the community's own names (Khasi, Jaintia, Ladakhi) and correct regional clothing and buildings.
  - Don't talk about "primitive" or "simple" people, and don't describe a culture by comparing it with the West.
- **Accuracy:**
  - Every place, name, number, date and "how it works" claim must be checkable (section 5).
  - When sources disagree, use the cautious wording: "hundreds of thousands", "for centuries", "can last weeks".
  - Never give health or safety advice beyond what sources support.
- **Ending (last 2 scenes):**
  - A one-line recap of all the hacks.
  - Then a call to action with a question viewers can answer in the comments ("Which of these would work where you live?") and
    "subscribe".
  - Smart Village Boy appears in the hook and the ending, on the same Kenyan hilltop with the wooden globe.
- **Tone:** warm, curious and clear. Not hype: no "INSANE", no "you won't believe", no exclamation marks in a row.


## 4. Length and cost

**Speed.** The default voice speaks about 150–160 words a minute at the channel's +5% speed, plus a short pause after every
scene. (This speed is still an estimate until a full narration is measured; `--check` uses the current figure.)

| Video length | Scenes | Spoken words |
|---|---|---|
| 1 minute | 5–6 | about 150 |
| 2 minutes | 11–12 | about 300 |
| **Standard: just under 3 minutes** | **15–17** | **400–430** |

- **The standard video is 15–17 scenes and 400–430 words,** so that `--check` estimates 2:50 or less.
- **Hard limit for the vertical video:** the vertical video is the same length as the horizontal one, plus about 6 seconds of
  intro and outro. YouTube Shorts must be 3 minutes or less. `--check` notes anything outside 15–20 scenes.

**Cloudflare cost.** Images are free up to 10,000 Cloudflare "neurons" a day. Each new scene costs about **390 neurons**:

| Item | Neurons |
|---|---|
| Image prompt | ~46 |
| Two images (horizontal and vertical) | ~264 |
| Two quality checks | ~81 |
| YouTube titles and description, once per video | ~70 |

So a first render costs about:

| Scenes | Neurons |
|---|---|
| 12 | ~4,800 |
| 16 | ~6,300 |
| 20 | ~7,900 |
| 25 | ~9,800, too close to the limit |

- **Redraws:** each image that fails the quality check and is redrawn adds ~173 neurons.
- **Stock:** scenes that end up using stock footage don't need AI images, so they cost almost nothing.
- **Reruns:** cached scenes are free, so changing one scene's narration or Visuals line only costs that scene.
- **Keep it under 20 scenes,** so one day's allowance covers a first render plus a few redraws.


## 5. Fact-check checklist

Before handing a script over, check every claim against **1–2 independent, reliable sources**: official sites, established news,
NGOs running the project, academic or UNESCO pages. A YouTube video or another script doesn't count. List each claim with its
source links and a verdict (confirmed / unclear / unsupported) in a separate note, not in the script.

- [ ] **Places:** every country, region, town and community name is real, spelled correctly, and is where the hack happens.
- [ ] **People:** names, roles and nationalities are correct, and the person really did what the script says (invented, revived
      or popularised is not the same).
- [ ] **Numbers:** quantities, sizes, dates, durations and "how many homes" figures match a source. Round down, or write "about"
      or "up to".
- [ ] **How it works:** the physical explanation is correct and simple, with no invented mechanism.
- [ ] **Results:** claims of what the hack achieves are supported, with no "solves", "ends" or "everyone" unless sourced.
- [ ] **Health and safety:** any water, food, fire, chemical or building claim is supported and doesn't encourage anything
      dangerous (for example, drinking untreated water).
- [ ] **Culture:** community names, clothing, buildings and practices are described correctly and respectfully.
- [ ] **Still true:** projects still running, records still standing, people still living (check recent news).
- [ ] **Soften what can't be confirmed:** rewrite it with cautious wording, or cut it.

In the editor's project, the fact-check skill writes `Research/<script>_factcheck.md` the same way. Run it before a full render.


## 6. Example script (`Scripts/TEMPLATE.txt`)

This exact text is in `Scripts/TEMPLATE.txt` and passes `--check` with no flags or notes: 16 scenes, about 430 words, about 2:49,
4 `[video]` scenes, 7 chapters and about 6,200 neurons. The facts were chosen from well-documented examples, but **fact-check
them (section 5) before using this script for a real video**.

```text
# Five Village Water Hacks That Beat Drought
# note: Example script from SCRIPT_GUIDE.md. Copy it, keep the structure, replace the content.
# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile

# HOOK - Visuals: Smart Village Boy kneeling on a cracked, dry riverbed in rural Kenya at sunrise, holding up a handful of damp sand in one open palm.
[video] This river has been dry for months. Yet dig into the sand and you find water. Today we visit five villages that have learned to catch every drop.

# Visuals: Smart Village Boy standing beside a large wooden globe on a Kenyan hilltop at sunrise, pointing at one of five small blue pins stuck into it.
Welcome to Smart Village Boy, where we explore the cleverest ideas from the world's villages. Stay to the end for a pot that waters plants by itself.

# HACK 1 KENYA - Visuals: A wide seasonal riverbed of pale sand in Kitui, Kenya, in the dry season, with a low concrete wall built straight across it and acacia trees on the banks.
# location: Kitui, Kenya
In the dry hills of eastern Kenya, many rivers flow for only a few weeks a year. So villagers build a low concrete wall straight across the riverbed.

# Visuals: Brown floodwater pouring over the low concrete wall in the Kenyan riverbed during the rainy season, with wet sand piled up behind the wall.
[video] When the rains come, the floodwater carries sand that piles up behind the wall. The sand holds water like a sponge and shields it from the hot sun.

# Visuals: Two Kenyan women filling yellow jerrycans from a hand pump beside the sand-filled riverbed on a hot afternoon.
Months later, villagers pump filtered water from a shallow well beside it. This is a sand dam, and it keeps water flowing long after the rains have gone.

# HACK 2 BURKINA FASO - Visuals: An elderly farmer in a long robe and a knitted cap digging a small round pit in hard, crusted earth with a short hand hoe in Burkina Faso in the dry season.
In Burkina Faso, a farmer named Yacouba Sawadogo watched drought turn his fields into a hard crust. He revived an old farming technique called zai.

# Visuals: Close view of a hand dropping dark compost into a small round pit in dry, cracked soil, with rows of identical pits behind it.
Farmers dig small pits across the field and fill each one with compost. Termites come for the compost and dig tunnels that help rain soak deep into the ground.

# Visuals: Green millet seedlings growing out of rows of small round pits in a once-bare field in Burkina Faso after the first rains, with young trees in the distance.
[video] When the rains arrive, every drop stays where the seeds need it. On land where nothing grew, millet and even trees began to return.

# HACK 3 IRAN - Visuals: A dry desert plain in central Iran stretching toward distant brown mountains on a clear morning, with a straight line of round earth mounds crossing it.
# media: stock
In Iran, desert villages have drawn water from the mountains for thousands of years. The secret is a line of shafts you can spot from the air.

# Visuals: A gently sloping underground tunnel of bare earth with a shallow stream of clear water flowing along its floor, lit by a beam of daylight from a shaft above.
# media: ai
Each shaft leads down to a gently sloping tunnel called a qanat. It carries groundwater from the mountains to the village using only gravity, and many still flow today.

# HACK 4 MEGHALAYA - Visuals: Two Khasi farmers on a steep, forested hillside in Meghalaya, India, in the morning, fitting lengths of split bamboo together into a long open channel.
In the hills of Meghalaya in India, Khasi and Jaintia farmers build water pipes from bamboo. The channels carry stream water down steep slopes to their plantations.

# Visuals: Clear stream water running along a narrow split bamboo channel on a green hillside in Meghalaya and falling in small drops onto the base of a betel leaf vine.
[video] Each channel is made narrower as it goes. By the time it reaches the plants, a rushing stream has become a gentle drip right at the roots.

# HACK 5 THE OLLA - Visuals: Smart Village Boy kneeling in a small vegetable garden in rural Kenya in the late afternoon, lowering an unglazed clay pot into a hole beside young tomato plants.
Our last hack is one you can try at home. Bury an unglazed clay pot up to its neck beside your plants, and fill it with water.

# Visuals: Close view of a hand pouring water from a metal jug into the open neck of a clay pot buried in dark soil beside young tomato plants in a Kenyan garden.
The water seeps slowly through the clay, right where the roots are. Farmers have watered crops this way for centuries, and it uses much less water than a watering can.

# OUTRO - Visuals: Smart Village Boy sitting beside the wooden globe on the Kenyan hilltop at sunset, smiling at the five blue pins.
Sand that holds a river, pits that catch the rain, tunnels, bamboo pipes and a buried pot. Five ideas, and not one of them needs electricity.

# Visuals: Smart Village Boy waving towards the viewer on the Kenyan hilltop at dusk, with the first stars appearing above the village.
Which of these would work where you live? Tell us in the comments, and subscribe for more village ideas.
```


## 7. Self-review checklist

- [ ] First line is a `# Title In Title Case`. The header block has no spoken lines and ends with a blank line.
- [ ] Only invented characters on `# character:` lines, with the host's line copied exactly.
- [ ] 15–17 scenes (at most 20), 400–430 words, each scene 2–3 sentences and under 60 words.
- [ ] Every scene has one `# Visuals:` line: one moment, who, what, where and when, a simple pose, no text, logos, montages,
      time-lapses or camera directions.
- [ ] Chapter headings: `# HOOK` on scene 1, then `# HACK n PLACE` sections of 2 or more scenes, and `# OUTRO`.
- [ ] 4–5 `[video]` tags, each at the start of a narration line. No other brackets anywhere in the narration.
- [ ] `# media:` and `# location:` only where needed, each on its own line.
- [ ] Each hack's first scene names its country or region in the narration.
- [ ] Names, places and descriptions are spelled and worded the same every time.
- [ ] Hook opens on a problem or surprise, and the open loop pays off at the end. The ending has a recap, a question and
      "subscribe".
- [ ] Respectful, accurate depiction of every community.
- [ ] Fact-check list done (section 5), with sources, and anything unconfirmed softened or cut.
- [ ] `make_video.py Scripts\<name>.txt --check` shows **Flags: none**, no notes, about 2:50 or less, and fits the day's neurons.
