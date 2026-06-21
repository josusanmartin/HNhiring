# HN Who Is Hiring? comment chart

Creates an interactive chart of comment counts on monthly **Ask HN: Who is hiring?** posts.

The checked-in output is `who_is_hiring_chart.html`, with aggregate CSV/JSON data for
the monthly totals and category counts.

## Requirements

- Python 3.9+

## Run

Fetch from HN Algolia (requires internet) and regenerate the chart:

```bash
python3 hn_who_is_hiring_chart.py --refresh
```

Outputs:

- `who_is_hiring_chart.html`
- `who_is_hiring_comments.csv`
- `wih_category_counts.csv`
- `wih_category_counts_per_10k.csv`
- `hn_total_comments_by_month.json`
- `wih_category_counts.json`

Open `who_is_hiring_chart.html` in a browser to view the chart. To serve it locally:

```bash
python3 -m http.server 8000
```

Then open `http://127.0.0.1:8000/who_is_hiring_chart.html`.
