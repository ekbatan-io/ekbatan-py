# Palettes

`src/styles/palette.css` is the one knob: four custom properties that the whole site resolves
its colours through. This file is where the ones worth keeping are written down, so changing
the site's identity is a paste rather than an archaeology exercise.

To use one, replace the `:root` block in `src/styles/palette.css` with its tokens.

Contrast figures are WCAG ratios, measured rather than estimated. The two that matter are
**ink on paper** (body text) and **paper on primary** (the hero block, the buttons, the CTA
panel), because those are the only places the bundle puts text on a filled ground.

---

## `java` - the reference implementation's own

The Java site's palette, carried over unchanged. This is what the repository ships.

```css
--brand-orange: oklch(69% 0.21 38);   /* #ff5d22 */
--brand-yellow: oklch(93% 0.18 100);  /* #ffe939 */
--paper: oklch(99% 0 0);              /* #fcfcfc */
--ink: oklch(13% 0 0);                /* #070707 */
```

| | |
| --- | --- |
| ink on paper | 19.55:1 |
| paper on primary | **2.98:1** - fails AA for normal text, and does on the Java site too |
| ink on highlight | 16.30:1 |

The two accents are 62 degrees apart on the hue wheel, which is the property that makes the
design work: big flat blocks of orange and yellow sit beside each other without fighting.
Any replacement should keep that relationship. A near-complementary pair does not - tried at
280 and 122, and the two colours clashed everywhere they touched.

---

## `bone` - warm ground, no accent colour

