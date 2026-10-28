---
name: MediQueue
description: AI-assisted emergency department queue, dressed as the Indian hospital corridor it runs in.
colors:
  whitewash: "#f4f6f5"
  surface: "#ffffff"
  surface-2: "#f7f9f8"
  line: "#dbe3df"
  line-strong: "#b9c6c0"
  ink: "#14201b"
  ink-2: "#33433c"
  muted: "#55635d"
  dado: "#24594a"
  dado-deep: "#1a4337"
  dado-ink: "#e7f1ec"
  dado-muted: "#b5cfc3"
  dado-soft: "#e3efe9"
  enamel: "#1d3e9a"
  enamel-deep: "#122a6c"
  enamel-soft: "#e6ecfa"
  brass: "#b08d3c"
  brass-deep: "#8a6d24"
  brass-ink: "#1f1603"
  brass-soft: "#f4ecd9"
  danger: "#c8102e"
  danger-ink: "#a10c25"
  danger-soft: "#fdecef"
  warning: "#a45a00"
  warning-soft: "#fdf1df"
  success: "#15803d"
  success-soft: "#e5f4ea"
  esi-1: "#c8102e"
  esi-2: "#c2410c"
  esi-3: "#eab308"
  esi-3-ink: "#2b1d00"
  esi-4: "#15803d"
  esi-5: "#1d4ed8"
  slider-off: "#6b7671"
  enamel-text-dark: "#a9bdf5"
  whitewash-dark: "#0d1512"
  surface-dark: "#141f1b"
  surface-2-dark: "#192722"
  line-dark: "#273730"
  line-strong-dark: "#3a4d45"
  ink-dark: "#e6eeea"
  ink-2-dark: "#c3d1ca"
  muted-dark: "#9cb0a6"
  dado-dark: "#2f7a64"
  dado-deep-dark: "#0f2a22"
  dado-ink-dark: "#e0ece6"
  dado-muted-dark: "#9fbfb1"
  dado-soft-dark: "#183129"
  enamel-dark: "#3156c4"
  enamel-deep-dark: "#1d3e9a"
  enamel-soft-dark: "#1a2546"
  brass-soft-dark: "#2e2611"
  danger-dark: "#e5384f"
  danger-ink-dark: "#ff8a98"
  danger-soft-dark: "#3a1219"
  warning-dark: "#f0a33a"
  warning-soft-dark: "#33240c"
  success-dark: "#3fbf6a"
  success-soft-dark: "#12301d"
  esi-5-dark: "#3b6ef0"
typography:
  signboard:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "31px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "0.03em"
    fontVariation: "'wdth' 64"
  display:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "96px"
    fontWeight: 900
    lineHeight: 0.86
    fontVariation: "'wdth' 62"
  headline:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "0.02em"
    fontVariation: "'wdth' 68"
  title:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 900
    lineHeight: 1.05
    letterSpacing: "0.02em"
    fontVariation: "'wdth' 68"
  token:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "0.01em"
    fontFeature: "'tnum'"
    fontVariation: "'wdth' 68"
  body:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "'tnum'"
  label:
    fontFamily: "Archivo, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 800
    letterSpacing: "0.08em"
  hindi-sign:
    fontFamily: "Sign Devanagari, Nirmala UI, Noto Sans Devanagari, Mangal, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.3
rounded:
  stamp: "3px"
  tag-square: "4px"
  sm: "5px"
  md: "8px"
  plate: "9px"
  board-card: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  gap: "18px"
  panel: "20px"
  gutter: "28px"
