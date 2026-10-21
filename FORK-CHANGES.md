# Changes from upstream Waybill

This fork is based on [Matthew-Beckett/waybill](https://github.com/Matthew-Beckett/waybill) at
version 1.5.0 (commit `cabc873`). Everything below was added in this fork; the
[README](README.md) has the full reference for each option.

## Dispatcharr compatibility

- Built and locked against **Dispatcharr v0.32.0** (upstream targets v0.24.0); it also works on v0.31.0. The changes still work on older versions.
- New channel profiles start empty on 0.31+ instead of receiving every existing channel.
- `streamProfile` names are matched ignoring case when there is no exact match, so `ffmpeg`/`streamlink` keep working after 0.32 renamed them to `FFmpeg`/`Streamlink`.
- Channels copy the radio flag (`is_radio`, 0.32+) from their first stream.
- Catch-up flags (`is_catchup`, `catchup_days`) are kept up to date after streams are assigned.
- EPG changes now trigger Dispatcharr's programme refresh and guide cache invalidation.

## New manifest options

| Option | Where | What it does |
|---|---|---|
| `startChannelNumber` | profile | Numbers channels consecutively in manifest order. |
| `channelNumber` | member | Pins a channel to a fixed number, so deleting a channel leaves a gap instead of renumbering the rest. |
| `keepEmptyChannels` | profile | A member with no streams keeps its channel, disabled, with its number, logo and EPG. It is re-enabled when streams return. |
| `streamPriorities` | member | Integer priority per feed, keyed by URL or stream hash (not by name). Higher goes first; negative sends unreliable feeds to the end. |
| `m3uOrder` | profile, group, member | Ranks M3U accounts by name, to prefer one provider's copy of a channel. |
| `orderStreamsBy` | profile, group, member | Now also accepts a list of criteria (`priority`, `quality`, `m3u`) applied in order, e.g. `[priority, quality, m3u]`. The old single value `quality` still works. |
| `epgSchedule` | profile, group, member | Enables a channel only around EPG programmes that match (matchers on title, sub-title, description), from `before` ahead of their start until `after` past their end; disables it in the profile otherwise. |

New plugin setting: **Auto-apply every (minutes)** runs Apply on a timer, so `epgSchedule` follows the guide without an external scheduler.

New transformer: **`normalizeUnicode`** applies NFKC, turning decorated letters such as squared
`🄳🄰🅉🄽` or full-width `ＤＡＺＮ` into plain text before matching.

## Bug fixes

- `setMetadata` `tvgId` and `logoUrl` were ignored when building the plan; they now apply.
- Apply cleared the logo of every channel whose manifest and streams had none, wiping logos set from the EPG. Such channels now take their EPG icon, or keep their current logo.
- An empty manifest section (e.g. `members:` with nothing under it) gives a clear error instead of crashing with `'NoneType' object has no attribute 'get'`.
- `exactMatch` and `containsAny` matchers with pre-transformers no longer miss streams. The database pre-filter used to run on the raw name, before the transformers.
- Applying is much faster on large libraries: Jinja templates are no longer recompiled for every stream, and the full stream list is loaded once and shared. The slow apply caused uwsgi "Broken pipe" errors.
- JSON schema: nullable fields keep `null`, and a dangling reference was removed.
- The plugin ZIP no longer ships compiled extensions (`.so`) for markupsafe and pyyaml; both use their pure-Python code. The ZIP no longer depends on the build machine's Python version and CPU, and the loaded `.so` no longer blocks Dispatcharr from removing the old plugin folder on update (`Failed to back up existing plugin: ... Directory not empty`).

## Releases and updates

- A GitHub Actions workflow builds `waybill.zip` and publishes a release on every push to `main` that bumps the version.
- Each release also publishes a Dispatcharr plugin-repo `manifest.json` to the `releases` branch, so the plugin can be installed and updated from the Dispatcharr dashboard (see [Updating from the Dispatcharr dashboard](README.md#updating-from-the-dispatcharr-dashboard)).
