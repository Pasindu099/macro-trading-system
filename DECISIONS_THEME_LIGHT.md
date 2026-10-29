# DECISIONS - LIGHT THEME + SEMANTIC COLOUR TOKENS (Step 18 Part E)

Deferred until Parts A-D are complete. References: PositioningLight.dc.html and Positioning.dc.html.

- up/on/long/positive: --up; down/against/short/negative: --down.
- Caution: --caution; neutral data lines: --ink/--ink-2; interface only: --accent.
- No violet or blue data series or decorative data colours. Positions use bars/chips/filled zones; prices use plain slate lines. Up/down colours always have direction arrows.

| Token | Light | Dark |
|---|---|---|
| --bg | #F4F6F8 | #0b0e13 |
| --bg-wash | #E6EDFA to #F4F6F8 | none |
| --surface | #FFFFFF | #12161d |
| --surface-2 | #F4F6F8 | #0e1218 |
| --surface-3 | #EAF0F5 | #1b2230 |
| --line | #D8E0E8 | #222833 |
| --grid | #A9B6C3 | #4a5568 |
| --ink | #172B3A | #e6e9ef |
| --ink-2 | #3B4F60 | #c9d1dd |
| --ink-3 | #526477 | #9aa3b2 |
| --accent | #2457C5 | #f2a33a |
| --accent-ink | #FFFFFF | #14100a |
| --accent-tint | #EEF3FC | #2a2214 |
| --up | #087F6D | #4CC38A |
| --up-tint / --up-tint-2 | #E3F3F0 / #F0F8F6 | #10302a / #0f241f |
| --down | #C43D4B | #F2706A |
| --down-tint / --down-tint-2 | #FBEAEC / #FDF3F4 | #3a1d1d / #2a1616 |
| --caution | #946200 | #d6c4fb |
| --caution-tint / --caution-line | #FBF1DC / #E8D3A6 | #261d3a / #4a3a6a |
| --header-bg | linear-gradient(90deg, #172B3A, #1F4578) | existing |
| --header-strip | 4px gradient #2457C5 to #087F6D to #946200 to #7356B5 | none |

- The header strip is chrome decoration, the only permitted violet decoration.
- Light header: navy/white wordmark, white active tab with accent text, outlined inactive tabs.
- Cards: surface, 1px line, 3px accent top edge, shadow 0 1px 2px rgba(23,43,58,0.06); squeeze uses caution top edge and caution-line border.
- Number badges: 28px rounded squares, accent/white; squeeze uses caution.
- Takeaways: accent-tint and 4px accent left edge. Status cards use up-tint-2/down-tint-2/caution-tint; disagreement has caution-line border.
- Extreme chips: solid up/down/caution background with white text.
- Hawkish/beat temporarily map to up; dovish/miss to down, with arrows and separate --hawk/--dove/--beat/--miss aliases.
- Top-bar Light/Dark switch, default Light; cookie remembers choice, prefers-color-scheme only without a cookie. ECharts theme objects read tokens and re-render on toggle.
- Replace raw colours in templates/CSS/chart configs with tokens; enforce no raw hex outside tokens.css except allow-listed logos/images.
- Body text contrast >=7:1, labels/coloured text >=4.5:1 in both themes; report failures. Tests cover complete tokens, raw hex, cookie persistence, re-themed charts and both Positioning themes.
