// The guided path, in order. One list: the index renders it, the pager walks it, and adding a
// page is a line here plus the file.
//
// The order mirrors Java's - the problem, then the model, then building something, then the
// harder shapes, then the whole project - minus native-image, which has no counterpart, plus
// three this port needs and Java does not: installing, wiring by hand, and what concurrency
// actually costs on CPython.
export const LEARN_PAGES = [
    { slug: "getting-started", title: "Getting started", blurb: "One wallet change, end to end. The whole framework in ten minutes." },
    { slug: "the-dual-write-trap", title: "The dual-write trap", blurb: "Why writing to a database and a broker separately drifts, silently." },
    { slug: "outbox", title: "The outbox", blurb: "State and events in one transaction, and the two ways to drain them." },
    { slug: "models-and-entities", title: "Models and entities", blurb: "Which to use, why they are frozen, and what the builder buys." },
    { slug: "actions", title: "Actions", blurb: "The two phases, the plan, retries, and why nesting is refused." },
    { slug: "your-first-action", title: "Your first action", blurb: "A model, a repository, an action - and the outbox row you never wrote." },
    { slug: "install", title: "Installing it", blurb: "The extras, the two generators, and the Makefile." },
    { slug: "wiring-a-service", title: "Wiring a service", blurb: "One function, one object, three pools." },
    { slug: "consuming-events", title: "Consuming events", blurb: "In process, or through a broker. Both at-least-once." },
    { slug: "sharding", title: "Adding a shard", blurb: "One database to two, without changing an action signature." },
    { slug: "sharding-strategies", title: "Sharding strategies", blurb: "Two axes, three strategies, and why cross-shard is refused." },
    { slug: "sagas", title: "Sagas", blurb: "Workflows that will not fit in one transaction, and honest compensation." },
    { slug: "going-to-production", title: "Going to production", blurb: "Threads, migrations, what to alert on, and what is missing." },
    { slug: "complete-project-setup", title: "Complete project setup", blurb: "Every file of a working service, for your dialect and your installer." },
] as const;