components:
  button-primary:
    backgroundColor: "{colors.dado}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.dado-deep}"
  button-enamel:
    backgroundColor: "{colors.enamel}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
    height: "40px"
  button-enamel-hover:
    backgroundColor: "{colors.enamel-deep}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
    height: "40px"
  button-destructive:
    backgroundColor: "transparent"
    textColor: "{colors.danger-ink}"
    rounded: "{rounded.sm}"
    padding: "5px 12px"
    height: "34px"
  button-destructive-hover:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.surface}"
  button-lg:
    padding: "12px 22px"
    height: "50px"
  enamel-plate:
    backgroundColor: "{colors.enamel}"
    textColor: "{colors.surface}"
    rounded: "{rounded.plate}"
    padding: "11px 13px 10px"
  door-plate:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.plate}"
    padding: "10px 12px 10px 10px"
  door-room:
    backgroundColor: "{colors.enamel}"
    textColor: "{colors.surface}"
    rounded: "6px"
    width: "52px"
    height: "62px"
  slider-thumb-free:
    backgroundColor: "{colors.brass}"
    textColor: "{colors.brass-ink}"
    rounded: "4px"
  slider-thumb-busy:
    backgroundColor: "{colors.enamel}"
    textColor: "{colors.surface}"
    rounded: "4px"
  level-plate:
    rounded: "{rounded.plate}"
    padding: "8px 6px 10px"
    width: "124px"
  waitbar:
    backgroundColor: "{colors.surface-2}"
    rounded: "5px"
    height: "10px"
  waitbar-fill:
    backgroundColor: "{colors.dado}"
  waitbar-fill-late:
    backgroundColor: "{colors.danger}"
  esi-badge-1:
    backgroundColor: "{colors.esi-1}"
    textColor: "{colors.surface}"
    rounded: "{rounded.tag-square}"
    padding: "3px 8px 2px"
  esi-badge-3:
    backgroundColor: "{colors.esi-3}"
    textColor: "{colors.esi-3-ink}"
    rounded: "{rounded.tag-square}"
    padding: "3px 8px 2px"
  tag:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
  tag-delta:
    backgroundColor: "{colors.brass-soft}"
    textColor: "{colors.brass-ink}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
  tag-aged:
    backgroundColor: "{colors.enamel-soft}"
    textColor: "{colors.enamel}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
  stamp:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.stamp}"
    padding: "1px 6px 0"
  alarm:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.surface}"
    rounded: "{rounded.plate}"
    padding: "16px 18px"
  census-line:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "18px 20px"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 11px"
    height: "42px"
  rail:
    backgroundColor: "{colors.dado}"
    textColor: "{colors.dado-ink}"
    width: "248px"
  board:
    backgroundColor: "{colors.enamel}"
    textColor: "{colors.surface}"
---

# Design System: MediQueue

## Overview

**Creative North Star: "The Ward Corridor"**

The software looks like the building it runs in: an Indian government-hospital emergency corridor. A deep oil-paint dado green runs down the left as the navigation rail, flecked with a faint terrazzo chip. The working ground is whitewash. Every sign is a cobalt enamel plate with a white inner keyline, the brand plate is bilingual ("आपातकालीन विभाग / EMERGENCY"), and each consultation room is a doctor's door plate with a brass sliding indicator. Emergency red appears only when something is actually dangerous.

The mode is Operate: dense enough for a nurse at a front desk, but tuned for a projector and a waiting-room TV first. Headings, tokens, room numbers and ESI levels speak in Archivo condensed caps, like painted signage; all running UI text is Archivo at normal width with tabular figures so numbers line up in every column. Depth is shallow and physical: a plate sits on a wall, so surfaces carry one soft shadow and enamel objects carry inset keylines rather than lifted stacks.

The system refuses the white-card SaaS dashboard with a blue accent and a row of stat tiles on top. Counts are read as one census line or one joined strip.

**Key Characteristics:**
- Dado-green rail with terrazzo speckle; whitewash ground; white panels.
- Cobalt enamel plates with a white inner keyline for every sign, room number and the brand.
- Brass is the slider and the "this is you / moved / next" marker, never a clinical colour.
- Clinical ESI scale with the number always printed.
- Condensed caps (wdth 62–68%) for signage voice; normal width and tabular figures for everything you read in sentences.
- Motion only explains a state change: a plate sliding, a queue row moving to its new place, a called token glowing, the preemption alarm dropping in. Nothing moves for decoration.

## Colors

A three-material palette (paint, enamel, brass) on a whitewash wall, with a separate clinical scale that never borrows from the materials.

### Primary
- **Dado Green** (dado): the rail, the primary action ("Issue token and add to queue"), the wait-bar fill, caret and accent-color, and "ours" in charts. Deepens to Dado Deep for hover and for the rail in dark theme.

### Secondary
- **Cobalt Enamel** (enamel): signboards, the brand plate, door-plate room numbers and keyline frames, the Busy slider thumb, focus rings, links, the aged and info tags, the waiting-room board background, and the free-beds semaphore cell. Enamel Deep for hover.

### Tertiary
- **Corridor Brass** (brass): the door-plate slider thumb in its Free state, the brass ring around the signed-in doctor's own plate, the user avatar, text selection, the "Next" tiles on the waiting-room board, and a terrazzo chip colour. Brass Soft carries the signed override delta tag and the background flash of a queue row that moved up. Brass Ink is its text colour.

