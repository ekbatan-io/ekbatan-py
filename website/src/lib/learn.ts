// The guided path, in order. One list: the index page renders it, the pager walks it, and
// adding a page is a line here plus the file.
export const LEARN_PAGES = [
    { slug: "install", title: "Install it", blurb: "The extras, the two generators, and the Makefile." },
    { slug: "your-first-action", title: "Your first action", blurb: "A model, a repository, an action - and the outbox row you never wrote." },
    { slug: "wiring-a-service", title: "Wiring a service", blurb: "One function, one object, three pools." },
    { slug: "consuming-events", title: "Consuming events", blurb: "In process, or through a broker. Both at-least-once." },
    { slug: "going-to-production", title: "Going to production", blurb: "Threads, migrations, sharding, and what to alert on." },
] as const;
