// The guided path, in order. One list: the index renders it, the pager walks it, and adding a
// page is a line here plus the file.
//
// The order mirrors Java's - the problem, then the model, then building something, then the
// harder shapes, then the whole project - minus native-image, which has no counterpart, plus
// six this port needs and Java does not: installing, choosing sync or async, wiring by hand,
// going async, what concurrency actually costs on CPython, and deploying under each server.
export const LEARN_PAGES = [
    { slug: "getting-started", title: "Getting started", blurb: "Start here. Follow one wallet change through the dual-write problem, model events, action staging, and what the executor commits." },
    { slug: "the-dual-write-trap", title: "The dual-write trap", blurb: "Why writing to a database and to a broker separately creates silent drift, and why the event has to be stored with the state that caused it." },
    { slug: "outbox", title: "The outbox", blurb: "How domain rows and event rows commit together, so the database holds both the state and the fact that produced it - and the two ways to drain the table afterwards." },
    { slug: "models-and-entities", title: "Models and entities", blurb: "Models emit events when they change; entities do not. Which shape to use, why both are frozen, and how a mutation that returns a new object stays readable." },
    { slug: "actions", title: "Actions", blurb: "The two-phase lifecycle: build the new state first, then let the executor commit domain rows and event rows in one transaction. Retries, and why nesting is refused." },
    { slug: "your-first-action", title: "Your first action", blurb: "Build a wallet deposit end to end: define the event, define the model, write the action, call it from an endpoint, and read the outbox row you never wrote." },
    { slug: "install", title: "Installing it", blurb: "The extras that bring each driver, the two generators and what each reads, and the Makefile that stands in for a task runner." },
    { slug: "sync-or-async", title: "Sync or async?", blurb: "Two APIs, one framework. How to pick one per service, what is the same in both, what differs, and what always runs on threads." },
    { slug: "wiring-a-service", title: "Wiring a service", blurb: "Every collaborator a service needs, built in one function and closed in the right order - with the pools sized for what actually contends." },
    { slug: "consuming-events", title: "Consuming events", blurb: "Use committed events after the fact: handle them in the same service, publish from a handler, or stream them out with CDC. Every path is at-least-once." },
    { slug: "sharding", title: "Adding a shard", blurb: "Take a single-database service to two shards. Your action signatures, repository queries and endpoints do not change; the model's id type does." },
    { slug: "sharding-strategies", title: "Sharding strategies", blurb: "The deeper model behind groups, members, embedded-bits ids and custom routing - and why a cross-shard action is refused until you say otherwise." },
    { slug: "sagas", title: "Sagas", blurb: "Split a multi-step workflow into committed actions chained by events, with explicit compensation when a later step fails and nothing to roll back." },
    { slug: "async", title: "Going async", blurb: "Move an existing sync service to ekbatan.aio, file by file: async repositories and actions, an awaited executor, async def endpoints - while jobs stay on threads." },
    { slug: "going-to-production", title: "Going to production", blurb: "What concurrency costs on CPython, how migrations run at deploy, what to alert on, and what this port does not do for you yet." },
    { slug: "deploying", title: "Deploying", blurb: "Create ekbatan when your app starts, stop it when it stops. Where that is under uvicorn, gunicorn, uwsgi, Celery, RQ and the rest - and the two mistakes it refuses." },
    { slug: "complete-project-setup", title: "Complete project setup", blurb: "The whole setup, for your dialect and your installer: scaffold, dependencies, connection config, migrations, generated schema, domain, HTTP layer, tests." },
] as const;