### Clinical scale (ESI)
- **ESI 1 Resuscitation** (esi-1), **ESI 2 Emergent** (esi-2), **ESI 3 Urgent** (esi-3, with dark Esi-3 Ink text), **ESI 4 Less urgent** (esi-4), **ESI 5 Non-urgent** (esi-5; brightened in dark theme). Used for badges, level plates, the picker's checked state, probability strip segments and per-level chart bars.

### Status
- **Emergency Red** (danger / danger-ink / danger-soft): out-of-range vitals, late wait bars, the H/L vital flag, red-flag callouts, the destructive hover state, and the preemption alarm.
- **Warning Amber** (warning / warning-soft): "over target", "paused" tags, cleaning beds.
- **Success Green** (success / success-soft): the live clock dot, success flashes, "lowers urgency" reasons.

### Neutral
- **Whitewash** (whitewash): page ground. **Surface** (white) for panels, **Surface 2** for inputs, tracks and phone queue cards.
- **Line / Line Strong**: hairlines and control borders.
- **Ink / Ink 2 / Muted**: text in three steps; muted is still AA on white.

Dark theme (`data-theme="dark"`) swaps every neutral and material to its `-dark` token; the ESI scale is unchanged except ESI 5. Brass, Brass Ink and Brass Deep are shared across themes; in dark, brass itself becomes the delta-tag text.

### Named Rules
**The Number Always Rule.** An ESI colour never appears without the level number printed on it ("ESI 2", a plate reading 2, a picker cell "ESI 2"). Colour is never the only carrier of urgency.

**The Brass Is Not Yellow Rule.** Brass marks position and identity (slider, your plate, moved row, next up). It never stands in for ESI 3 yellow, and ESI 3 yellow never decorates.

**The Red Means Danger Rule.** Emergency red is reserved for clinical danger, lateness and destruction. It is never a brand or decorative colour.

## Typography

**Display Font:** Archivo variable, self-hosted (wght 100–900, wdth 62–125%), with Segoe UI, system-ui fallback
**Body Font:** Archivo at normal width (wdth 100%)
**Sign Font:** Sign Devanagari (self-hosted Noto Devanagari subset, 700, U+0900–097F) with Nirmala UI, Noto Sans Devanagari, Mangal fallback

**Character:** One grotesque family used two ways: squeezed into tall caps it is painted hospital signage; at normal width it is a quiet, legible clerical voice. Body is set with tabular figures globally.

### Hierarchy
- **Signboard** (900, 31px, wdth 64%, +0.03em, caps): the brand plate; 64px on the login wall; board heading clamps 40–88px at wdth 64%.
- **Display** (900, 96px, wdth 62%, line-height 0.86): the number on the ESI level plate (76px on phones, 54px small plate); board tokens clamp 56–96px at wdth 66%.
- **Headline** (900, 34px, wdth 68%, caps, line-height 1): page titles in the top bar (22px on phones). Section heads between panels are 26px.
- **Title** (900, 22px, wdth 68%, caps): panel headings. Census numbers are 38px at wdth 72%.
- **Token** (900, 24px, wdth 68%, caps; 34px large): patient tokens such as T033 and room numbers.
- **Body** (400, 15px, line-height 1.45, tabular figures): all UI text; inputs at 600; prose capped at 68–72ch. Inputs rise to 16px on coarse pointers.
- **Label** (800, 11–13px, +0.06–0.08em, uppercase, normal or wdth 80%): table headers, fact and vital labels, slider labels, sub-heads.

### Named Rules
**The Signboard Voice Rule.** Condensed caps (wdth 62–72%) belong to things a sign would say: headings, tokens, room numbers, ESI levels, stamps, census labels, legends. Sentences, names, hints and buttons stay at normal width and in sentence case.

**The Tabular Rule.** Figures are tabular everywhere (`font-variant-numeric: tabular-nums` on body) so waits, vitals and counts align in columns and the clock does not jitter.

**The Bilingual Plate Rule.** The Devanagari line sits above the English on the brand plate and the board, set in the sign face at 700.

## Layout

App shell is a two-column grid: a 248px sticky dado rail and a fluid main column. The top bar is sticky, whitewash at 92% with an 8px backdrop blur and a hairline below; it holds the condensed page title, a one-line subtitle (hidden under 760px), the 24-hour IST clock pill and a theme toggle. Page content sits in a single grid with an 18px gap and 20px/28px padding (14px/16px on phones).

Working areas use a split: fluid form left, a 380–460px right column for the AI panel (sticky at 96px on wide screens). Even and wide splits exist for doctor and beds. Door plates auto-fit at min 230px; beds auto-fill at 200px. Admin opens with a one-line census instead of tiles.

