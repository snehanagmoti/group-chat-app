"""Cross-check experiment evidence and final-report coverage."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent.parent


def read_json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def verify_experiments() -> None:
    one = read_json("1_backend.json")
    three = read_json("3_backends.json")
    one_status = read_json("1_backend_lb_status.json")
    three_status = read_json("3_backends_lb_status.json")
    one_metrics = read_json("1_backend_lb_metrics.json")
    three_metrics = read_json("3_backends_lb_metrics.json")
    with (ROOT / "results.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert one["requests"] == three["requests"] == 5000
    assert one["concurrency"] == three["concurrency"] == 200
    assert one["successful"] + one["failed"] == 5000
    assert three["successful"] + three["failed"] == 5000
    for result in (one, three):
        assert all(
            metric in result
            for metric in (
                "throughput_rps",
                "dropout_percent",
                "p50_ms",
                "p95_ms",
                "p99_ms",
            )
        )

    assert len(one_status["backends"]) == 1
    assert one_status["backends"][0]["url"].endswith(".39:5000")
    assert len(three_status["backends"]) == 3
    assert all(backend["alive"] for backend in three_status["backends"])
    assert {backend["url"] for backend in three_status["backends"]} == {
        "https://172.17.0.39:5000",
        "https://172.17.0.40:5000",
        "https://172.17.0.41:5000",
    }
    assert one_metrics["total"] == three_metrics["total"] == 5000
    assert len(rows) == 2
    assert [row["Experiment"] for row in rows] == ["1_backend", "3_backends"]

    print(
        "EXPERIMENT ARTIFACT PASS: 5,000 requests x 2, concurrency 200, "
        f"one backend then three; RPS {one['throughput_rps']:.1f} -> "
        f"{three['throughput_rps']:.1f}."
    )


def verify_report() -> None:
    report = ROOT / "output" / "pdf" / "lab5_load_balanced_backend_report.pdf"
    reader = PdfReader(str(report))
    page_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(page_text)
    required = (
        "Sneha Nagmoti",
        "12342090",
        "10.1.75.53:2237",
        "10.1.75.53:2238",
        "10.1.75.53:2239",
        "10.1.75.53:2240",
        "Local load generator",
        "public port 4237",
        "container port 4000",
        "Results and Comparison",
        "339.4",
        "439.6",
        "Complete Load Balancer Source",
        "Figure 2",
        "Figure 3",
        "Figure 4",
    )

    assert len(reader.pages) == 15
    assert all(value.strip() for value in page_text)
    missing = [value for value in required if value not in text]
    assert not missing, f"report text missing: {missing}"
    assert "8082" not in text
    assert "\ufffd" not in text
    assert "&quot;" not in text
    embedded_images = sum(len(page.images) for page in reader.pages)
    assert embedded_images >= 4
    assert len(list((ROOT / "output" / "pdf").glob("*.pdf"))) == 1

    print(
        f"REPORT PASS: 15 pages, {embedded_images} embedded images, identity, "
        "systems, mapped ports, comparison, evidence, and complete source present."
    )


def main() -> None:
    verify_experiments()
    verify_report()


if __name__ == "__main__":
    main()
