# The ekbatan-py website

<https://ekbatan-io.github.io/ekbatan-py/> — an Astro static site, deployed as this
repository's GitHub Page by `.github/workflows/deploy-website.yml`.

```bash
cd website
npm install
npm run dev       # http://localhost:4321
npm run build     # -> dist/
BASE=/ekbatan-py/ npm run build     # what CI builds
```

Node is the **only** part of this repository that does not run through `uv`. It is confined to
this directory and to its own workflow, so nothing else has to know about it.

## Two sections, and only one of them has content of its own

**`/learn/`** is a guided path — five MDX pages in `src/pages/learn/`, meant to be read in order.
This is genuinely its own writing: a narrative, not a reference.

**`/reference/`** is **the repository's `docs/` tree, rendered**. Not a copy of it — the same
files. `src/content.config.ts` points Astro's glob loader at `../docs`, and
`src/pages/reference/[...slug].astro` renders each entry.

That is the whole reason this is wired up rather than the pages being pasted in as MDX: a
website page and its docs counterpart **cannot drift when they are one file**. Adding a page to
`docs/` adds it to the site, with its title and lead taken from the markdown's own first heading
and first paragraph — so a docs page needs no frontmatter and no knowledge that a website exists.

## Copied, not rewritten

Most of this directory is the Java site's, verbatim. That is deliberate and was learned the
hard way: the bundle is Tailwind output **tree-shaken from the Java site's own HTML**, so a
class that site never used is simply absent and using it does nothing, silently. A hand-written
`md:hidden` left the MENU button visible at every width; a hand-rolled index page lost its
spacing and its breakpoints.

| | |
| --- | --- |
| byte-identical | `styles.css`, `fonts.css`, the fonts, `CodeBlock`, `SectionCard`, `DocLayout`, `privacy`, `remark-base-path` |
| adapted - content changed, structure and CSS kept | `Nav`, `Footer`, `BaseLayout`, `index`, the two section indexes, `LearnPager` |
| written here | the docs-rendering machinery, the palette, the Learn pages |

`npm run check` is what keeps this honest: it reports every utility class used but not defined
in the bundle, and the answer must not include anything the Java site does not also use.

## The one file that owns the colours

`src/styles/palette.css`. Four custom properties; everything else resolves through them.

`src/styles/styles.css` is the Java site's compiled Tailwind bundle, carried over **verbatim**
and treated as a frozen design asset — it is not rebuilt from a Tailwind config. Keeping it
byte-identical is what makes the two sites recognisably one project. `palette.css` loads after
it and is the only place this site's colours diverge.

The three font families (Space Grotesk, DM Sans, JetBrains Mono) are self-hosted woff2 in
`public/fonts/`, also carried over.

## Links

`src/plugins/remark-doc-links.mjs` exists because the docs are written to be read **in the
repository**. A page says `[the outbox](outbox.md)` or
`[app.py](../../ekbatan-py-examples/.../app.py)` — spellings that resolve on GitHub and would
404 here. Two rules:

| a link that… | becomes |
| --- | --- |
| stays inside `docs/` | the matching `/reference/` URL |
| escapes `docs/` | the file on GitHub |

The second is not a fallback for a broken link. `ekbatan-py-examples/` and `DIVERGENCE.md` are
source, not documentation, and a reader following one wants to see the file.

`remark-base-path.mjs` then prefixes absolute paths with `BASE` for the subpath deploy. Order
matters and is set in `astro.config.mjs`.

## Astro 7

`@astrojs/markdown-remark` is an explicit dependency. Astro 7 made a different Markdown
processor the default, and `markdown.remarkPlugins` needs the unified processor installed — which
the two plugins above require.
