# Build 8109 port — derived anchors

Source: ChatGPT `26.901.51231` build `8109`,
`app.asar` sha256 `64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d`.

Every value below was derived by structural alignment against build 7746 and
verified with exact occurrence counts on the real 8109 bundles. Nothing here is
inferred from the model menu or from a running app.

## Bundles

| role | 7746 | 8109 |
| --- | --- | --- |
| initial | `app-initial-f1c3ba37268a.js` | `app-initial-cadb12d4a15e.js` |
| menu (primary) | `app-primary-b1300cb15eed.js` | `app-primary-6cd7b8b3f5e3.js` |
| profile | `profile-e6cc91a1b00f.js` | `profile-ee89ef5968ad.js` |
| plugins | `plugins-settings-*.js` | `plugins-settings-b1cc0e4499bb.js` |
| thread | `local-conversation-thread-*.js` | `local-conversation-thread-40db5af470f5.js` |

## Unchanged from 7746 (verified present, count 1)

- app-server request bridge, except `timeoutMs:vCn` -> `timeoutMs:TCn`
- usage query `return _lr(t,o),o`
- selected usage window `let y=v;if(g!=null){`
- three subscription-depletion alert strings
- plugin scope `action:F,children:w})`
- thread component `function uT(e){let t=(0,pT.c)(43),`
- thread summary children `children:[d,f,p,m,h,g,_,v,y,b,x,S,C,w,T,E,D]`
- thread identifier map (`$n`->`zd`, `sr`->`Gi`, `TE`->`nT`, `zE`->`mT`, `K`->`Q`)
- CUA service layout `("Codex Computer Use.app", 17)`
- CUA asar identifier replacements: 16
- CUA identity replacements: 49. The package holds 53 raw references, but
  `patch_computer_use_identity` deletes both `embedded.provisionprofile` files
  (4 references) before counting. Counting the raw total aborts the install.

## Re-derived for 8109

| site | 7746 | 8109 |
| --- | --- | --- |
| menu marker | `function ign(e){let t=(0,Fq.c)(223),` | `function Ymn(e){let t=(0,sY.c)(223),` |
| profile query | `async function Uzs(){let e=await AO.safeGet(` | `async function wBs(){let e=await _O.safeGet(` |
| reset query fn | `l2i` / `RK` / `HR` / `lb` / `d2i` / `u2i` / `gD` / `vb` | `v2i` / `VK` / `WR` / `Cb` / `b2i` / `y2i` / `lD` / `Mb` |
| reset mutation fn | `f2i` / `mb` / `mD` / `p2i` / `Y1i` / `xb` | `x2i` / `Ob` / `sD` / `S2i` / `i0i` / `Fb` |
| usage modal | `jG` | `tq` |
| profile avatar | `L.isPending` / `re(` / refetch `j` | `B.isPending` / `gt(` / refetch `M` |
| profile display name | `Re` / `J` | `Je` / `W` |
| profile username | `Ie` / `J` | `qe` / `W` |
| usage menu slot | `usageItems:Tt` | `usageItems:Dt` |
| usage header | `ge` / `AG` / `Gv` / `me` / `he` | `_e` / `eq` / `mw` / `he` / `ge` |
| menu open state | `triggerButton:kt` | `triggerButton:jt` |
| plugin timeout symbol | `vCn` | `TCn` |

## Component identifier map (injected account menu)

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

Derivation: the injected-component map came from a 33,859-character exact
structural alignment of the menu component; `lt` was confirmed separately at
three independent call sites. `j`->`M` was confirmed at 18 aligned slots.

## Verified

A full patch run against `/Applications/ChatGPT.app` (8109) completed with no
`--allow-untested-source` override: every anchor matched, the app and the
standalone Computer Use helper both built, and both passed
`codesign --verify --deep --strict`. The built `app.asar` contains the injected
account menu, thread subscription, plugin scoping, profile avatar stack and
depletion strings, with zero remaining `com.openai.sky.CUAService` references.

Signing resolved team `CZWR4XCLQ7` rather than the `TNBQQS8Z3A` name suffix,
which exercises the signing fix from upstream PR #25.

## Asar integrity (found by launching the build)

The first 8109 install built and signed cleanly and then died on launch:

    FATAL:asar_util.cc:143 Integrity check failed for asar archive

`ElectronAsarIntegrity` records the SHA-256 of the archive's **header block**,
not of the whole file. The patcher recorded the whole-file digest. That was
already wrong for 6396/6662/7746 and simply never checked, because those builds
ship with the embedded-asar-integrity fuse off. 8109 enables it, so the wrong
value became fatal. `asar_header_digest` now reproduces the digest that both the
official 7746 and 8109 bundles record.

## Runtime state

The 8109 build launches, loads all connected accounts through the control API,
and the composer offers **GPT-6 Astra**. Still untested: routing across accounts,
failover when one is exhausted, thread resume, and the Computer Use matrix.

`@electron/asar` 4.2.1 requires node >= 22.12; the patcher now fails fast with
an actionable message instead of aborting mid-extract.
