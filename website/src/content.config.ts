// The reference section is the repository's own `docs/` tree, rendered.
//
// Not a copy of it - the same files. Astro's glob loader reads from outside `src/`, so
// `docs/database/tables/events.md` becomes `/reference/database/tables/events/` with no
// second version of the prose to keep in step. That is the whole reason this is wired up
// rather than the pages being pasted in as MDX: a website page and its docs counterpart
// cannot drift when they are one file.
import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";

export const collections = {
    docs: defineCollection({
        loader: glob({
            pattern: ["**/*.md", "!**/*-LEDGER.md", "!DECISIONS-TO-REVIEW.md"],
            base: "../docs",
        }),
    }),
};
