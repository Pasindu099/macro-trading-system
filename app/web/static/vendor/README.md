# Vendored frontend libraries

Served from `/static/vendor/`; no runtime CDN loads (Google Fonts excepted).

| File | Version | Source | Licence | SHA-256 |
| --- | --- | --- | --- | --- |
| `echarts-5.6.0.min.js` | 5.6.0 | https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js | Apache-2.0 | bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1 |
| `htmx-1.9.12.min.js` | 1.9.12 | https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js | 0BSD | 449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452 |

To upgrade: download the new pinned file, update the template `<script>` tag, the table above and `tests/unit/test_vendored_assets.py`.