Spacing rhythm is 4 / 8 / 12 / 18 / 20 / 28px, with 6, 10 and 14px as in-component steps.

Breakpoints: at 1180px splits and three-column grids collapse to one column. At 960px the rail becomes a 272px off-canvas drawer with a backdrop, two-column grids collapse, the login wall stacks and the board goes single column. At 760px the top bar compacts, door plates become a two-up grid with the slider spanning full width, forms go two columns, vitals two columns, and the queue table becomes one card per patient.

## Elevation & Depth

Shallow and physical: one soft ambient shadow for anything resting on the wall, inset keylines for enamel, and no stacked lift. Hover never raises a surface; presses scale down slightly instead.

### Shadow Vocabulary
- **Wall shadow** (`0 1px 2px rgba(20,32,27,.06), 0 8px 20px -14px rgba(20,32,27,.35)`; dark: `0 1px 2px rgba(0,0,0,.3), 0 10px 24px -16px rgba(0,0,0,.7)`): panels, census strip and line, door plates.
- **Enamel keyline** (`inset 0 0 0 3px <plate colour>, inset 0 0 0 5px rgba(255,255,255,.92)`): the brand plate and the alarm; 2px / 3.5px on room numbers; 10px / 14px around the whole waiting-room board.
- **Plate frame** (`inset 0 0 0 2px enamel`): door plates and occupied beds.
- **Hung sign** (`0 8px 18px -10px rgba(0,0,0,.5)`): the brand plate on the rail; board cards use `0 10px 26px -16px rgba(0,0,0,.6)`.
- **Slider well and thumb** (well: `inset 0 0 0 1px line, inset 0 1px 2px rgba(0,0,0,.08)`; thumb: `inset 0 -2px 0 rgba(0,0,0,.14), 0 1px 2px rgba(0,0,0,.25)`): the only bevelled object, because it is a physical slide.

### Named Rules
**The Keyline Rule.** Anything cobalt or red that acts as a sign carries the white inner keyline. Plain panels never do.

## Shapes

Softly squared metal. Panels and census at 8px; enamel plates, door plates, level plates, beds and the alarm at 9px; controls and inputs at 5px; ESI badges, nav counts and the AI tag at 4px; stamps at 3px; board cards 10–12px. Only conversational tags and the clock dot are fully round. The waiting-room board is itself a plate: the whole viewport is enamel with a white keyline frame. Hairlines are 1px; control borders 1.5px; table headers sit on a 2px ink rule; dividers inside forms are dashed.

## Components

### Buttons
- **Shape:** squared (5px), 40px min height; small 34px, large 50px.
- **Primary:** Dado Green fill, white text, 700 14px; one per working area ("Issue token and add to queue").
- **Enamel:** Cobalt fill for operational re-runs (concurrency test).
- **Secondary:** white with a 1.5px line-strong border; quiet/ghost is borderless.
- **Hover / Focus / Press:** hover only on fine pointers (border to ink-2, fills to their deep tone); `:active` scales to .97 over 160ms; focus is a 3px enamel outline at 2px offset. Loading hides the label behind a 600ms spinner.
- **Destructive:** outlined, never filled at rest: transparent with a 55% red border and red-ink text. It fills red only on hover or keyboard focus. It sits apart from neighbours (a 14px gap in row actions) and confirms before acting ("Mark left", "End shift").

### Enamel plate
Cobalt plate, 9px corners, white text, double inset keyline, hung shadow. Devanagari line over a 900-weight, wdth 64% English line in caps. Used for the brand on the rail and login wall.

### Door plate with slider (signature)
White plate framed by a 2px enamel inset; a 52 x 62px enamel room-number block on the left with its own keyline; doctor name (800) and a muted status line ("With T027 · ESI 3 · 12 min"). Below, a three-cell slider (Busy / Free / Off) in a recessed well. The thumb glides with `transform 240ms` on the ease-out curve when state changes: enamel when Busy (white label), brass when Free (brass-ink label), grey when Off. The signed-in doctor's plate gets an extra 3px brass ring. Beds reuse the slider as Free / In use / Clean, with a warning-amber thumb for cleaning.

### Level plate
ESI-coloured plate, 9px corners, a white outline inset 6px; "ESI" label over a 96px wdth 62% numeral and the level name. Small variant (76px wide, 54px numeral) on the doctor console.

