# Waybill

> [!CAUTION]
> **This fork is entirely vibe coded.**

See [FORK-CHANGES.md](FORK-CHANGES.md) for what this fork changes from [upstream Waybill](https://github.com/Matthew-Beckett/waybill).

> [!NOTE]
> Built against Dispatcharr v0.33.0 and works on v0.31.0 and later. The apply path degrades gracefully on older releases that lack the newer hooks.

A [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) plugin for managing channel configurations declaratively. You define a YAML manifest describing which streams to keep and how to name them. Waybill plans and applies those changes to Dispatcharr for you: **Plan** computes what would change (channels, numbers, streams in order, logos, EPG, which channels are disabled and why) and writes it to the log without touching anything; **Apply** computes the same and writes it to Dispatcharr.

## Overview

Waybill reads a **WaybillConfig** manifest and produces a reconciled channel list inside Dispatcharr. Each run can either:

- **Plan** — preview the desired state without writing anything to the database.
- **Apply** — write the desired state to the database, in `upsert` or `overwrite` mode.

Channels are selected from Dispatcharr's stream catalogue using **matchers**, renamed / normalised using **transformers**, and then checked against **validators** to assert expected conditions. Stream profiles and predefined **variables** are both resolved through a three-level inheritance hierarchy: profile → group → member, with inner scopes shadowing outer ones.

## Theory behind Waybill

Waybill is the culmination of my attempt to learn and apply computation theory in practice. It is not a theorem prover or a research artefact, but a lot of its execution model maps naturally onto formal-language and automata concepts.

At its core, Waybill is a small declarative language which happens to be encoded as YAML. A valid manifest is a well-formed sentence in that language, and the schema plus type layer define its grammar and which terms are admissible.

The configuration format is of particular intellectual interest to me as a Platform Engineer because a lot of my day-to-day work involves making informed decisions about schema design, especially in Kubernetes.

Parsing determines whether the input belongs to the language, and evaluation assigns meaning to that sentence by constructing a pipeline.

The matcher stage was inspired by finite-state automata theory, but it diverges significantly in real-world use. Each member defines a recogniser over a projected view of a stream record, usually a field such as `name` or `tvg_id`. `exactMatch`, `hasPrefix`, `containsAny`, and especially `regex` correspond to recognisers for regular properties over strings. In operational terms the member accepts a stream if all of its matcher predicates accept after any local pre-transformers have normalised the input. This is close to a textbook FSA-style acceptance pipeline, except that the effective "alphabet" is not raw characters but a finite catalogue of `Stream` records whose fields are inspected selectively.

During my research I also took heavy inspiration from the Mealy machine. In a classical Mealy machine, output is produced on transitions as a function of current state and input symbol. Waybill behaves similarly because a stream moves through a deterministic sequence of transformations and emits rewritten metadata as it advances. Regex replacement, strip, set, cardinal-number conversion, and Jinja templating are all transition-like output functions.

The next representation of the stream is determined by the current working value plus the current environment. The implementation even records each transition as a transformation step, which makes the transduction explicit in the plan output.

The important nuance is that Waybill is not a textbook Mealy machine. Its state is richer than finite control alone. Regex matchers can dynamically bind named capture groups, predefined variables are scoped and inherited lexically from profile to group to member, and optional mutability constrains accidental rebinding or shadowing. Captures are accumulated during matching, but only names declared in a `variables` block are forwarded into the main transformer scope.

That means the effective machine state includes a scoped environment as well as the current transformed stream value. For you CS nerds, it is better described as a deterministic finite-state transducer with an auxiliary environment, or an extended finite-state machine, than as a minimal Mealy automaton.

It looks like a finite-state automaton when it filters streams, it looks like a Mealy machine when it rewrites them, and it stops being either in the strict textbook sense once aggregation, scoped bindings, validation, and database reconciliation are introduced for the real-world task of channel management and pursuit of a tolerable user experience.

If you're interested in reading more about the theoretical concepts applied in Waybill, read about regular-language recognition, deterministic transduction, lexical environments, finite-state automata theory, and predicate checking.

## Manifest structure

```yaml
kind: WaybillConfig
version: v1alpha1
metadata:
  name: my-config
  description: Optional free-text description
spec:
  profiles:
    <profile_key>:
      name: Human-readable profile name
      streamProfile: Proxy          # optional profile-level default
      groups:
        <group_key>:
          name: Human-readable group name
          streamProfile: Redirect   # optional group-level override
          members:
            - name: Channel Name
              streamProfile: null   # optional member-level override (null clears inheritance)
              matchers:
                - ...
              transformers:
                - ...
```

### `kind` and `version`

Both fields are required. The only supported values are `WaybillConfig` and `v1alpha1`.

### Stream profile inheritance

`streamProfile` is resolved from the most specific scope that sets it:

```
member → group → profile → (none)
```

Setting `streamProfile: null` at any level explicitly clears the inherited value. Names are matched exactly first and then ignoring case, so `ffmpeg` still finds the built-in profile that Dispatcharr v0.32 renamed to `FFmpeg` (likewise `streamlink` → `Streamlink`).

### Profile key and channel numbering

The profile key (`<profile_key>` above) is the name of the Dispatcharr channel profile that Waybill writes to. Apply creates the profile if it is missing and **enables** every manifest channel in it. To use a profile you already have, such as one called `todo`, use its exact name as the key.

By default a new channel gets the next free channel number and existing numbers are never changed. Set `startChannelNumber` on a profile to number its channels consecutively in manifest order: groups in the order they are written, then members, then channels. Every apply renumbers existing channels to match, so reordering the manifest reorders the channels.

```yaml
spec:
  profiles:
    todo:
      name: Todo
      startChannelNumber: 1   # first channel is 1, the next 2, ...
      groups:
        ...
```

With `startChannelNumber` alone, deleting a channel from the manifest shifts every channel after it down by one, and clients such as Jellyfin may then show the old logos on the wrong channels. To make numbers permanent, give members a `channelNumber`. It pins that member's channel to the number, and numbering continues from there for the members that follow. With `channelNumber` on every member, deleting one leaves a gap instead of renumbering:

```yaml
members:
  - name: DAZN F1
    channelNumber: 1
    ...
  - name: DAZN MotoGP
    channelNumber: 2
    ...
```

### Stream priorities

Some feeds claim to carry a channel but are unreliable (wrong channel, wrong language, frequent drops), and some M3U accounts are better than others, so quality alone is not a good order. `priorities` gives streams points; a stream's priority is the **sum** of the points every scope gives it, and higher goes first.

```yaml
spec:
  profiles:
    todo:
      orderStreamsBy: [priority, quality]   # points first; picture quality breaks ties
      priorities:
        m3u:                    # points for every stream from an M3U account
          provider-a: 50
          provider-b: 40
          provider-c: 30
          provider-flaky: -20
      groups:
        sports:
          name: Sports
          priorities:
            m3u:
              provider-c: 100   # added to the profile's 30 for this group: 130
          members:
            - name: DAZN F1
              priorities:
                streams:        # points for single feeds, by URL fragment or hash
                  "<acestream-id-of-reliable-feed>": 200
                  "<acestream-id-of-flaky-feed>": -10
              matchers:
                - type: exactMatch
                  values: ["DAZN F1"]
```

A `priorities` block can go on the profile, on a group and on a member, with two maps:

| Key | Gives points to |
|---|---|
| `m3u` | every stream from that M3U account (Dispatcharr's account name; case is ignored) |
| `streams` | single feeds: the **first** key, in the order written, that is contained in the stream's URL or equal to its stream hash |

How a stream's priority is worked out:

1. Only the streams the member's matchers already selected are considered. Points never add a stream to a channel or move one between channels.
2. Each scope that applies (profile, then group, then member) adds its `m3u` points for the stream's account and its `streams` points for the first key that fits. Scopes never replace each other; to lower an account for one group or channel, give it negative points there (`provider-a: -60` on a member turns the profile's 50 into -10).
3. A stream nothing gives points to has priority `0`. Positive totals go before it, negative ones after it.
4. Streams are then sorted by `orderStreamsBy` (see [Stream order](#stream-order)); `priority` is this total.

The plan shows each stream's total and where it came from, for example `priority 250 (+50 m3u provider-a (profile), +200 stream c8ad9458…)`. It also warns about `streams` keys that fit no stream (so keys for feeds that have gone away are easy to spot) and about `m3u` names that match no M3U account.

Pick a scale and stick to it, for example tens for accounts (10, 20, 30, …), 100 for an account that should always win, and 200 for a feed you know is reliable.

Which `streams` key to use: for AceStream feeds, the id after `id=` in the stream URL (`http://host/ace/getstream?id=<id>`) identifies the feed. Any other part of the URL works too, but keep it specific: a short key such as `123` can be contained in the URLs of feeds you did not mean. Always quote keys, because YAML reads an id made only of digits as a number.

Keys are URLs, not names, on purpose: several feeds often share the same name, and priorities exist to tell them apart. Names decide which streams a channel gets (matchers); URLs decide the order of those streams. If a feed's URL changes, its key stops fitting and the feed loses those points; nothing else breaks.

`streamPriorities` on a member is the older spelling of the member's `priorities.streams` and still works; its points add up like any other. A key may not be in both.

### Preferring M3U accounts

The simplest way to prefer an M3U account is to give it points with `priorities.m3u` (see [Stream priorities](#stream-priorities)). `m3uOrder` is the older way, still supported: a ranking of accounts, most preferred first, used as its own `orderStreamsBy` criterion (`m3u`) instead of adding points:

```yaml
spec:
  profiles:
    todo:
      m3uOrder: ["provider-a", "provider-b"]
```

Names are Dispatcharr's M3U account names (case is ignored); accounts not listed go after every listed one. Where the account ranks against priority and quality is set by `orderStreamsBy`. Set it on a profile, and override it on a group or member; `m3uOrder: []` clears an inherited order. The plan shows each stream's account (`m3u: provider-a`) and warns about any name that matches no M3U account.

### Stream order

`orderStreamsBy` states how a channel's streams are ordered, as a list of criteria applied in turn: each one only decides between streams that tie on the ones before it.

| Criterion | Orders by |
|---|---|
| `priority` | total `priorities` points (and `streamPriorities`), highest first |
| `quality` | resolution, then bitrate, highest first (needs Dispatcharr stream stats) |
| `m3u` | the stream's M3U account in `m3uOrder`, earliest first |

```yaml
orderStreamsBy: [priority, quality]   # most points first, then best picture
```

Criteria you leave out are not used. Like `streamProfile`, it can be set on a profile and overridden on a group or member. For compatibility, the single value `orderStreamsBy: quality` means `[priority, m3u, quality]`, and leaving it out means `[priority, m3u]`. Remaining ties keep the order Dispatcharr returned the streams in. `quality` uses the resolution and bitrate Dispatcharr measured when the stream was played, not what its name says; a stream that has never been played has no measurements and goes after measured ones.

### Keeping channels without streams

By default a member that matches no streams produces no channel. Set `keepEmptyChannels: true` on a profile to keep it instead: the channel (named after the member) stays in Dispatcharr, apply removes its streams and **disables** it in the profile, and its number, logo and EPG are left untouched. The next apply that finds matching streams again fills it in and re-enables it. The plan marks these channels as `no streams, kept disabled`.

### Enabling channels per profile

Every channel Waybill manages is a member of its profile, enabled or disabled there. A disabled channel stays fully managed (streams, number, logo, EPG); clients of that profile just do not get it. Three settings decide it:

- `enabled: false` on a member or a group disables those channels. A member's own `enabled` overrides its group's (`enabled: true` re-enables one channel of a disabled group).
- `tags` on a member or a group label channels; a member has its own tags, its group's, and the **group key** as a tag.
- On the profile, `excludeTags` disables every channel with any of those tags, and `includeTags`, when set, keeps only channels with one of them enabled. Tag rules apply after `enabled`: `enabled: true` does not override them.

```yaml
profiles:
  todo:
    excludeTags: [tdt]          # free-to-air channels: watch them on normal TV
    groups:
      generalistas:
        name: Generalistas
        tags: [tdt]
        members: [...]
      deportes:
        name: Deportes
        members:
          - name: Teledeporte
            tags: [tdt]
            ...
```

To have several profiles over the same channels, write the channels once and start other profiles `from` it. `from: <profile key>` copies that profile's settings and groups, and the keys you set replace the copied ones (`name` is not copied). Each profile then decides what is enabled in it:

```yaml
profiles:
  todo:
    name: Todo
    excludeTags: [tdt]
    groups: {...}
  tdt:
    from: todo                  # same channels, numbers, matchers...
    name: Con TDT
    excludeTags: []             # ...but the free-to-air ones enabled
  deportes:
    from: todo
    name: Solo deportes
    includeTags: [dazn, movistar_deportes, deportes]   # group keys are tags
```

Each profile becomes a Dispatcharr channel profile with the same key; the channels themselves are shared. A disabled channel shows in the plan as `— disabled in this profile (excludeTags: tdt)`. `epgSchedule` only applies to channels the profile keeps enabled.

### Enabling channels by EPG schedule

`epgSchedule` enables a channel in its profile only around the guide programmes it is for, and disables it the rest of the time. Waybill reads the programmes from Dispatcharr's EPG (the entry the channel's EPG id points to) each time it plans or applies.

With `action: drop`, the matchers describe what to **discard**: every programme that does *not* match turns the channel on. Use it to hide a channel while it shows filler:

```yaml
- name: DAZN 1
  epgSchedule:
    action: drop
    before: 15m          # switch on 15 minutes before a real programme starts
    after: 15m           # and keep it on until 15 minutes after it ends
    matchers:            # what counts as filler
      - type: containsAny
        substrings: ["Disfruta de DAZN", "Programa por determinar"]
```

With `action: keep` (the default), the matchers describe what to **keep**: only programmes that match turn the channel on. Use it for event channels that only matter during live events:

```yaml
- name: DAZN F1
  epgSchedule:
    before: 15m
    after: 15m
    matchers:
      - type: hasPrefix
        prefixes: ["DIRECTO"]
```

How a channel is decided, at the moment of the plan or apply:

1. Programmes of the channel's EPG entry are checked against the `matchers`, the same matcher types as for streams (with pre-transformers too). They read these programme fields: `name` (the title, and the default), `title`, `sub_title` and `description`. A programme matches when every matcher accepts it; without `matchers`, every programme matches.
2. The programmes that turn the channel on are the matching ones with `keep`, and the non-matching ones with `drop`.
3. The channel is **enabled** from `before` ahead of the start of such a programme until `after` past its end, and **disabled** otherwise (including gaps in the guide). `before` and `after` take minutes (`45`) or a duration (`30m`, `2h`, `1h30m`); both default to 0, i.e. only while it airs. The channel keeps its streams, number, logo and EPG either way.
4. If there is nothing to decide on, the channel is left enabled and the plan says why. This covers a channel with no EPG id or EPG entry, and a guide with no programme still to end (e.g. an EPG source that stopped refreshing). A broken guide never switches a lineup off.

Like `streamProfile`, `epgSchedule` can be set on a profile, a group or a member, and the most specific one applies; `epgSchedule: false` clears an inherited rule. Channels kept by `keepEmptyChannels` stay disabled regardless. The plan shows the decision under each channel, e.g. `Schedule: on: "DIRECTO Jornada 10: Barcelona - Real Madrid" Sun 21:00-23:00`, or `Schedule: off: "Disfruta de DAZN en Movistar Plus" Wed 18:00-22:00` with `— disabled by epgSchedule` next to the channel.

The schedule only changes when Waybill runs, so run Apply regularly: set **Auto-apply every (minutes)** in the plugin settings (e.g. `5`; `0`, the default, turns it off), or call Dispatcharr's API (`POST /api/plugins/plugins/<key>/run/` with `{"action": "apply"}`) from a scheduler of your own.

The timer is a Celery beat schedule named `waybill: auto-apply` that Waybill creates when it loads: every minute it re-reads the setting and the plugin's enabled flag, and runs Apply once the interval has passed, so changes need no restart. Applies are serialised through Redis, so a timed run never overlaps a manual or API one. On Dispatcharr 0.33+ the schedule belongs to the plugin: it is paused while the plugin is disabled and deleted with it. On older versions, delete the `waybill: auto-apply` periodic task yourself if you remove the plugin.

---

## Matchers

Matchers filter which streams from Dispatcharr are candidates for a channel. Multiple matchers on one member are evaluated in sequence — each one narrows (or widens, for `keep`) the candidate set.

| Type | Description |
|---|---|
| `regex` | Matches the field against a regular expression |
| `hasPrefix` | Matches if the field starts with any of the listed prefixes |
| `containsAny` | Matches if the field contains any of the listed substrings |
| `exactMatch` | Matches if the field equals any of the listed values |

All matchers share these common fields:

| Field | Default | Description |
|---|---|---|
| `field` | `name` | Stream attribute to match against (`name`, `tvg_id`, `logo_url`, …) |
| `action` | `keep` | `keep` retains matching streams; `drop` removes them |
| `caseSensitive` | `false` | Whether matching is case-sensitive |

### `regex`

```yaml
- type: regex
  field: name
  action: keep
  pattern: "^UK\\| NBS [1-9]( HD| HEVC)?$"
```

| Field | Required | Description |
|---|---|---|
| `pattern` | yes | Python regular expression; named capture groups (`(?P<name>...)`) are extracted into the variable scope |

### `hasPrefix`

```yaml
- type: hasPrefix
  field: name
  action: keep
  prefixes:
    - "UK| NBS ONE"
    - "UK| NBS One"
```

| Field | Required | Description |
|---|---|---|
| `prefixes` | yes | List of prefix strings, any of which will match |

### `containsAny`

```yaml
- type: containsAny
  field: name
  action: keep
  substrings:
    - "Globe Talk"
```

| Field | Required | Description |
|---|---|---|
| `substrings` | yes | List of substrings, any of which will match |

### `exactMatch`

```yaml
- type: exactMatch
  field: name
  action: keep
  values:
    - "UK| NBS ONE HD"
    - "UK| NBS ONE HEVC"
```

| Field | Required | Description |
|---|---|---|
| `values` | yes | List of exact values, any of which will match |

### Pre-transformers

Any matcher can carry a `transformers` list. These **pre-transformers** mutate the field value *before* the matcher evaluates it, without affecting the final stream name. This is useful for normalising noisy data so a single pattern can match multiple forms:

```yaml
- type: regex
  field: name
  action: keep
  pattern: "^UK\\| NBS [1-4]( HD| HEVC)?$"
  transformers:
    - type: convertCardinalNumbers
      field: name
      outputType: number
```

---

## Transformers

Transformers rewrite stream fields after matching. They are applied in declaration order.

| Type | Description |
|---|---|
| `regex` | Replace a regex match (supports capture-group back-references) |
| `strip` | Remove a fixed prefix and/or suffix |
| `normalizeUnicode` | Fold decorated letters (squared, full-width, ...) into plain text (NFKC) |
| `setMetadata` | Set explicit metadata fields (`name`, `logoUrl`, `tvgId`) in one step |
| `set` | Overwrite an arbitrary field with a literal value (low-level escape hatch) |
| `convertCardinalNumbers` | Convert between word and digit forms of cardinal numbers |
| `template` | Render a Jinja2 template string using named capture groups and predefined variables |

Transformers that target a single stream field (`regex`, `strip`, `normalizeUnicode`, `set`, `convertCardinalNumbers`) share:

| Field | Default | Description |
|---|---|---|
| `field` | `name` | Stream attribute to transform |

### `regex`

```yaml
- type: regex
  field: name
  action: replace
  pattern: "^UK\\| "
  replacement: ""
```

| Field | Required | Description |
|---|---|---|
| `pattern` | yes | Python regular expression |
| `replacement` | yes (for `replace`) | Replacement string; use `$1`, `$2` … for back-references |
| `action` | yes | Currently only `replace` is supported |

### `strip`

```yaml
- type: strip
  field: name
  prefix: "UK| "
  suffix: " HD"
```

| Field | Required | Description |
|---|---|---|
| `prefix` | no | Literal prefix to remove if present |
| `suffix` | no | Literal suffix to remove if present |

### `normalizeUnicode`

Applies Unicode NFKC normalisation, which turns "decorated" letters into plain ones:
squared (`🄳🄰🅉🄽 1` → `DAZN 1`), full-width (`ＤＡＺＮ` → `DAZN`), circled, superscript
and similar. Accents, emoji and flags are kept, so put it before any regex that removes
emoji: squared letters sit in the same Unicode block as many emoji.

```yaml
- type: normalizeUnicode
  field: name
```

It has no options besides `field`.

### `set`

```yaml
- type: set
  field: name
  value: "NBS One"
```

| Field | Required | Description |
|---|---|---|
| `value` | yes | Literal value to write to the field |

`set` is intentionally low-level and can write to arbitrary fields. Prefer `setMetadata`
for common metadata assignments where field names are explicit.

### `setMetadata`

Sets one or more explicit metadata fields in a single transformer.

```yaml
- type: setMetadata
  name: "NBS One"
  logoUrl: "https://example.com/logos/bbc-one.png"
  tvgId: "bbc.one.uk"
```

| Field | Required | Description |
|---|---|---|
| `name` | no | Canonical stream name |
| `logoUrl` | no | Logo URL to write to `logo_url` |
| `tvgId` | no | TV guide identifier to write to `tvg_id` |

At least one of `name`, `logoUrl`, or `tvgId` must be provided.

### `convertCardinalNumbers`

Converts between digit and word forms of cardinal numbers (e.g. `ONE ↔ 1`).

```yaml
- type: convertCardinalNumbers
  field: name
  outputType: number
```

| Field | Values | Description |
|---|---|---|
| `outputType` | `number`, `word` | Target form to normalise to |

### `template`

Renders a [Jinja2](https://jinja.palletsprojects.com/) template string against the current variable scope.
Variables come from two sources: **named capture groups** extracted by preceding regex matchers, and
**predefined variables** declared in the manifest.  All standard Jinja2 filters are available.

```yaml
- type: template
  field: name
  value: "{{ ch_name }} ({{ quality }})"
```

| Field | Default | Description |
|---|---|---|
| `value` | — | Jinja2 template string |
| `field` | `name` | Stream attribute to write the rendered output to |

Referencing an undefined variable is a hard error (analogous to `set -u` in shell).

---

## Variables

Variables provide a way to pass reusable values into template transformers.  They follow lexical
scope through the manifest hierarchy — profile → group → member — with inner scope definitions
shadowing outer ones.  Named capture groups from regex matchers extend the scope further at
match time, so a single member can produce many distinct channels from one template.

### Declaring variables

`variables` may be declared on any `profile`, `group`, or `member` block:

```yaml
spec:
  profiles:
    sports:
      variables:
        brand:
          value: "Arena"
          mutable: false   # no capture group may shadow this
      groups:
        uk_sports:
          variables:
            quality_suffix:
              value: " (Live)"   # shadows any profile-level quality_suffix
          members:
            - name: Sports Factory
              variables:
                network: "UK"   # shorthand — equivalent to {value: "UK", mutable: true}
```

Each variable has two properties:

| Property | Default | Description |
|---|---|---|
| `value` | — | The string value of the variable (required) |
| `mutable` | `true` | When `false`, a named capture group with the same name raises an error |

A scalar shorthand (e.g. `network: "UK"`) is equivalent to `{value: "UK", mutable: true}`.

### Scope resolution

Variables are resolved at match time in the following precedence order (highest last wins):

1. Profile-level `variables`
2. Group-level `variables` (shadows profile)
3. Member-level `variables` (shadows group)
4. Named capture groups from regex matchers (shadows predefined variables, subject to mutability)

### Named capture groups

A `regex` matcher may include named capture groups (`(?P<name>...)`).  On a successful match,
each captured value is added to the variable scope for that stream and passed to all
transformers, enabling a single member to produce many uniquely-named channels:

```yaml
- name: UK Sports
  matchers:
    - type: regex
      field: name
      pattern: "^UK\\| (?P<ch_name>.+?) (?P<quality>HD|SD)$"
  transformers:
    - type: template
      field: name
      value: "{{ ch_name }} ({{ quality }})"
```

A stream named `UK| Northgate HD` would produce a channel named `Northgate (HD)`.

Captures are also recorded on each `StreamRecord` in the plan output for traceability.

---

## Validators

Validators run after all transformers have been applied and assert simple conditions about the resulting transformed streams. They are **observational-only** — they never drop a stream or channel. A violation either logs a warning (`action: warn`) or logs an error and halts execution before any database write (`action: fail`).

Validators are declared on a member, alongside `matchers` and `transformers`:

```yaml
- name: My Channel
  matchers: [...]
  transformers: [...]
  validators:
    - type: count
      operator: gt
      value: 0
      action: fail
```

| Type | Scope | Description |
|---|---|---|
| `count` | `member` by default, optional `channel` | Asserts either the number of channels produced by a member or the number of streams in each assembled channel |
| `regexMatch` | `stream` by default, optional `channel` | Asserts that a stream or assembled channel field matches a regular expression after transformation and merging |
| `nonEmpty` | `stream` by default, optional `channel` | Asserts that a stream or assembled channel field is non-empty after transformation and merging |

`scope` is available on all validators, but the allowed values depend on the validator type.

**Count scopes:**
- **member** — evaluated once per member after grouping and merging; counts the number of channels produced
- **channel** — evaluated once per assembled channel; counts the number of streams in that channel

**regexMatch / nonEmpty scopes:**
- **stream** — evaluated once per transformed stream before grouped streams are reported under a shared channel
- **channel** — evaluated once per assembled channel after merging; useful when you care about the final emitted channel metadata rather than each contributing stream

Violations are printed in the plan output alongside the channel or stream that triggered them, and summarised in the `=== Summary ===` section.

### `count`

Asserts either the number of channels produced by a member or the number of streams resolved for each channel satisfies `count <operator> <value>`.
The default `scope: member` is the feed-health check: it still fires when a member produces zero channels after matching, transformation, and merging.

```yaml
- type: count
  scope: member
  operator: gt
  value: 0
  action: warn
```

| Field | Required | Description |
|---|---|---|
| `scope` | no (default: `member`) | `member` counts produced channels; `channel` counts streams in each assembled channel |
| `operator` | yes | One of: `gt`, `gte`, `lt`, `lte`, `eq`, `neq` |
| `value` | yes | Integer to compare the count against |
| `action` | no (default: `warn`) | `warn` or `fail` |

### `regexMatch`

Asserts that a field matches a regular expression after transformation. The default `scope: stream` catches per-stream transformer drift; `scope: channel` validates the final assembled channel metadata after merging.

```yaml
- type: regexMatch
  scope: channel
  field: name
  pattern: "^NBS [1-9]"
  action: fail
```

| Field | Required | Description |
|---|---|---|
| `scope` | no (default: `stream`) | `stream` validates each transformed stream; `channel` validates the assembled channel |
| `pattern` | yes | Python regular expression |
| `field` | no (default: `name`) | Stream or channel attribute to evaluate; `tvg_id` maps to channel `epg_id` when `scope: channel` |
| `action` | no (default: `warn`) | `warn` or `fail` |

### `nonEmpty`

Asserts that a field is non-empty. The default `scope: stream` checks each transformed stream; `scope: channel` checks the assembled channel metadata that will actually be emitted.

```yaml
- type: nonEmpty
  scope: channel
  field: tvg_id
  action: warn
```

| Field | Required | Description |
|---|---|---|
| `scope` | no (default: `stream`) | `stream` validates each transformed stream; `channel` validates the assembled channel |
| `field` | no (default: `name`) | Stream or channel attribute to check; `tvg_id` maps to channel `epg_id` when `scope: channel` |
| `action` | no (default: `warn`) | `warn` or `fail` |

---

## Loading the manifest from a URL

Instead of pasting the manifest into the plugin settings, you can keep it in a repository and set **Manifest URL**. Plan and Apply (and the auto-apply timer) then download it on every run and ignore the YAML box.

- The first line of the log says where the manifest came from, with its size and the start of its SHA-256, so you can tell which version a run used:

  ```text
  Manifest: downloaded from https://raw.githubusercontent.com/<user>/<repo>/main/waybill.yaml (54.2 KB, sha256 3f1c0a9b7e22)
  ```

- If the download fails (network error, HTTP error, empty file, over 2 MB, not UTF-8), the run stops and **nothing is changed**. Waybill never falls back to the YAML box or an older copy.
- A GitHub page link (`https://github.com/<user>/<repo>/blob/<ref>/<path>`) is converted to its raw file link.
- `raw.githubusercontent.com` caches files, so for about 5 minutes after a push it can still serve the previous version. To pin a version, use a commit SHA instead of the branch name in the URL.
- For a private repository, set **Manifest URL token**. It is sent as `Authorization: Bearer <token>`; for GitHub, a fine-grained token with read-only access to the repository's contents is enough. Query strings and credentials in the URL are never written to the log.

---

## Apply modes

| Mode | Behaviour |
|---|---|
| `upsert` | Creates or updates channels declared in the manifest; leaves any other channels untouched |
| `overwrite` | Creates or updates declared channels **and deletes** any channels that exist in a group but are not in the manifest |

On every apply, Waybill also:

- Creates missing channel profiles **empty**. Only channels declared in the manifest are enabled in them; other existing channels are not auto-added as enabled.
- Replaces each channel's stream list with the planned streams, in plan order.
- Enables each channel in the profile, or disables it when the profile says so (`enabled`, `excludeTags`, `includeTags`; see [Enabling channels per profile](#enabling-channels-per-profile)) or its `epgSchedule` rule says it is off right now (see [Enabling channels by EPG schedule](#enabling-channels-by-epg-schedule)).
- Sets the channel's logo from, in order: `setMetadata` `logoUrl`, the most common logo among its streams, or the icon of its EPG entry (like Dispatcharr's "Set logos from EPG"). When none of them has a logo, the channel keeps the logo it already has; Waybill never clears it.
- Recomputes the channel's catch-up flags (`is_catchup` / `catchup_days`) from its assigned streams (Dispatcharr v0.31+).
- Copies the radio flag (`is_radio`) from the channel's first stream, as Dispatcharr does for channels created from a stream (Dispatcharr v0.32+). A channel with no streams keeps its current value.
- Triggers Dispatcharr's EPG programme refresh when a channel's EPG assignment changes.

> [!NOTE]
> Since Dispatcharr v0.31, deleting a channel no longer stops a stream that is currently playing on it. Channels removed by `overwrite` keep streaming to connected clients until they disconnect.

---

## Examples

The `examples/` directory contains annotated manifests covering every feature:

| File | What it demonstrates |
|---|---|
| [01-minimal.yaml](examples/01-minimal.yaml) | Minimum valid manifest |
| [02-matchers.yaml](examples/02-matchers.yaml) | All four matcher types, keep/drop actions, field targeting |
| [03-transformers.yaml](examples/03-transformers.yaml) | All five transformer types including multi-step pipelines |
| [04-pre-transformers.yaml](examples/04-pre-transformers.yaml) | Transformers scoped inside a matcher |
| [05-stream-profiles.yaml](examples/05-stream-profiles.yaml) | Stream profile inheritance (profile → group → member) |
| [06-multiple-profiles.yaml](examples/06-multiple-profiles.yaml) | Multiple independent profiles in one manifest |
| [07-exhaustive.yaml](examples/07-exhaustive.yaml) | Every schema feature in one manifest |
| [08-validators.yaml](examples/08-validators.yaml) | All three validator types with both action levels and combined validator suites |
| [09-template-transformer.yaml](examples/09-template-transformer.yaml) | Named capture groups, template transformer, and scoped variables at profile/group/member level |
| [10-uk-freeview.yaml](examples/10-uk-freeview.yaml) | Real-world manifest for UK free-to-air channels from the iptv-org playlist |

---

## Compatibility

| Waybill | Dispatcharr |
|---|---|
| 1.17.x | v0.33.0 (built and locked against), v0.31.0 and v0.32.0; older releases fall back gracefully where newer hooks are missing |
| 1.13.x – 1.16.x | v0.32.0 (built and locked against) and v0.31.0; older releases fall back gracefully where newer hooks are missing |
| 1.6.x – 1.12.x | v0.31.0 (built and locked against); older releases fall back gracefully where newer hooks are missing |
| 1.5.0 | v0.24.0 |

The Dispatcharr version used for development is pinned in `[tool.uv.sources]` in `pyproject.toml`. Dispatcharr itself is provided by the host at runtime and is never vendored into the plugin ZIP.

---

## Installation

Download `waybill.zip` from the [latest release](https://github.com/donmerendolo/waybill/releases/latest) and upload it in Dispatcharr under **Plugins → Import Plugin**. Keep the file name `waybill.zip`: Dispatcharr names the plugin after it, so re-importing with **Overwrite** updates the existing install.

### Updating from the Dispatcharr dashboard

Each release also publishes a plugin-repo manifest (`manifest.json` on this repo's [`releases` branch](https://github.com/donmerendolo/waybill/tree/releases)), so Dispatcharr can show new versions and install them for you:

1. In Dispatcharr's **Plugins** page, click **Manage Repos** and add this as the **Repository Manifest URL**:

   ```text
   https://raw.githubusercontent.com/donmerendolo/waybill/releases/manifest.json
   ```

   The repo shows up as `donmerendolo/waybill`. It is unsigned, so Dispatcharr marks it as unverified.

2. Find **Waybill** from `donmerendolo/waybill` in the available plugins and click **Install**. If you imported Waybill by hand before, Dispatcharr notes that it was installed manually and that installing from the repo takes over management; it replaces the plugin in place and keeps your settings (manifest and apply mode).

3. When a new release is published, the plugin shows **Update Available**; update it from there.

The official Dispatcharr plugin repo also lists a `waybill` (the original, older project). Install this one from `donmerendolo/waybill`; Dispatcharr shows the other listing as installed from a different repo.

Releases are built by the `Build and Release` workflow on every push to `main`. It publishes a release tagged `v<version>` from `pyproject.toml` (which must match `plugin.json`), with `waybill.zip` and the repo `manifest.json` (built by `build_repo_manifest.py`) attached, commits that manifest to the `releases` branch, and skips the run if that release already exists, so bump the version in both files to publish a new one.

To build the plugin ZIP yourself:

```sh
uv build --wheel
```

The resulting `.zip` file in `dist/` can be uploaded directly through the Dispatcharr UI.

---

## Development

Requires Python 3.13 and a recent [uv](https://docs.astral.sh/uv/) (older releases such as 0.8.x cannot read the current `uv.lock`; run `uv self update`).

```sh
# Install dependencies
make deps

# Run the unit tests
make test

# Regenerate the JSON Schema from the Python type definitions
uv run generate_schema.py

# Run pre-commit checks
pre-commit run -a
```

The JSON Schema at `schema/config.schema.json` can be referenced by editors (e.g. VS Code with the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml)) to get inline validation and autocompletion when writing manifests.

Do not hand-edit `schema/config.schema.json`; always regenerate it from the source types and descriptions in `src/types/config.py` and `generate_schema.py`.
