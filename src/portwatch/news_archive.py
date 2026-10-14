"""Official LA release metadata; publication dates never become invented UTC instants."""

import json
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from portwatch.context import ContextError, normalize_news


class ReleaseTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.archive_depth = None
        self.tables = 0
        self.rows = []
        self.row = None
        self.cell = None
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            self.depth += 1
            labels = (
                " ".join((attributes.get(name) or "").casefold().split())
                for name in ("summary", "aria-label")
            )
            if any("news releases" in label.replace("-", " ") for label in labels):
                self.archive_depth = self.depth
                self.tables += 1
        if self.archive_depth is None:
            return
        if tag == "tr":
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = dict(text=[], links=[])
        elif tag == "a" and self.cell is not None:
            self.anchor = dict(href=attributes.get("href", ""), text=[])

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)
        if self.anchor is not None:
            self.anchor["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None and self.cell is not None:
            self.cell["links"].append(self.anchor)
            self.anchor = None
        elif tag == "td" and self.cell is not None and self.row is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            if self.archive_depth == self.depth:
                self.archive_depth = None
            self.depth -= 1


def normalize_official_archive(payload, request):
    if len(payload) > 2 * 1024 * 1024:
        raise ContextError("oversized_news_archive")
    parser = ReleaseTableParser()
    try:
        parser.feed(payload.decode("utf-8"))
        parser.close()
    except (ValueError, UnicodeError) as error:
        raise ContextError("invalid_news_archive") from error
    if not parser.tables or len(parser.rows) > 5000:
        raise ContextError("news_archive_structure_unavailable")
    articles, dates, dated, outside, unknown = [], {}, 0, 0, 0
    pacific = ZoneInfo("America/Los_Angeles")
    for row in parser.rows:
        if not row:
            continue
        try:
            publication_date = datetime.strptime(
                "".join(row[-1]["text"]).strip(), "%m/%d/%Y"
            ).date()
        except ValueError:
            unknown += 1
            continue
        dated += 1
        day_start = datetime.combine(publication_date, datetime.min.time(), pacific)
        day_end = datetime.combine(
            publication_date + timedelta(days=1), datetime.min.time(), pacific
        )
        if (
            day_end.astimezone(UTC) <= request.start_utc
            or day_start.astimezone(UTC) >= request.end_utc
        ):
            outside += 1
            continue
        selected = None
        for cell in row:
            for link in cell["links"]:
                url = urljoin(request.endpoint, link["href"])
                title = " ".join("".join(link["text"]).split())
                if title and urlsplit(url).hostname in {
                    "portoflosangeles.org",
                    "www.portoflosangeles.org",
                }:
                    selected = dict(url=url, title=title, language="English")
                    break
            if selected:
                break
        if not selected:
            raise ContextError("news_archive_article_metadata_missing")
        articles.append(selected)
        dates.setdefault(selected["url"], publication_date.isoformat())
    if not dated:
        raise ContextError("news_archive_publication_dates_unavailable")
    result = normalize_news(json.dumps(dict(articles=articles)).encode(), request)
    for row in result.rows:
        row["quality_flags"].append("publication_date_only_window_overlap")
    result.quality.update(
        label="official Port of Los Angeles releases",
        exhaustive=False,
        archive_dated_rows=dated,
        provider_article_count=dated + unknown,
        parsed_item_count=dated + unknown,
        eligible_item_count=len(articles),
        excluded_by_date=outside,
        unknown_date_count=unknown,
        unknown_count=unknown,
        outside_window=outside,
        unknown_publication_rows=unknown,
        date_coverage_complete=unknown == 0,
        response_bytes=len(payload),
        format="official HTML release archive",
        coverage="official authority releases only; not a broad disruption-news search",
        window_semantics=(
            "publication calendar day overlaps request; exact publication time unknown"
        ),
    )
    result.metadata.update(
        provider="Port of Los Angeles",
        format="official HTML release archive",
        response_bytes=len(payload),
        publication_dates={row["original_url"]: dates[row["original_url"]] for row in result.rows},
        publication_time_precision="calendar date only; no UTC instant inferred",
        publication_date_timezone_assumption="America/Los_Angeles",
        first_seen_field="unknown",
        window_semantics=(
            "publication calendar day overlaps request; exact publication time unknown; "
            "unknown dates excluded"
        ),
    )
    return result