### Wait bar
True-scale bar: 10px track in surface-2 with a hairline; dado fill whose width is minutes waited; a 2px ink tick marks the ESI target. Past target the fill turns red and the minutes text turns red-ink. A legend line explains fill, late and tick. 6px tall in compact lists.

### Census line and strip
Admin opens with a single sentence-like line on a white panel: a condensed caps lead ("LAST 12 HOURS") then bold condensed figures (17px, wdth 78%) with muted labels. The bed board uses a joined census strip: one surface divided by hairlines, 38px wdth 72% figures; the semaphore cell is enamel with white text.

### Stamp
Rubber stamp for log events: 1.5px currentColor border, 3px corners, 12px 900 caps, rotated -2deg. Ink, green, red, amber or blue. Log entries are stamped, never removed.

### Tags and badges
- **ESI badge:** squared 4px, 900 15px condensed caps, "ESI n" always.
- **Tags:** pill, 12px 700. Aged ("now 3", enamel-soft with an up arrow), signed override delta ("AI 4 · up 1", brass-soft), warn, bad, ok, info.
- **Vital flags:** a 20px red square with white H or L beside an out-of-range vital, lab-report style.
- **Nav counts:** squared 4px count in the rail (patients waiting, beds free/total).

### Alarm
The single alarm: preemption on the doctor console, rendered as a red enamel sign with the white double keyline, a 30px white icon, condensed caps headline and a white button with red text.

### Inputs / Fields
Surface-2 fill, 1.5px line-strong border, 5px corners, 42px tall, 600 15px. Focus: enamel border, white fill, 3px enamel ring at 25%. Out of range: red border, danger-soft fill, red-ink value and the helper prefixed "Out of range · ". The ESI picker is five equal squared cells that fill with their ESI colour when checked.

### Navigation
Dado rail with terrazzo; group labels in 13px condensed caps, dado-muted. Links 600 15px on dado-ink; active link is a whitewash tab with ink text and a dado icon and count. Clock pill: 22px 800 wdth 72%, 24-hour IST ("23:08 IST") with a green live dot; turns red-bordered when offline.

### Queue table to phone cards
Uppercase 12px muted headers on a 2px ink rule; 10px cells with hairlines. Columns: position, token, level (badge plus aged/delta tags), patient, wait bar, estimate, flags, actions with Re-triage then an isolated outlined Mark left. A row that moves up flashes brass-soft and fades over 1200ms. Under 760px each row becomes a surface-2 card with a hairline inset: token and level on top, then patient, wait bar, "Estimated wait …", flags and full-width actions.

### Waiting-room board
The whole viewport is one enamel plate with a white keyline frame. Devanagari line and a condensed caps department heading on the left, a giant 24-hour IST clock on the right. "Now being seen": white cards pairing an enamel ROOM plate with a huge token. "Waiting": translucent tiles with a hairline keyline; the next patients are brass tiles marked "Next".
When an ambulance is on its way, a brass band under the header says "Ambulance arriving in about 6 min · Room 3 is being kept free for it", so the waiting room sees why someone will go ahead of them.

### Risk and ambulance tags
Pills next to the ESI badge in the queue. "risk 31%" (warning amber on amber wash) marks an ESI 3–5 patient the AI thinks may really be an emergency; their aging interval is shorter and they may reach level 2. "ambulance" (enamel on enamel wash, with the ambulance glyph) marks a patient who arrived after a pre-alert. Both carry a plain-language title explaining the effect.

### Held room
A door plate whose room is held for an incoming ambulance gets a brass outer ring (2px gap, 2px brass), and its status line reads "Held for ambulance A01 · in 6 min". The doctor sees a calm enamel alarm ("Room held · ambulance A01 (ESI 2) in 6 min"); a refused "Call next" is an info flash, not an error.

### Incoming ambulance row
List row with the ambulance glyph in enamel, the A-code as a token, ESI badge, complaint and age/sex, and a second line naming the held room. Right side: a condensed caps countdown ("IN 6 MIN", red when late) above "Arrived" (primary) and "Cancel" (destructive outline, confirmed).

