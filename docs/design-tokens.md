# wilkr — design tokens

Palette and type for the Jinja2/HTMX web viewer. Continues the purple/orange
identity from the app logo (`docs/` field-notes artifact) — the accent
orange here is the same family as the logo's gradient.

## Palette

| Token | Dark mode | Light mode |
|---|---|---|
| Background | `#0d0b18` | `#f5f4f8` |
| Card surface | `#19152b` | `#ffffff` |
| Card border | `#292343` | `#e1e0e8` |
| Primary text | `#e2e4ed` | `#12101a` |
| Secondary text | `#80839e` | `#616377` |
| Accent orange | `#fca338` | `#d97306` |
| Tag background | `#1c1b30` | `#eae9f2` |
| Tag border | `#39385d` | `#c3c1d5` |

## Type

Monospace, for a data/telemetry feel fitting activity stats, timestamps, and
tabular numbers (pace, distance, HR):

```
'JetBrains Mono', 'Fira Code', 'SF Mono', 'Roboto Mono', monospace
```

## Usage notes (for whoever implements the CSS)

- Express as CSS custom properties (`--bg`, `--surface`, `--border`,
  `--text-primary`, `--text-secondary`, `--accent`, `--tag-bg`,
  `--tag-border`), redefined under both `prefers-color-scheme: dark` and an
  explicit `data-theme` override, so a manual toggle can win over OS
  preference.
- `font-variant-numeric: tabular-nums` wherever digits line up in columns
  (activity list distance/time/pace columns, stat tiles).
