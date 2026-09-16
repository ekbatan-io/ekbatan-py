// @ts-check
import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";
import remarkBasePath from "./src/plugins/remark-base-path.mjs";
import remarkStripLead from "./src/plugins/remark-strip-lead.mjs";

// The ekbatan-py website.
//
// The visual identity - brutalist palette, Space Grotesk + DM Sans + JetBrains Mono,
// brutalist borders and hard shadows - is carried over from the Java site verbatim.
// `src/styles/styles.css` is a compiled Tailwind bundle and is treated as a **frozen
// design-system asset**: it is not rebuilt from a Tailwind config, it is shipped and
// wired into every page by BaseLayout. Keeping it byte-identical to the Java site's is
// what makes the two recognisably one project.
//
// `src/styles/palette.css` loads *after* it and is the one place this site's colours
// diverge. Change the four custom properties there and the whole site follows; nothing
// else references a colour directly.
//
// Deployment base path. Default "/" (a subdomain or custom domain). Override with BASE
// when deploying to a subpath - `BASE=/ekbatan-py/ npm run build` for a GitHub Pages
// project site. `remarkBasePath` then prefixes absolute markdown links, so page source
// stays portable and only this one knob switches deployments.
const BASE = process.env.BASE || "/";

export default defineConfig({
    base: BASE,
    integrations: [mdx()],
    markdown: {
        shikiConfig: { theme: "github-light" },
        remarkPlugins: [remarkStripLead, [remarkBasePath, { base: BASE }]],
    },
    site: "https://ekbatan-io.github.io",
});