### Token slip
After a token is issued the triage desk shows a slip: white plate with an enamel keyline, an SVG QR code (dark #14201b on white, 2-module quiet zone, viewBox only so it scales) beside the token in condensed caps, its ESI badge, one sentence on what the code opens, and an "Open the patient's view" button.

### Patient's own page
Phone first, opened from the QR, no login and no names. A dado-green header with a small enamel EMERGENCY plate, then white panels: the token and arrival time, "You are 12th in the queue" in condensed caps with the position in enamel, the expected range, an enamel-wash note when their priority is about to rise, and a "Why you are waiting" list of plain counts (who came later but is more urgent, ambulances on the way, doctors on duty). It refreshes every 4 seconds (every 15 in a background tab); status changes replace the card ("You are being admitted"). Brass-wash note "You are next" / "Nearly your turn" at positions 1–2; enamel-wash note with a full-width "I'm back" button while stepped away; red-wash note while a "feels worse" report is open, green once a nurse has checked or re-triaged.

### Patient actions ("While you wait")
An accordion of full-width rows, one open at a time: 40px round icon well, bold label, muted one-line explanation, chevron. "I feel worse" has a red well and opens a two-column grid of 52px check chips (red when ticked) and a solid red "Tell the nurse"; "Step away" opens 10/20-minute chips (green when chosen) and is drawn dashed and disabled, with the reason as its subtitle, when the patient may not leave; "Sound and vibration" is a switch (green when on); "I'm leaving" has a grey well and asks in place, with the destructive outline button beside "Stay in the queue". Confirmations appear as a toast pinned to the bottom of the screen.

### The call ("Please go to Room 3")
When the patient is called the whole screen turns enamel-deep and shows one cobalt enamel sign with the white inner keyline: the token, "PLEASE GO TO" and the room in condensed caps at up to 108px, one sentence, and a white "I'm on my way" button that takes focus. It scales in and glows brass twice (fades only with reduced motion), and comes with a beep and a vibration when the patient switched them on.

### Patient sign-in
Same chrome as the patient page: one panel with a condensed-caps heading, two 50px inputs (token, last 4 digits) and the primary "Show my place". Demo patients sit below as full-width chips: token with its ESI badge, where they are in the queue, their complaint, and the phone ending.

### Help requests on the triage desk
A panel with a red inner keyline above the ambulances: one list row per open report with a red warning glyph, token, ESI badge, name, a "red flag" tag for breathing, chest pain or faintness, the symptoms in bold (red for red flags) and how long ago, then "Re-triage" (primary) and "Checked, no change". New reports drop in once. The queue repeats them as a red "feels worse" tag, and a stepped-away patient carries an enamel "away till 11:05" tag (and a dashed outline tile on the TV).

## Do's and Don'ts

### Do:
- **Do** print the level number on every ESI colour (badge, plate, picker, legend).
- **Do** set signage (headings, tokens, room numbers, levels, stamps) in Archivo condensed caps at wdth 62–72% and everything else at normal width.
- **Do** keep tabular figures on for all numbers.
- **Do** frame cobalt and red signs with the white inner keyline.
- **Do** keep destructive actions outlined and isolated until hovered or focused, and confirm them.
- **Do** draw waits to true scale against the ESI target tick.
- **Do** show time as 24-hour IST.
- **Do** use the ease-out curve `cubic-bezier(.23, 1, .32, 1)` for things entering or answering a press (160ms presses, 240ms slider glide, 260ms drawer, 220ms alarm drop-in, 300ms called-token grow-in, 200ms AI level settle) and the ease-in-out curve `cubic-bezier(.77, 0, .175, 1)` for things moving across the screen (300ms queue-row slide, 600ms login demo glide).
- **Do** animate only on a state change: queue rows slide (FLIP) when the scheduler reorders them and new rows fade in 220ms; a newly called token on the board grows in and glows brass twice (1.4s each); the red preemption alarm drops in and pulses its ring twice (900ms each); the AI level plate settles only when the suggested level changes, never on every keystroke; the login page's door plate glides Busy to Free once.
- **Do** keep reduced motion gentle, not absent: under `prefers-reduced-motion` nothing slides, glides or pulses, and those state changes fade in over 150ms instead.
- **Do** colour charts through CSS variables so they follow the theme.

### Don't:
- **Don't** open a page with a row of stat tiles; use a census line or a joined census strip.
- **Don't** use brass for urgency or ESI 3 yellow for decoration.
- **Don't** use emergency red for anything that is not danger, lateness or destruction.
- **Don't** fill a destructive button at rest or place it flush against a safe action.
- **Don't** delete events from the log; stamp them.
- **Don't** lift surfaces on hover or stack heavier shadows than the wall shadow.
- **Don't** animate data people read: no growing chart bars, no sliding wait bars on refresh, no counting numbers, no page-load stagger.
- **Don't** show another patient's name or details anywhere the public can see: the board and the patient page use tokens and counts only.
- **Don't** set sentences, names or button labels in condensed caps.
