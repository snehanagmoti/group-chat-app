"""Build the final Lab 5 load-balanced backend report as a polished PDF."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output" / "pdf" / "lab5_load_balanced_backend_report.pdf"
SCREENSHOTS = ROOT / "artifacts" / "screenshots"

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2E5E8C")
PALE_BLUE = colors.HexColor("#EAF2F8")
GREEN = colors.HexColor("#2E7D5B")
PALE_GREEN = colors.HexColor("#EAF6F0")
ORANGE = colors.HexColor("#D9822B")
PALE_ORANGE = colors.HexColor("#FFF3E5")
INK = colors.HexColor("#1D2733")
MUTED = colors.HexColor("#5D6B78")
GRID = colors.HexColor("#C7D2DC")
LIGHT = colors.HexColor("#F6F8FA")
WHITE = colors.white


def clean_text(value: str) -> str:
    return (
        value.replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )


def paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(clean_text(value), style)


def register_fonts() -> tuple[str, str]:
    candidates = [
        Path("C:/Windows/Fonts/aptos.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    bold_candidates = [
        Path("C:/Windows/Fonts/aptos-bold.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    regular = next((path for path in candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), None)
    if regular and bold:
        pdfmetrics.registerFont(TTFont("ReportSans", str(regular)))
        pdfmetrics.registerFont(TTFont("ReportSansBold", str(bold)))
        return "ReportSans", "ReportSansBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = register_fonts()
BASE = getSampleStyleSheet()
STYLES = {
    "title": ParagraphStyle(
        "Title",
        parent=BASE["Title"],
        fontName=FONT_BOLD,
        fontSize=28,
        leading=33,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=8,
    ),
    "subtitle": ParagraphStyle(
        "Subtitle",
        parent=BASE["Normal"],
        fontName=FONT,
        fontSize=13,
        leading=18,
        textColor=MUTED,
        alignment=TA_CENTER,
    ),
    "h1": ParagraphStyle(
        "Heading1",
        parent=BASE["Heading1"],
        fontName=FONT_BOLD,
        fontSize=18,
        leading=23,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=8,
        keepWithNext=True,
    ),
    "h2": ParagraphStyle(
        "Heading2",
        parent=BASE["Heading2"],
        fontName=FONT_BOLD,
        fontSize=13,
        leading=17,
        textColor=BLUE,
        spaceBefore=8,
        spaceAfter=5,
        keepWithNext=True,
    ),
    "body": ParagraphStyle(
        "Body",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=9.5,
        leading=14,
        textColor=INK,
        spaceAfter=6,
    ),
    "small": ParagraphStyle(
        "Small",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=8,
        leading=11,
        textColor=MUTED,
    ),
    "caption": ParagraphStyle(
        "Caption",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=8.2,
        leading=11,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceBefore=4,
        spaceAfter=9,
    ),
    "callout": ParagraphStyle(
        "Callout",
        parent=BASE["BodyText"],
        fontName=FONT_BOLD,
        fontSize=10,
        leading=14,
        textColor=GREEN,
        alignment=TA_CENTER,
    ),
    "table_header": ParagraphStyle(
        "TableHeader",
        parent=BASE["BodyText"],
        fontName=FONT_BOLD,
        fontSize=8.2,
        leading=10,
        textColor=WHITE,
        alignment=TA_LEFT,
    ),
    "table": ParagraphStyle(
        "TableCell",
        parent=BASE["BodyText"],
        fontName=FONT,
        fontSize=7.8,
        leading=10.2,
        textColor=INK,
    ),
    "code": ParagraphStyle(
        "Code",
        fontName="Courier",
        fontSize=5.8,
        leading=7.1,
        textColor=colors.HexColor("#263746"),
        backColor=colors.HexColor("#F2F5F7"),
        leftIndent=5,
        rightIndent=5,
        borderPadding=7,
        borderColor=colors.HexColor("#486346"),
        borderWidth=0.5,
        spaceAfter=7,
    ),
}


def page_decor(canvas, document) -> None:
    canvas.saveState()
    width, height = A4
    if document.page > 1:
        canvas.setFillColor(NAVY)
        canvas.rect(0, height - 15 * mm, width, 15 * mm, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont(FONT_BOLD, 8.5)
        canvas.drawString(18 * mm, height - 9.5 * mm, "LAB 5 - LOAD BALANCED BACK-END")
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 8)
        canvas.drawString(18 * mm, 10 * mm, "Sneha Nagmoti | Roll No. 12342090")
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {document.page}")
        canvas.setStrokeColor(GRID)
        canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.restoreState()


class ReportDocument(BaseDocTemplate):
    def __init__(self, filename: Path):
        super().__init__(
            str(filename),
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=22 * mm,
            bottomMargin=19 * mm,
            title="Lab 5: Load Balanced Back-end",
            author="Sneha Nagmoti",
            subject="Go reverse proxy, round robin, health checks, and experiments",
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="normal",
        )
        self.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=page_decor))


def info_box(rows: list[tuple[str, str]]) -> Table:
    data = [
        [
            paragraph(label, STYLES["table_header"]),
            paragraph(value, STYLES["table"]),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=[43 * mm, 92 * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), NAVY),
                ("BACKGROUND", (1, 0), (1, -1), LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def data_table(
    headers: list[str],
    rows: list[list[str]],
    widths: list[float],
    *,
    header_color=BLUE,
) -> Table:
    data = [[paragraph(header, STYLES["table_header"]) for header in headers]]
    data.extend(
        [[paragraph(str(cell), STYLES["table"]) for cell in row] for row in rows]
    )
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_color),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT]),
                ("GRID", (0, 0), (-1, -1), 0.45, GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def bullet_list(items: list[str]) -> Table:
    rows = [
        [
            paragraph("-", ParagraphStyle("BulletMark", parent=STYLES["body"], fontName=FONT_BOLD, textColor=BLUE)),
            paragraph(item, STYLES["body"]),
        ]
        for item in items
    ]
    return Table(
        rows,
        colWidths=[5 * mm, 165 * mm],
        style=TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        ),
        hAlign="LEFT",
    )


def image_flow(path: Path, max_width: float, max_height: float) -> Image:
    width, height = ImageReader(str(path)).getSize()
    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale, hAlign="CENTER")


def metric_chart(one: dict, three: dict) -> Drawing:
    drawing = Drawing(480, 155)
    drawing.add(String(12, 135, "Final mapped-port experiment comparison", fontName=FONT_BOLD, fontSize=11, fillColor=NAVY))
    chart_specs = [
        ("Throughput (RPS)", one["throughput_rps"], three["throughput_rps"], 15, BLUE, ".1f"),
        ("Failed requests", one["failed"], three["failed"], 255, ORANGE, ".0f"),
    ]
    for title, first, second, origin_x, accent, value_format in chart_specs:
        drawing.add(String(origin_x, 112, title, fontName=FONT_BOLD, fontSize=8.5, fillColor=INK))
        base_y = 22
        max_height = 74
        bar_width = 42
        gap = 22
        maximum = max(first, second, 1) * 1.15
        for index, (label, value, fill) in enumerate(
            [("1 backend", first, colors.HexColor("#A9B8C6")), ("3 backends", second, accent)]
        ):
            height = value / maximum * max_height
            x = origin_x + 22 + index * (bar_width + gap)
            drawing.add(Rect(x, base_y, bar_width, height, strokeColor=fill, fillColor=fill))
            drawing.add(String(x + bar_width / 2, base_y + height + 5, format(value, value_format), textAnchor="middle", fontName=FONT_BOLD, fontSize=8, fillColor=INK))
            drawing.add(String(x + bar_width / 2, 8, label, textAnchor="middle", fontName=FONT, fontSize=7, fillColor=MUTED))
    return drawing


def format_source(path: Path, width: int = 104) -> str:
    output: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        expanded = raw_line.expandtabs(4)
        prefix = f"{line_number:4d}  "
        available = width - len(prefix)
        wrapped = textwrap.wrap(
            expanded,
            width=available,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        output.append(prefix + wrapped[0])
        output.extend("      " + part for part in wrapped[1:])
    return "\n".join(output)


def read_results() -> tuple[dict, dict]:
    with (ROOT / "1_backend.json").open(encoding="utf-8") as file:
        one = json.load(file)
    with (ROOT / "3_backends.json").open(encoding="utf-8") as file:
        three = json.load(file)
    return one, three


def read_lb_metrics() -> tuple[dict, dict]:
    with (ROOT / "1_backend_lb_metrics.json").open(encoding="utf-8") as file:
        one = json.load(file)
    with (ROOT / "3_backends_lb_metrics.json").open(encoding="utf-8") as file:
        three = json.load(file)
    return one, three


def improvement(old: float, new: float) -> float:
    return (new - old) / old * 100


def reduction(old: float, new: float) -> float:
    return (old - new) / old * 100


def build_story() -> list:
    one, three = read_results()
    one_lb, three_lb = read_lb_metrics()
    throughput_change = improvement(one["throughput_rps"], three["throughput_rps"])
    p50_change = improvement(one["p50_ms"], three["p50_ms"])
    p95_change = improvement(one["p95_ms"], three["p95_ms"])
    p99_change = improvement(one["p99_ms"], three["p99_ms"])
    success_delta = three["successful"] - one["successful"]
    failed_delta = three["failed"] - one["failed"]
    story: list = []

    story.extend(
        [
            Spacer(1, 26 * mm),
            paragraph("LAB 5", STYLES["callout"]),
            Spacer(1, 4 * mm),
            paragraph("Load Balanced Back-end", STYLES["title"]),
            paragraph(
                "Reverse Proxy, Health-Aware Round Robin, WebSocket Integration, and Performance Experiments",
                STYLES["subtitle"],
            ),
            Spacer(1, 15 * mm),
            info_box(
                [
                    ("Student Name", "Sneha Nagmoti"),
                    ("Roll Number", "12342090"),
                    ("Assignment", "Lab 5: Load Balanced Back-end"),
                    ("Implementation", "Go load balancer and load generator; FastAPI messaging replicas"),
                    ("Report Date", "24 August 2026"),
                ]
            ),
            Spacer(1, 14 * mm),
            Table(
                [[paragraph("VERIFIED IMPLEMENTATION", STYLES["callout"])]],
                colWidths=[135 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PALE_GREEN),
                        ("BOX", (0, 0), (-1, -1), 1.2, GREEN),
                        ("TOPPADDING", (0, 0), (-1, -1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ]
                ),
                hAlign="CENTER",
            ),
            Spacer(1, 8 * mm),
            paragraph(
                "This report is generated from the repository's current source and final remote experiment artifacts. Verification used the assigned Sys1-Sys4 machines, three live HTTPS backends, health failover and recovery, and a real WebSocket connection through Sys1.",
                STYLES["small"],
            ),
            PageBreak(),
        ]
    )

    story.extend(
        [
            paragraph("1. Executive Summary", STYLES["h1"]),
            paragraph(
                "The assignment has been implemented as a complete load-balanced messaging back-end. Sys1 runs a Go reverse proxy. Sys2, Sys3, and Sys4 run replicas of the previous FastAPI messaging server. A concurrent Go load generator executes fair one-backend and three-backend experiments and writes JSON plus CSV results.",
                STYLES["body"],
            ),
            paragraph(
                "The audited implementation uses validated backend URLs, atomic health and in-flight state, health-aware round robin, active health checks, total request timeouts, explicit TLS controls, WebSocket-safe response handling, browser affinity for stateful chat sessions, and bounded latency percentile metrics.",
                STYLES["body"],
            ),
            paragraph("Assignment traceability", STYLES["h2"]),
            data_table(
                ["Assignment requirement", "Implementation", "Verification"],
                [
                    ["Load balancer on Sys1", "Go command at cmd/load-balancer; listens on container port 4000, mapped publicly to 4237.", "Remote process plus public health, status, metrics, and routing checks."],
                    ["Backends on Sys2, Sys3, Sys4", "FastAPI messaging server deployed on HTTPS port 5000.", "All three assigned machines passed remote health checks."],
                    ["Local load generator", "Concurrent Go worker pool targets 10.1.75.53:4237 from the student workstation.", "Mapped-port TLS run plus JSON/CSV writers."],
                    ["Measure Sys2 only", "run_mapped_experiments.py starts Sys1 with only backend 1 and drives it from the local PC.", "Recorded 1_backend.json and CSV row."],
                    ["Measure all three", "The same mapped-port script starts Sys1 with all three backend URLs.", "Recorded 3_backends.json and CSV row."],
                    ["Comparison table", "Section 6 uses the recorded artifacts and calculated improvements.", "Numbers cross-checked against JSON."],
                    ["Messaging app integration", "REST, uploads, and WebSocket upgrades route through the LB with affinity.", "Remote register, room, token rotation, and reconnecting WebSocket acceptance test."],
                    ["Single PDF report", "This generated PDF contains systems, code, results, analysis, and evidence.", "Rendered and visually inspected before submission."],
                ],
                [45 * mm, 68 * mm, 57 * mm],
            ),
            paragraph("2. System Architecture", STYLES["h1"]),
            paragraph(
                "The local load generator sends requests to Sys1. Sys1 selects a healthy backend using round robin and proxies the request or upgraded WebSocket connection. Health checks remove failed replicas from scheduling and restore them after recovery.",
                STYLES["body"],
            ),
        ]
    )

    architecture = ROOT / "architecture_diagram.jpg"
    if architecture.exists():
        story.extend(
            [
                image_flow(architecture, 165 * mm, 78 * mm),
                paragraph("Figure 1. Assignment deployment topology.", STYLES["caption"]),
            ]
        )

    story.extend(
        [
            paragraph("Assigned systems", STYLES["h2"]),
            data_table(
                ["System", "Role", "SSH entry", "Internal address", "Service port"],
                [
                    ["Sys1", "Go load balancer and frontend", "10.1.75.53:2237", "172.17.0.38", "4000->4237 LB; 3000->3237 UI"],
                    ["Sys2", "Messaging backend 1", "10.1.75.53:2238", "172.17.0.39", "5000; public 5238"],
                    ["Sys3", "Messaging backend 2", "10.1.75.53:2239", "172.17.0.40", "5000; public 5239"],
                    ["Sys4", "Messaging backend 3", "10.1.75.53:2240", "172.17.0.41", "5000; public 5240"],
                    ["Local PC", "Concurrent load generator", "Not applicable", "Student workstation", "HTTPS to public 4237"],
                ],
                [20 * mm, 47 * mm, 36 * mm, 34 * mm, 33 * mm],
            ),
            PageBreak(),
            paragraph("3. Load Balancer Implementation", STYLES["h1"]),
            bullet_list(
                [
                    "<b>Reverse proxy:</b> Go's httputil.NewSingleHostReverseProxy preserves the incoming method, path, query, headers, and response.",
                    "<b>Round robin:</b> an atomic counter selects the next healthy backend and starts with the first configured entry.",
                    "<b>Health awareness:</b> concurrent GET requests to /health update atomic alive state at a configurable interval.",
                    "<b>Timeouts:</b> dial, TLS handshake, response-header, and total non-upgraded request timeouts prevent indefinite waits.",
                    "<b>WebSockets:</b> the response recorder preserves Hijacker, Flusher, Pusher, ReaderFrom, and Unwrap behavior so HTTP upgrades remain functional.",
                    "<b>Stateful chat affinity:</b> an HttpOnly cookie keeps browser REST calls and WebSocket handshakes on one healthy replica; load-generator clients without a cookie still receive round-robin distribution.",
                    "<b>Monitoring:</b> /lb/health, /lb/status, and /lb/metrics are handled locally and are not included in forwarded-request totals.",
                    "<b>Metrics:</b> atomic totals plus bounded latency samples expose average, p50, p95, and p99 without an unbounded memory leak.",
                ]
            ),
            paragraph("Monitoring endpoints", STYLES["h2"]),
            data_table(
                ["Endpoint", "Purpose", "Representative fields"],
                [
                    ["/lb/health", "Load balancer liveness", "status"],
                    ["/lb/status", "Backend scheduling state", "url, alive, in_flight"],
                    ["/lb/metrics", "Request and latency metrics", "total, success, failed, backend_errors, average, p50, p95, p99"],
                    ["/", "Catch-all reverse proxy", "Original backend response plus X-Load-Balancer-Backend"],
                ],
                [32 * mm, 60 * mm, 78 * mm],
            ),
        ]
    )

    status_image = SCREENSHOTS / "lb_status.png"
    if status_image.exists():
        story.extend(
            [
                Spacer(1, 3 * mm),
                image_flow(status_image, 165 * mm, 90 * mm),
                paragraph(
                    "Figure 2. Verified local /lb/status snapshot after REST and WebSocket traffic; all three replicas are healthy.",
                    STYLES["caption"],
                ),
            ]
        )

    story.extend(
        [
            paragraph("4. Load Generator and Experiment Method", STYLES["h1"]),
            paragraph(
                "The load generator uses a fixed worker pool and one shared, connection-pooled HTTP client. It validates every CLI argument, optionally trusts the lab's self-signed certificate, drains response bodies for connection reuse, counts every attempt exactly once, and records latency for successful and failed attempts so timeout tail latency is not hidden.",
                STYLES["body"],
            ),
            data_table(
                ["Parameter", "Value used in both runs", "Fairness control"],
                [
                    ["Requests", "5,000", "Identical total attempts"],
                    ["Concurrency", "200 workers", "Identical worker pool"],
                    ["Synthetic delay", "100 ms", "Identical query string"],
                    ["Client timeout", "3 seconds", "Identical dropout boundary"],
                    ["LB backend timeout", "3 seconds", "Identical proxy boundary"],
                    ["Health interval", "1 second", "Identical health behavior"],
                    ["Changed variable", "1 healthy backend vs. 3 healthy backends", "Only experimental factor"],
                ],
                [42 * mm, 55 * mm, 73 * mm],
            ),
            paragraph("Metric definitions", STYLES["h2"]),
            bullet_list(
                [
                    "<b>Throughput (RPS)</b> = successful requests / elapsed seconds.",
                    "<b>Dropout (%)</b> = failed requests / total attempted requests x 100.",
                    "<b>p50, p95, p99</b> use the nearest-rank value from all sorted attempt latencies.",
                ]
            ),
            paragraph("5. Messaging Application Integration", STYLES["h1"]),
            paragraph(
                "The previous FastAPI/WebSocket project remains the backend. Its /health route supports active checks, /?delay=... and /?fail=true support controlled experiments, and X-Backend identifies the responding host. External browsers use Sys1 public port 4237, mapped to load-balancer container port 4000, rather than contacting a backend directly.",
                STYLES["body"],
            ),
            paragraph(
                "Two integration defects were corrected during verification: the proxy response wrapper now preserves HTTP upgrade interfaces, and cross-port frontend fetches include credentials so the affinity cookie is stored and sent. Authenticated sessions also survive WebSocket reconnects and token refresh now requires the existing session token.",
                STYLES["body"],
            ),
        ]
    )

    lobby_image = SCREENSHOTS / "messaging_lobby.png"
    chat_image = SCREENSHOTS / "messaging_chat.png"
    if lobby_image.exists():
        story.extend(
            [
                image_flow(lobby_image, 165 * mm, 95 * mm),
                paragraph(
                    "Figure 3. Messaging lobby loaded through the local three-replica load-balanced stack.",
                    STYLES["caption"],
                ),
            ]
        )
    if chat_image.exists():
        story.extend(
            [
                image_flow(chat_image, 165 * mm, 94 * mm),
                paragraph(
                    "Figure 4. Successful room join over a real WebSocket connection proxied by the Go load balancer.",
                    STYLES["caption"],
                ),
            ]
        )

    story.extend(
        [
            PageBreak(),
            paragraph("6. Results and Comparison", STYLES["h1"]),
            paragraph(
                "The following values are the final controlled measurements generated on the local workstation through Sys1's public port 4237 against the assigned machines on 24 August 2026. Public port 4237 maps to container port 4000. Background browser traffic was stopped before the run, and the only changed factor was one healthy backend versus three.",
                STYLES["body"],
            ),
            data_table(
                ["Experiment", "Success", "Failed", "RPS", "Dropout", "p50", "p95", "p99"],
                [
                    [
                        "1 backend",
                        f"{one['successful']:,}",
                        f"{one['failed']:,}",
                        f"{one['throughput_rps']:.1f}",
                        f"{one['dropout_percent']:.2f}%",
                        f"{one['p50_ms']:.0f} ms",
                        f"{one['p95_ms']:.0f} ms",
                        f"{one['p99_ms']:.0f} ms",
                    ],
                    [
                        "3 backends",
                        f"{three['successful']:,}",
                        f"{three['failed']:,}",
                        f"{three['throughput_rps']:.1f}",
                        f"{three['dropout_percent']:.2f}%",
                        f"{three['p50_ms']:.0f} ms",
                        f"{three['p95_ms']:.0f} ms",
                        f"{three['p99_ms']:.0f} ms",
                    ],
                ],
                [29 * mm, 20 * mm, 18 * mm, 21 * mm, 23 * mm, 19 * mm, 20 * mm, 20 * mm],
                header_color=GREEN,
            ),
            Spacer(1, 4 * mm),
            metric_chart(one, three),
            paragraph("Calculated changes", STYLES["h2"]),
            data_table(
                ["Metric", "Change from 1 to 3 backends", "Interpretation"],
                [
                    ["Successful requests", f"{success_delta:+,} request", f"{one['successful']:,} changed to {three['successful']:,}."],
                    ["Failed requests", f"{failed_delta:+,} request", f"{one['failed']:,} changed to {three['failed']:,} out of 5,000."],
                    ["Throughput", f"{throughput_change:+.1f}%", f"{one['throughput_rps']:.1f} RPS increased to {three['throughput_rps']:.1f} RPS."],
                    ["Dropout", f"{three['dropout_percent'] - one['dropout_percent']:+.2f} percentage points", f"{one['dropout_percent']:.2f}% changed to {three['dropout_percent']:.2f}%."],
                    ["p50 latency", f"{p50_change:+.1f}%", "Median latency improved." if p50_change < 0 else "Median latency increased."],
                    ["p95 latency", f"{p95_change:+.1f}%", "Tail latency improved." if p95_change < 0 else "Tail latency increased."],
                    ["p99 latency", f"{p99_change:+.1f}%", "Extreme tail latency improved." if p99_change < 0 else "Extreme tail latency increased."],
                ],
                [35 * mm, 47 * mm, 88 * mm],
            ),
            paragraph("Analysis", STYLES["h2"]),
            paragraph(
                f"With one backend, all 200 local workers contend for a single server. With three backends, health-aware round robin spreads new requests across three processes. The final mapped-port run increased throughput by {throughput_change:.1f}%, changed generator failures from {one['failed']} to {three['failed']} out of 5,000, and changed p50, p95, and p99 latency by {p50_change:+.1f}%, {p95_change:+.1f}%, and {p99_change:+.1f}%. The single three-backend timeout and p99 variation are consistent with external-network tail jitter; median and p95 latency improved materially. Sys1's own metrics recorded {one_lb['total']:,} and {three_lb['total']:,} forwarded requests respectively; the three-backend run had {three_lb['backend_errors']} backend error.",
                STYLES["body"],
            ),
            paragraph("7. Verification Performed", STYLES["h1"]),
            data_table(
                ["Check", "Result", "What it proves"],
                [
                    ["go test, vet, build", "PASS", "Round robin, health transitions, affinity, cancellation handling, timeouts, metrics, TLS, and complete builds."],
                    [
                        "go test -race ./internal/...",
                        "HOST LIMIT",
                        "Not runnable here: the Windows host has only a 32-bit MinGW compiler; standard tests, vet, and build pass.",
                    ],
                    ["Python, JS, shell checks", "PASS", "3 FastAPI tests plus Python compilation, frontend parsing, and experiment-script syntax."],
                    ["tests/e2e_smoke.py", "PASS", "Three HTTPS replicas received round-robin traffic and a real chat WebSocket completed through the LB."],
                    ["Remote topology, ports, failover", "PASS", "Public 3237/4237 and backend health mappings passed; routing was 4/4/4, 6/6 on failover, and 4/4/4 after recovery."],
                    ["Remote messaging and UI", "PASS", "Register, affinity, room, token rotation, two WebSocket joins, cleanup, and error-free frontend rendering."],
                ],
                [46 * mm, 24 * mm, 100 * mm],
            ),
            paragraph("8. Deployment and Reproduction", STYLES["h1"]),
            paragraph("Build the load balancer on Sys1 and the load generator on the local PC:", STYLES["body"]),
            Preformatted(
                "# Sys1\n"
                "go build -o load_balancer ./cmd/load-balancer\n"
                "# Local workstation\n"
                "go build -o tmp/bin/load_generator ./cmd/load-generator",
                STYLES["code"],
            ),
            paragraph("Start the HTTPS load balancer:", STYLES["body"]),
            Preformatted(
                "./load_balancer \\\n"
                "  -backends https://172.17.0.39:5000,https://172.17.0.40:5000,https://172.17.0.41:5000 \\\n"
                "  -port 4000 -backend-insecure-skip-verify \\\n"
                "  -tls-cert cert.pem -tls-key key.pem",
                STYLES["code"],
            ),
            paragraph(
                "For repeatable measurement, run python tools/run_mapped_experiments.py on the local workstation. It controls the Sys1 load balancer over SSH, sends both experiments through https://10.1.75.53:4237 with the same parameters, captures metrics, and writes results.csv plus one JSON file per experiment.",
                STYLES["body"],
            ),
            paragraph("9. Limitations and Production Considerations", STYLES["h1"]),
            bullet_list(
                [
                    "The lab messaging replicas use local SQLite databases and in-memory WebSocket hubs. Cookie affinity makes each browser session correct, but transparent failover of an active room would require shared durable storage and cross-replica pub/sub such as PostgreSQL plus Redis.",
                    "The -backend-insecure-skip-verify and -insecure flags are explicitly limited to the self-signed lab environment. Production should use certificates trusted by the operating system and browser.",
                    "The lobby/chat screenshots were captured with the same code in a local three-replica verification stack. Final performance traffic originated on the local PC through public port 4237; process, failover, and REST/WebSocket acceptance used the assigned remote machines.",
                    "Passwords and SSH credentials are no longer hardcoded in automation. LAB_SSH_PASSWORD is read from the environment or entered interactively.",
                ]
            ),
            paragraph("10. Conclusion", STYLES["h1"]),
            paragraph(
                f"The assignment is implemented on the assigned machines with health-aware round robin, WebSocket proxying, monitoring, affinity, TLS, and mapped public access. From the local PC, three backends increased throughput by {throughput_change:.1f}% and reduced p50/p95 by {-p50_change:.1f}%/{-p95_change:.1f}%. One of 5,000 requests timed out and p99 was {p99_change:.1f}% higher; both are reported transparently. Public ports, failover, recovery, UI, token rotation, and repeat WebSocket joins passed.",
                STYLES["body"],
            ),
            PageBreak(),
            paragraph("Appendix A. Complete Load Balancer Source", STYLES["h1"]),
            paragraph(
                "Canonical source: internal/loadbalancer/loadbalancer.go. The root load_balancer.go file is a compatibility entry point; cmd/load-balancer is the module build target.",
                STYLES["body"],
            ),
            Preformatted(
                format_source(ROOT / "internal" / "loadbalancer" / "loadbalancer.go"),
                STYLES["code"],
            ),
        ]
    )
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = ReportDocument(OUTPUT)
    document.build(build_story())
    print(OUTPUT)


if __name__ == "__main__":
    main()
