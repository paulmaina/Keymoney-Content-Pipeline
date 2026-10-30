# Smart Village Boy script standard

How to write a script for the AI Video Editor (`make_video.py`). A script written to this standard renders into a narrated,
captioned full-length horizontal video (8–10 minutes or longer) and a numbered series of vertical Shorts, with no hand fixes. Every rule here matches what the tool's parser and
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
- It doesn't make a video clip. Use it on about one scene in 6 to 12, the most dynamic ones: water rushing, a reveal, a
  bridge being crossed. `--check` gives the range for your scene count (5–9 for a 55-scene script) and notes anything outside
  it.
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
- **Every chapter opening is a hook too:**
  - The vertical video is split automatically into Shorts of under 2:50, cut at chapter headings wherever possible. So the
    first scene of each `# HACK` section may be the first thing a viewer sees.
  - Make it work on its own: name the place and the problem or surprise in its first sentence ("Our third stop is Iran, home
    to some of the driest land on Earth."). Don't open a chapter with "As we saw" or anything that needs an earlier part.
  - End each chapter on its payoff (the result, a surprising fact), so a Short that stops there still feels complete.
- **Ending (last 2–3 scenes):**
  - A one-line recap of all the hacks.
  - Then a call to action with a question viewers can answer in the comments ("Which of these would work where you live?") and
    "subscribe".
  - Smart Village Boy appears in the hook and the ending, on the same Kenyan hilltop with the wooden globe.
- **Tone:** warm, curious and clear. Not hype: no "INSANE", no "you won't believe", no exclamation marks in a row.


## 4. Length and cost

**No length cap.** The horizontal video uses the whole script. Plan for **8–10 minutes or longer**.

**Speed.** The default voice speaks about 150–160 words a minute at the channel's +5% speed, plus a short pause after every
scene, which comes to about **150 words and 6–8 scenes a minute** with 2–3 short sentences per scene. (The speed is still an
estimate until a full narration is measured; `--check` uses the current figure.)

| Video length | Scenes | Spoken words |
|---|---|---|
| 5 minutes | 30–40 | about 750 |
| **8 minutes** | **50–60** | **about 1,200** |
| **10 minutes** | **60–75** | **about 1,500** |
| 12 minutes | 75–90 | about 1,800 |

- **Structure for 8–10 minutes:** a hook of 3–4 scenes, 5 hacks of 8–12 scenes each, and an ending of 2–3 scenes.
- **Shorts are automatic:** the vertical version is the same script split into parts of under 2:50 each, including the
  intro and outro, at scene boundaries and at chapter headings wherever possible. They're saved as
  `<name>_Short_Part1.mp4`, `<name>_Short_Part2.mp4` and so on, in the video's `Upload` folder. Each starts with "Part 1 of N", and every part but the
  last ends with "Watch Part 2" instead of the subscribe card. Keep each `# HACK` section under about 2:40 (around 15
  scenes), so a chapter fits in one Short and never has to be cut in the middle. `--check` lists every part with its scenes,
  chapters and length.

**Cloudflare cost.** Cloudflare makes every final image. The first 10,000 "neurons" a day are free; after that the editor's
paid plan bills **$0.011 per 1,000**, up to a daily cap of **60,000 neurons** (about $0.55 a day beyond the free amount,
`max-daily-neurons` in `make_video.toml`). Each new scene costs about **390 neurons** for one shot. Scenes longer than 6 seconds also get a **second shot** of the same moment from another angle or distance
(on by default), which roughly doubles that scene to about **770 neurons**:

| Item | Neurons |
|---|---|
| Image prompt | ~46 |
| Two images (horizontal and vertical) | ~264 |
| Two quality checks | ~81 |
| Second shot (scenes over 6 s): its prompt, two images and two checks | ~380 |
| YouTube notes: the full video, plus one per Short (upper estimate) | ~110 + ~95 each |

With 2–3 sentences, nearly every scene runs over 6 seconds, so a first render costs about (the dollars are beyond the free
10,000, on a day nothing else was spent):

| Scenes | With second shots (default) | Cost | With `--no-two-shots` | Cost |
|---|---|---|---|---|
| 25 | ~19,000 | ~$0.10 | ~10,000 | ~$0.00 |
| 50 | ~39,000 | ~$0.32 | ~20,000 | ~$0.11 |
| 60 | ~47,000 | ~$0.41 | ~24,000 | ~$0.15 |
| 75 | ~58,000 | ~$0.53 | ~30,000 | ~$0.22 |

- **The daily cap:** all of these fit under 60,000 in one day. Past about 75 scenes with second shots, `--check` says the run
  doesn't fit and which scenes would go past the cap. Run the render command with `--visuals-only` once a day (each day's
  images are cached, so the next day carries on where it stopped) and render when `--check` shows 0 new images, or raise the
  cap. Scenes made past the cap during a render are drawn by a stand-in service and listed at the end, with the command that
  redraws them with Cloudflare.
