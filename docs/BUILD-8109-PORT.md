# Porting notes: desktop build 8109

Source: ChatGPT `26.901.51231` build `8109`, `app.asar` sha256
`64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d`.

Most 7746 anchors survive unchanged in 8109. This file records the ones that
did not, so the next port has a starting point rather than a blank page.

## Method

Renderer anchors are exact minified strings, and the identifiers around them
are renamed every build. To re-derive one, locate the same construct in both
bundles, tokenize each into identifiers plus a punctuation skeleton, and walk
the two skeletons in step. Where the skeletons stay identical the identifiers
correspond positionally. Minified names are scope-local, so a global mapping is
wrong; align within one function and confirm each result with an exact
occurrence count on the real bundle before trusting it.

## Bundles

| role | 7746 | 8109 |
| --- | --- | --- |
| initial | `app-initial-f1c3ba37268a.js` | `app-initial-cadb12d4a15e.js` |
| menu (primary) | `app-primary-b1300cb15eed.js` | `app-primary-6cd7b8b3f5e3.js` |
| profile | `profile-e6cc91a1b00f.js` | `profile-ee89ef5968ad.js` |
| plugins | `plugins-settings-*.js` | `plugins-settings-b1cc0e4499bb.js` |
| thread | `local-conversation-thread-*.js` | `local-conversation-thread-40db5af470f5.js` |

## Unchanged from 7746

The app-server request bridge (except its timeout symbol), the usage query, the
usage-window selection, the three depletion strings, plugin scoping, the thread
component and its summary list and identifier map, and the Computer Use service
layout and asar identity count.

Computer Use identity replacements stay at 49. The package holds 53 raw
references, but `patch_computer_use_identity` deletes both
`embedded.provisionprofile` files first. Counting the raw total aborts the
install.

## Re-derived for 8109

| site | 7746 | 8109 |
| --- | --- | --- |
| menu marker | `function ign(e){let t=(0,Fq.c)(223),` | `function Ymn(e){let t=(0,sY.c)(223),` |
| profile query | `Uzs` / `AO` | `wBs` / `_O` |
| reset query | `l2i` `RK` `HR` `lb` `d2i` `u2i` `gD` `vb` | `v2i` `VK` `WR` `Cb` `b2i` `y2i` `lD` `Mb` |
| reset mutation | `f2i` `mb` `mD` `p2i` `Y1i` `xb` | `x2i` `Ob` `sD` `S2i` `i0i` `Fb` |
| usage modal | `jG` | `tq` |
| usage menu slot | `usageItems:Tt` | `usageItems:Dt` |
| usage header | `ge` `AG` `Gv` `me` `he` | `_e` `eq` `mw` `he` `ge` |
| menu open state | `triggerButton:kt` | `triggerButton:jt` |
| plugin timeout symbol | `vCn` | `TCn` |
| profile avatar | `L.isPending` / `re(` / refetch `j` | `B.isPending` / `gt(` / refetch `M` |
| profile display name | `Re` / `J` | `Je` / `W` |
| profile username | `Ie` / `J` | `qe` / `W` |

## Injected account-menu identifier map

| canonical | 7746 | 8109 |
| --- | --- | --- |
| `e7` (jsx runtime) | `Iq` | `cY` |
| `kXc` (React) | `lgn` | `ehn` |
| `Lo` | `DO` | `zx` |
| `Q` | `_S` | `qv` |
| `BW` (open modal) | `ru` | `QC` |
| `QLs` (usage modal) | `jG` | `tq` |
| `_H` (menu row) | `fa` | `xl` |
| `S2` (icon) | `eD` | `IE` |
| `CH` (menu namespace) | `Oo` | `Sy` |
| `jLa` (avatar url) | `jB` | `gV` |
| `lt` (useQueryClient) | `Su` | `Cx` |

## Asar integrity

`ElectronAsarIntegrity` records the SHA-256 of the archive's **header block**,
not of the whole file. The patcher recorded the whole-file digest, which was
wrong for every build but never checked: 6396, 6662 and 7746 ship with the
embedded-asar-integrity fuse disabled. Build 8109 enables it, so a correctly
signed app died on launch with `Integrity check failed for asar archive`.
`asar_header_digest` records what Electron actually validates.

Anchors matching and the app signing are not evidence the build runs. Launch it.

## Status

Build 8109 patches, signs, launches and loads connected accounts. Routing
across accounts, failover, thread resume and Computer Use are untested there.
