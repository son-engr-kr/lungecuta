# DaVinci Resolve: facts the design relies on

Sources: the DaVinci Resolve 21 Reference Manual installed with Resolve 21.0.4
(`/Applications/DaVinci Resolve/DaVinci Resolve Manual.pdf`, 4444 pages, created
2026-07-14; page numbers are PDF pages), the scripting README installed with it
(`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt`,
"Last Updated: 24 Jul 2026"), and Blackmagic's release notes. Checked 2026-10-09 to 10-11.

## Timeline import and speed (Phase 0)

- **Speed effects on import** (manual, "About Speed Effects", p. 485): "EDL: DaVinci Resolve
  only supports the import of linear speed effects when importing EDLs." "XML: DaVinci
  Resolve supports the import of both linear and variable speed effects when importing XML
  project files from Premiere Pro, Final Cut Pro 7, and Final Cut Pro X." Variable speed is
  converted "by creating one speed keyframe per frame for each affected clip".
- **Timeline import formats** (scripting README, `ImportTimelineFromFile`): AAF, EDL, XML,
  FCPXML, DRT, ADL, OTIO. Through the GUI: File > Import > Timeline.
- Not yet verified: whether FCPXML markers import, and whether an imported variable ramp
  can be edited as a curve rather than as per-frame keyframes. Both are Phase 0 tests.

## Scripting and the free edition

- Blackmagic, "About DaVinci Resolve 21" release notes
  (https://www.blackmagicdesign.com/support/content/readme/baf7c071c0524fbf8ccc961925c9f443),
  21.1: "Advanced scripting now requires DaVinci Resolve Studio." "Built in Python support
  for console scripting." "Native MCP server for interacting with AI assistants." "Query
  and set timeline item speed change."
- Secondary coverage of 21.1: Puget Systems quotes the notes as "We have moved the ability
  to script in Python to the Studio version"
  (https://www.pugetsystems.com/blog/2026/09/10/how-davinci-resolve-free-v21-1-scripting-changes-affect-puget-bench/);
  Digital Production describes the MCP server as Studio-only
  (https://digitalproduction.com/2026/09/08/resolve-21-1-adds-mcp-and-finally-gets-presets/).
- In the 21.0 scripting README, `TimelineItem:SetProperty` has no speed key (only
  `RetimeProcess`, the interpolation method), and no function creates keyframes for
  Inspector properties.

## Studio-only features that bear on the design (manual, marked "Studio Version Only")

| feature | page | why it matters |
| --- | --- | --- |
| Beat Detector | 801 | the free edition cannot find beats, so lungecuta's beat map is needed even in Phase 0 |
| Motion Effects palette (temporal NR, motion blur) | 3583 | the free edition cannot add optical-flow motion blur, which the shooting guidance relies on |
| Motion Blur (ResolveFX) | 3751 | same |

Smart Reframe and Magic Mask are also marked Studio-only in the manual. Speed Warp
(neural retiming) is described as using the DaVinci Neural Engine (p. 149); the manual
passages found so far do not say it is Studio-only, so that is unverified.

## Other

- **Generate LUT** (Color page, p. 3552): a 17, 33 or 65 point `.cube` can be generated from
  a clip's grade, combining "Primaries palette adjustments, Custom Curves palette
  adjustments, and compatible ResolveFX plugins that include Color Space Transform, ACES
  Transform, and Gamut Mapping". This is how the user can export Resolve's own CST with
  DaVinci tone mapping as a LUT (`docs/design/04-color.md`, Views).
- **Gyro stabilization** (p. 3573): "Camera Gyro: If your footage is from a Blackmagic Cinema
  Camera that has a gyroscope". Sony gyro data is not used by Resolve; Gyroflow stays.
- Installed version on this machine: 21.0.4, free.