- **Drafts:** `--draft` renders the whole script quickly with stand-in pictures (only the prompts cost neurons, about 75 a
  scene), to check the pacing and the shots before paying for the final images.
- **Redraws:** each image that fails the quality check and is redrawn adds ~173 neurons.
- **Stock:** scenes that end up using stock footage don't need AI images, so they cost almost nothing.
- **Reruns:** cached scenes are free, so changing one scene's narration or Visuals line only costs that scene.
- **To save neurons:** `--no-two-shots` halves the cost (one picture per scene); `--no-check-images` drops the checks.
  Writing a few scenes as one short sentence (under 6 seconds) also keeps them to one shot.


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

This exact text is in `Scripts/TEMPLATE.txt` and passes `--check` with no flags or notes:
- 55 scenes and about 1,220 words, so about 8:09 with the intro and outro
- 7 `[video]` scenes and 7 chapters
- a vertical series of 4 Shorts, cut only at chapter headings
- about 42,700 neurons with second shots (about $0.36 beyond the free 10,000, one day under the cap), or 21,900 (about
  $0.13) with `--no-two-shots`
The facts were chosen from well-documented examples, but **fact-check them (section 5) before using this script
for a real video**.

```text
# Five Village Water Hacks That Beat Drought
# note: Example script from SCRIPT_GUIDE.md. Copy it, keep the structure, replace the content.
# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile

# HOOK - Visuals: Smart Village Boy kneeling on a cracked, dry riverbed in rural Kenya at sunrise, holding up a handful of damp sand in one open palm.
[video] This river has been dry for months. Yet dig into the sand, and you find water.

# Visuals: A wide view of a dry, sandy riverbed winding between thorny bushes and acacia trees in rural Kenya under a hazy morning sky.
Around the world, billions of people live with water shortages for at least part of every year. Most of them will never see a big dam or a water factory.

# Visuals: Smart Village Boy standing beside a large wooden globe on a Kenyan hilltop at sunrise, pointing at one of five small blue pins stuck into it.
So villages found their own answers. Today we visit five of them, and every one of them still works.

# Visuals: Smart Village Boy turning the wooden globe slowly with one hand on the Kenyan hilltop in the early morning light.
Welcome to Smart Village Boy, where we explore the cleverest ideas from the world's villages. Stay to the end for a clay pot that waters plants by itself.

# HACK 1 KENYA - Visuals: A wide seasonal riverbed of pale sand in Kitui, Kenya, in the dry season, with acacia trees on the banks and a herd of goats crossing it.
# location: Kitui, Kenya
Our first stop is the dry hills of eastern Kenya. Here, many rivers flow for only a few weeks a year.

# Visuals: Brown floodwater rushing down the sandy Kenyan riverbed during a heavy rainstorm, carrying branches past the acacia trees.
[video] When the rain finally comes, it arrives all at once. Floodwater roars down the riverbed and is gone within hours.

# Visuals: A group of Kenyan villagers mixing concrete with shovels beside a dry riverbed on a hot morning, with a half-built low wall behind them.
So communities came up with a plan. Together, they build a low concrete wall straight across the riverbed, down to the solid rock beneath.

# Visuals: The finished low concrete wall standing across the dry Kenyan riverbed in the afternoon sun, with the riverbank rising on either side.
It looks like a small dam, but it isn't built to hold back a lake. It is built to catch something else.

# Visuals: Floodwater pouring over the low concrete wall in the Kenyan riverbed during the rainy season, with wet sand piled up behind the wall.
Each flood carries sand along with the water. The sand settles behind the wall, and after a few rainy seasons it fills up completely.

# Visuals: Close view of a hand pressing into damp sand behind the concrete wall in the Kenyan riverbed, with water seeping into the hollow it leaves.
Now the clever part begins. Sand holds a surprising amount of water in the tiny spaces between its grains.

# Visuals: The sand-filled riverbed behind the concrete wall under the harsh midday sun in Kitui, Kenya, its surface dry and pale.
The top layer dries out in the sun, but it acts like a blanket. The water underneath is shielded from the heat, so very little evaporates.

# Visuals: Two Kenyan women filling yellow jerrycans from a hand pump beside the sand-filled riverbed on a hot afternoon.
[video] Months after the rains have gone, villagers pump water from a shallow well beside the wall. The sand even filters it on the way.

# Visuals: A Kenyan farmer watering rows of green kale with a watering can beside the sand-filled riverbed in the late afternoon.
This is called a sand dam. It gives families water for drinking, for their animals and for vegetable gardens through the long dry season.

# Visuals: Kenyan children walking home along a dusty path at sunset, each carrying a small yellow jerrycan of water.
And because the water is close to home, the daily walk to fetch it gets much shorter.

# Visuals: An old, weathered concrete wall across a sand-filled riverbed in rural Kenya, with green bushes growing along the banks in the late afternoon.
Built well, a sand dam can keep working for many years. Some villages now have several along the same river.

# HACK 2 BURKINA FASO - Visuals: A wide view of a bare, crusted field under a hazy sky in northern Burkina Faso in the dry season, with a few scattered thorn trees.
Now we travel west, to Burkina Faso in the Sahel. In the 1970s and 1980s, terrible droughts turned farmland here into hard, bare crust.

# Visuals: Close view of a hand pouring water onto hard, crusted soil, with the water pooling and running off instead of soaking in.
When rain fell on that crust, it couldn't soak in. It simply ran off, taking the last good soil with it.

# Visuals: An elderly farmer in a long robe and a knitted cap standing at the edge of a bare field in Burkina Faso at dawn, leaning on a short hand hoe.
A farmer named Yacouba Sawadogo refused to give up his land. He turned to an old farming technique called zai, and made it better.

# Visuals: The elderly farmer in a long robe and a knitted cap digging a small round pit in the hard, crusted earth with a short hand hoe in Burkina Faso in the dry season.
[video] In the dry season, he dug small round pits across his field. Each one is only about as wide as a large bowl.

# Visuals: Close view of a hand dropping dark compost into a small round pit in dry, cracked soil, with rows of identical pits behind it.
Into every pit went a handful of compost or manure. That makes the soil richer, but it also invites an unexpected helper.

# Visuals: Close view of termites crawling over dark compost at the bottom of a small pit in dry soil in Burkina Faso.
Termites come to eat the compost. As they feed, they dig tiny tunnels that break open the hard ground beneath.

# Visuals: Raindrops falling into a row of small round pits in a dry field in Burkina Faso during the first storm of the rainy season.
When the rains arrive, each pit catches the water like a cup. The termite tunnels help it sink deep into the soil, right where the seeds are.

# Visuals: Green millet seedlings growing out of rows of small round pits in a once-bare field in Burkina Faso after the first rains.
Millet and sorghum began to grow again on land that had been written off. Even tree seeds in the manure sprouted.

# Visuals: The elderly farmer in a long robe and a knitted cap walking between young trees in a green forest in Burkina Faso in the late afternoon.
Over the years, he grew a forest on land that had been bare. People came to call him the man who stopped the desert.

# Visuals: Farmers in Burkina Faso sitting in the shade of a large tree at midday, listening to the elderly farmer in a long robe and a knitted cap.
He shared what he learned with other farmers, and in 2018 he received the Right Livelihood Award for his work.

# Visuals: Farmers working far apart across a wide field of small round pits in the Sahel on a hazy morning, each with a short hand hoe.
Today, zai pits are dug by farmers across the Sahel, from Burkina Faso to Niger and Mali.

# HACK 3 IRAN - Visuals: A dry desert plain in central Iran stretching toward distant brown mountains on a clear morning, with a straight line of round earth mounds crossing it.
# media: stock
Our third stop is Iran, home to some of the driest land on Earth. Yet desert towns here have had flowing water for thousands of years.

# Visuals: Aerial view of a long straight line of round earth mounds crossing a desert plain in Iran in the morning, like a row of giant beads.
From the air, you can spot the secret. Long lines of round mounds cross the desert, one after another, for kilometres.

# Visuals: Close view of the dark, round opening of a shaft in the top of an earth mound in the Iranian desert, with a wooden pulley frame above it.
Each mound marks the top of a deep vertical shaft. Far below, all the shafts connect to a single tunnel.

# Visuals: A gently sloping underground tunnel of bare earth with a shallow stream of clear water flowing along its floor, lit by a beam of daylight from a shaft above.
# media: ai
That tunnel is called a qanat. It starts under the mountains, where it taps into groundwater, and slopes gently down to the town.

# Visuals: Clear water flowing quickly along the floor of the earthen qanat tunnel, with a beam of daylight falling from a shaft above.
[video] The slope is so gentle that the water flows at just the right speed. It needs no pumps and no fuel, only gravity.

# Visuals: An Iranian digger in dusty work clothes and a cloth cap standing at the bottom of a narrow shaft, holding a lamp and looking up at the circle of daylight above.
Digging a qanat took great skill. Specialist diggers worked deep underground, using the shafts for air and to lift out the earth.

# Visuals: Clear water flowing out of a stone channel into a green orchard beside a mud-brick village in Iran on a hot afternoon.
Because the water travels underground, very little is lost to the desert heat. It comes out right where people need it.

# Visuals: Pomegranate trees and green fields beside a mud-brick village in the Iranian desert in the late afternoon, with brown mountains behind.
Whole towns grew up around qanats, with orchards and fields in the middle of the desert.

# Visuals: An old stone channel of clear water running through the quiet courtyard of a mud-brick house in an Iranian desert town in the morning.
# location: none
The idea spread far beyond Iran, to North Africa, Spain and beyond. Today the Persian qanat is a UNESCO World Heritage Site, and many still flow.

# HACK 4 MEGHALAYA - Visuals: Misty green hills covered in forest in Meghalaya, India, in the early morning, with a small stream tumbling down a rocky slope.
Next, we head to the misty hills of Meghalaya in northeast India. It is one of the wettest places on Earth, yet farming here has a water problem.

# Visuals: A steep, rocky hillside in Meghalaya covered with betel leaf vines climbing up areca palm trees, under a clear winter sky.
The hills are steep and rocky, and in the dry winter months the streams shrink. Getting water to crops on a slope is hard.

# Visuals: Two Khasi farmers on a steep, forested hillside in Meghalaya in the morning, fitting lengths of split bamboo together into a long open channel.
So Khasi and Jaintia farmers build water pipes from bamboo. They split the stems in half to make long open channels.

# Visuals: A long split bamboo channel carrying clear stream water down a green hillside in Meghalaya, held up on crossed bamboo stands.
The channels start at a stream high on the hill. They carry the water down across the slope, held up on bamboo stands.

# Visuals: Clear water racing along a split bamboo channel on a steep green hillside in Meghalaya in the morning sunlight.
[video] Along the way, the water is split into smaller and smaller channels. Each one is made narrower than the last.

# Visuals: Close view of water falling in small drops from the end of a narrow bamboo channel onto the base of a betel leaf vine in Meghalaya.
By the time it reaches the plants, a rushing stream has become a gentle drip right at the roots. Hardly a drop is wasted.

# Visuals: A Khasi farmer adjusting the angle of a bamboo channel with both hands on a hillside plantation in Meghalaya in the afternoon.
The whole system is built from local bamboo, and farmers can move and adjust it by hand. It has been used here for generations.

# Visuals: Rows of healthy betel leaf vines and black pepper vines climbing trees on a terraced hillside in Meghalaya in soft evening light.
It still waters betel leaf and black pepper plants today, proof that the simplest pipes can work beautifully.

# HACK 5 THE OLLA - Visuals: Smart Village Boy holding an unglazed clay pot with both hands in a small vegetable garden in rural Kenya in the late afternoon.
Our last hack is the one we promised, and you can try it at home. It is just a clay pot, but not an ordinary one.

# Visuals: Close view of the rough, unglazed surface of a round clay pot resting on dark soil in a Kenyan garden, with tiny beads of water on its side.
The pot is unglazed, so the clay is full of tiny pores. Water can slowly pass straight through its walls.

# Visuals: Smart Village Boy kneeling in the small Kenyan vegetable garden, lowering the unglazed clay pot into a hole beside young tomato plants.
[video] Bury the pot up to its neck beside your plants. Then fill it with water and cover the top.

# Visuals: Close view of a hand pouring water from a metal jug into the open neck of a clay pot buried in dark soil beside young tomato plants in a Kenyan garden.
The water seeps out through the clay, right where the roots are. Almost none is lost to the sun or the wind.

# Visuals: Close view of a flat stone lid resting on the neck of a clay pot buried in damp, dark soil beside green tomato leaves in a Kenyan garden.
The lid keeps the water in and stops mosquitoes from breeding. Many gardeners use a flat stone or an upturned saucer.

# Visuals: Young tomato plants with fresh green leaves growing around a buried clay pot in a Kenyan garden in the morning light.
Here is the smartest part. When the soil is dry, the water seeps out faster, and when the soil is wet, it slows down.

# Visuals: A gardener in a wide straw hat checking the water inside a buried clay pot in a dry vegetable garden in the morning.
Farmers have watered crops this way for centuries, in places from North Africa to Latin America. In Spanish, the pot is called an olla.

# Visuals: Smart Village Boy smiling as he fills the buried clay pot in his Kenyan garden from a small watering can at sunset.
It can use much less water than pouring it over the soil, and you only need to refill it every few days.

# Visuals: Two unglazed clay flowerpots joined rim to rim, resting on a wooden table in a Kenyan garden in the afternoon light.
No olla nearby? Gardeners make their own from two unglazed flowerpots joined rim to rim, with the hole in one sealed.

# OUTRO - Visuals: Smart Village Boy sitting beside the wooden globe on the Kenyan hilltop at sunset, smiling at the five blue pins.
Sand that holds a river, pits that catch the rain, and tunnels that carry water under the desert.

# Visuals: Smart Village Boy pointing at the blue pins on the wooden globe one by one on the Kenyan hilltop at dusk.
Bamboo pipes that turn a stream into drops, and a buried pot that knows when to water. Not one of them needs electricity.

# Visuals: Smart Village Boy waving towards the viewer on the Kenyan hilltop at dusk, with the first stars appearing above the village.
Which of these would work where you live? Tell us in the comments, and subscribe for more clever ideas from the world's villages.
```