Sampled from [Craftora's retro-watch
shot](https://dribbble.com/shots/27066331-Retro-Watch-E-commerce-Website-Brutalist-UI-Design):
`#e3e1c6` is 40.6% of every pixel in that image, and the only chroma in the frame comes from
the product photography.

```css
--brand-orange: oklch(17% 0 90);       /* #0f0f0f  the button   */
--brand-yellow: oklch(83% 0.035 102);  /* #cbc9af  deep bone    */
--paper: oklch(90% 0.036 104);         /* #e1e0c5  bone ground  */
--ink: oklch(19% 0.004 107);           /* #141412  warm black   */
```

| | |
| --- | --- |
| ink on paper | 13.76:1 |
| paper on primary | 14.25:1 |
| ink on highlight | 10.98:1 |

**The two accent slots stop holding accents.** The shot has none - its one button is solid
black - so the primary becomes the ink itself, and the highlight becomes the deeper bone the
design uses for panel fills. Black text still reads on that, which is what the bundle expects
of `--brand-yellow`.

Every hue is around 105, a warm yellow-green. That is what keeps the bone from going grey and
the black from going blue; drop the chroma to 0 and it becomes a cold grey site.

Known rough edge: cards use `bg-paper`, so they are the same bone as the ground and are held
apart only by their 2px border. The shot sits its panels a shade off the ground. Fixing that
means giving cards the deeper bone rather than swapping tokens.

What this palette cannot carry: most of what makes that shot look like that is typography -
enormous condensed uppercase display type, hairline dividers rather than 2px borders, and tiny
wide-tracked mono labels. Our display face is Space Grotesk and the bundle's borders are 2px
throughout.

---

## `formwork` - a poured wall, hairlines, one word of burgundy

Formwork is the timber shuttering concrete is poured into; board-formed concrete keeps the
grain of those boards after the moulds come off. It names what this palette actually does
rather than the shot it started from - the vertical striation is one of its four texture
layers, and the whole ground is built to read as a poured surface rather than as paper.

Sampled from [Algirdas Jasaitis's brutalism-exhibition
shot](https://dribbble.com/shots/19056626-Brutalist-website-exploration). Unlike the others
this is **not only tokens** - half of what makes that image look the way it does is line
weight, not colour.

```css
--brand-orange: oklch(30% 0 90);       /* #2e2e2e  filled blocks  */
--brand-yellow: oklch(91% 0.05 85);    /* #f1dfbc  chalky ochre   */
--paper: oklch(96% 0.003 68);          /* #f3f1f0  warm off-white */
--ink: oklch(30% 0 90);                /* #2e2e2e  charcoal      */
--rule: oklch(88% 0.004 68);           /* #d9d7d5  hairline grey */
--blood: oklch(38% 0.152 19);          /* #80011f  burgundy       */
--on-accent: var(--paper);             /* text on an accent fill  */
--glyph-dark: oklch(48% 0.1 200);      /* #006e74  glyphs on bone */
--glyph-light: oklch(78% 0.1 200);     /* #5dcbd1  glyphs on ink  */
```

| | |
| --- | --- |
| ink on paper | 12.14:1 |
| paper on primary | 12.14:1 |
| ink on highlight | 10.43:1 |

Plus three structural overrides, all in `palette.css` and none touching the frozen bundle:

- **the wall** - four texture layers over the paper colour, each doing a different job and
  none of them a pattern you can name while reading: *grain* (fine paper fibre, 180px tile);
  *aggregate* (sparse darker flecks - a steep gamma on the noise alpha keeps only its darkest
  end, so it scatters rather than fogs); *boarding* (vertical streaking - `baseFrequency` takes
  two numbers, and a low one across with a high one down stretches the noise into columns,
  which is the board-formed concrete in the reference photograph); and *pour* (very low
  frequency mottling, the uneven patches a wall dries with, on an 1100px tile so it never
  repeats visibly). `stitchTiles` keeps every edge seamless, so the repeat down a long
  page is invisible. The stack **scrolls with the page**: `background-attachment: fixed` pins
  it to the viewport instead, and then the content slides over a stationary surface, which
  reads as a filter on the screen rather than as a wall the page is mounted on. A wall moves
  when you move past it. Cards keep `bg-paper` and come out smooth: panels laid on the wall,
  not part of it;
- **a plain header.** A sticky element cannot carry a matching patch of a scrolling texture -
  its background is painted against its own box, so as the wall travels behind it the two drift
  apart and the seam crawls. A flat plane over a moving wall is coherent; a mismatched piece of
  wall is not;
- **hairlines** - `.brutalist-border`, `.border-b-2` and `.border-t-2` drop from 2px ink to 1px
  `--rule`. Every rule in that shot is a light grey hairline;
- **flat panels** - `.brutalist-shadow` and its two coloured variants become `box-shadow: none`.
  Nothing in the shot casts a shadow, and a 6px hard shadow under a 1px border looks like a
  mistake;
- **two accents, in Java's places.** The Java site spends colour in fixed spots and this keeps
  the spots, changing only what goes in the small ones. `--brand-yellow` becomes a chalky ochre
  at chroma 0.05 - one token doing two jobs, since `.bg-yellow` fills inline marks, the nav chip
  and the marquee with ink on top, while `.text-yellow` is the numeral inside a dark chip. A
  light value satisfies both at 10.4:1 either way round. Push the chroma to 0.08 and it stops
  being a warm grey and becomes a second loud colour. `.text-orange` - the marquee's star and
  the full stop closing `framework.` - goes burgundy: a handful of glyphs, which is exactly
  where a strong colour can be spent. **Filled blocks stay charcoal.** The hero band, the
  buttons and the CTA panel are the largest areas on the page, and colouring those would not be
  a tiny modification;
- **the accent is spent where the area is smallest.** Measured rather than guessed: on the
  landing page the bone ground is 84.3% of the pixels, the charcoal fills 12.9%, and everything
  in the highlight slot 2.8% across 26 elements; on a docs page it is 95.2 / 4.7 / 0.05. That is
  60-30-10 with the highlight at the bottom of the accent band, and the rule for that band is to
  **spend saturation there**, because a quiet neutral is already carrying the bulk.

  Seven colours were tried in that slot first - a chalky ochre, a tint of the burgundy, a pale
  sage, a real gold, a petrol teal, a pale teal, a lilac - every one at chroma 0.03 to 0.05.
  Java's yellow is **0.18**. Over 2.8% of a page a wash that faint is not restraint, it is
  invisible, which is why each of them read as nothing. The answer was not another hue: it was
  to give the slot the accent the design already had, at the chroma an accent needs. So
  `--brand-yellow` is the burgundy, and because it is dark it carries paper text - the version
  chip, inline marks, the marquee band, inline `code` and card hover all became solid accent
  blocks with light type;
- **`--on-accent` exists because the accent is dark.** Ten places use `--brand-yellow` outside
  the bundle's own classes - inline `code` in prose, table headers, link hover, the learn
  pager, two buttons in the alias-rename flow, a "publish failed" label, the code block's
  language tag. Every one was written when that token was a light wash, so it either hard-codes
  `var(--ink)` or just inherits it. Turning the token dark broke all of them at once, in the
  same way and invisibly: charcoal on burgundy, and in two cases `#ef4444` red on burgundy.

  They now read `var(--on-accent, inherit)`, so the accent can be light or dark and nothing
  downstream has to know. Two more rules cover what sits *inside* an accent fill rather than on
  it: a round `bg-ink` dot (the nav logo, the footer logo, the blinking dot in the version
  chip) and an accent-coloured glyph (the star in the marquee). The star is the clearest case
  of why this needs a rule rather than a value - `.text-orange` is burgundy because it is also
  the full stop closing `framework.`, which sits on the bone and is right; the same class
  inside the marquee band was burgundy on burgundy. The code block's language tag was the odd one - it is accent-coloured
  *text* on a charcoal header rather than text on a fill, so it reads `--glyph-light` instead;
- **one word in colour.** `python` in the hero is `--blood`, and it is the only chroma on the
  site. It lands *because* everything around it is charcoal - restraint is what makes a single
  accent work. Targeted as `.hero-h1 .italic`, the only italic in that heading, so it needs no
  new markup. 9.64:1 on the paper, far past what display-sized text needs. Burgundy
  rather than a blood red, which is almost purely a hue rotation - 19 degrees against 29,
  off orange-red and toward purple-red - with lightness and chroma near-unchanged. In gamut,
  so the value describes what renders. Do not deepen it: it sits 8 units of lightness from the
  charcoal around it, and below about 34% the one piece of colour on the page stops reading as
  colour at a glance.

Never applied: the display face. That shot's wordmark is a heavy squared grotesque with
flat-cut terminals; ours is Space Grotesk, rounder and lighter at the same size. Getting that
would mean adding a webfont to `public/fonts`, which is the one part of this that is not a
stylesheet change.

To go back to a plain palette, delete everything below the `:root` block.

---

## Rejected

Recorded so they are not re-proposed. All were built, served and looked at.

| | primary | highlight | hue gap | why not |
| --- | --- | --- | --- | --- |
| indigo + lime | `#6357f0` | `#cff846` | 158° | near-complementary; clashed at every edge |
| violet + orchid | `#984ddb` | `#ffc0ed` | 45° | - |
| blue + periwinkle | `#1a6fe1` | `#e2d4ff` | 34° | - |
| teal + mint | `#009294` | `#8cffc1` | 37° | also 3.68:1 on the primary, under AA |
| crimson + amber | `#de1d3f` | `#ffd354` | 58° | - |
| forest + lime | `#007f35` | `#e1f15d` | 35° | - |
| slate + sky | `#1f6cb0` | `#9df4ff` | 32° | - |

---

## `arc-wash` - a ground treatment, not a palette

Not four tokens: a background applied to the layout wrapper and the sticky header. Sampled
from arc.net's own background asset - pink at the top corners, peach across the top, washing
out to neutral, pale blue at the floor, over warm cream, with an SVG grain tile on top.

Keeps whatever accent tokens are in force. `background-attachment: fixed` is what lets it run
unbroken through the header: the stack is positioned against the viewport rather than each
element, so there is no seam to match.

Built and reverted - the full CSS is in this repository's history.
