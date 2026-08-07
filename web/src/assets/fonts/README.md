# Self-hosted fonts

Two families, and the choice between them is **semantic**, not stylistic
(see [DESIGN.md](../../../../DESIGN.md) §4.1):

| Family | Means | Source |
|---|---|---|
| **Satoshi** | a model wrote this | vendored here, `satoshi-{400,500,700}.woff2` |
| **IBM Plex Mono** | the database produced this | `@fontsource/ibm-plex-mono` (npm) |

## Why these files are committed

No font CDN. The app ships as **one Docker image** behind ECS Express Mode
(ADR-13.3 / ADR-13.7); a third-party font request would be an availability
dependency we do not accept and a render-blocking round trip we do not need.

Satoshi is not published to npm, so its WOFF2 files are vendored directly.
Total weight: **~76 KB** for three weights.

## Licensing

Satoshi is published by the Indian Type Foundry via [Fontshare](https://www.fontshare.com/fonts/satoshi),
free for personal and commercial use, and self-hosting is the documented
delivery path. Files retrieved 2026-08-07 from `api.fontshare.com/v2/css`.

## Refreshing or adding a weight

```bash
curl -sL "https://api.fontshare.com/v2/css?f%5B%5D=satoshi@400,500,700" -o satoshi.css
# extract the .woff2 URLs (they are protocol-relative) and download each
```

Then add a matching `@font-face` block to [`fonts.css`](../../styles/fonts.css).

**Do not add a fourth weight without a DESIGN.md change.** The scale uses 400
(body), 500 (all display and headings) and 700 (inline emphasis only). Display
type at 700 reads as shouting; see design rule 4.