## 7. Self-review checklist

- [ ] First line is a `# Title In Title Case`. The header block has no spoken lines and ends with a blank line.
- [ ] Only invented characters on `# character:` lines, with the host's line copied exactly.
- [ ] 8–10 minutes or longer: about 6–8 scenes and 150 words a minute. Each scene is 2–3 sentences and under 60 words.
- [ ] Each `# HACK` section is 8–15 scenes (under about 2:40), so it fits in one Short.
- [ ] Every scene has one `# Visuals:` line: one moment, who, what, where and when, a simple pose, no text, logos, montages,
      time-lapses or camera directions.
- [ ] Chapter headings: `# HOOK` on scene 1, then `# HACK n PLACE` sections of 2 or more scenes, and `# OUTRO`.
- [ ] `[video]` on about one scene in 6 to 12, each at the start of a narration line. No other brackets anywhere in the narration.
- [ ] `# media:` and `# location:` only where needed, each on its own line.
- [ ] Each hack's first scene names its country or region in the narration.
- [ ] Names, places and descriptions are spelled and worded the same every time.
- [ ] Hook opens on a problem or surprise, and the open loop pays off at the end. The ending has a recap, a question and
      "subscribe".
- [ ] Every chapter's first scene names its place and grabs attention on its own (it may open a Short), and every chapter
      ends on a payoff.
- [ ] Respectful, accurate depiction of every community.
- [ ] Fact-check list done (section 5), with sources, and anything unconfirmed softened or cut.
- [ ] `make_video.py Scripts\<name>.txt --check` shows **Flags: none** and no notes. Its Shorts list cuts only at chapter
      headings, and its "daily cap" line says "fits" (or the extra days are planned for).
