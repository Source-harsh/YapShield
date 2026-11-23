"""Benchmark the rules-only release pipeline on synthetic text PDFs."""

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import version
from statistics import median
from time import perf_counter

import pymupdf

from privydoc import api, detection, documents, identity
from privydoc.summary_privacy import check_summary


def peak_rss_mib():
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ("peak", "working", "paged_peak", "paged", "nonpaged_peak",
                                                   "nonpaged", "pagefile", "pagefile_peak")
            ]

        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi")
        read = psapi.GetProcessMemoryInfo
        read.argtypes = (ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong)
        counters = Counters()
        counters.size = ctypes.sizeof(counters)
        return counters.peak / 2**20 if read(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.size) else None

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (2**20 if sys.platform == "darwin" else 1024)


def fixture(pages):
    with pymupdf.open() as source:
        for index in range(pages):
            page = source.new_page()
            lines = [
                "Name: Jane Doe",
                f"Contact: reviewer{index:03d}@example.org",
                f"Employee ID: EMP-{1000 + index}",
                "The team finished the quarterly report and approved publication.",
            ]
            lines += ["The review team recorded the outcome and archived the public report."] * 20
            page.insert_text((50, 55), "\n".join(lines), fontsize=10)
        return source.tobytes(deflate=True)


def run(pages, runs):
    durations = {}

    def timed(name, action, *args, **kwargs):
        start = perf_counter()
        result = action(*args, **kwargs)
        durations.setdefault(name, []).append((perf_counter() - start) * 1000)
        return result

    def generate_summary(prepared):
        documents.validate(prepared.summary_input, prepared.summary_blocked)
        summary = documents.summarize(prepared.summary_input)
        check_summary(summary, prepared.summary_input, prepared.summary_blocked)
        return summary

    payload = fixture(pages)
    # The final summary check uses the same rule engine, with the optional model disabled.
    detection.local_nlp = lambda: None
    for iteration in range(runs + 1):
        text = timed("extract_pdf", documents.extract, payload, pdf=True)
        raw = timed("detect_pii", detection.detect_mentions, text, use_nlp=False)
        groups, clusters = timed("resolve_identity", identity.resolve, text, raw)
        review = api.Review(text, groups, 0, clusters)
        selection = api.ReleaseRequest(
            mention_decisions={identity.mention_id(item): "redact" for group in groups for item in group},
            links={identity.mention_id(link.mention): api.LinkDecision(status="confirm")
                   for cluster in clusters.values() for link in cluster.alternates},
            manual=[],
        )
        prepared = timed("preview", api.prepare_redactions, review, selection)
        checks = {"required": prepared.required, "allowed": prepared.allowed}
        txt = timed("export_txt", lambda: documents.verify_txt(
            prepared.text.encode("utf-8"), prepared.text, prepared.removed, **checks))
        pdf = timed("export_pdf", documents.export_pdf, txt, prepared.removed, **checks)
        summary = timed("summary", generate_summary, prepared)
        if not pdf or not summary or len(selection.mention_decisions) != 3 * pages:
            raise ValueError("The synthetic fixture did not exercise the complete release path.")
        if iteration == 0:
            durations.clear()
    return {"pages": pages, "upload_bytes": len(payload), "characters": len(text),
            "candidates": len(selection.mention_decisions), "pdf_bytes": len(pdf),
            "median_ms": {name: round(median(times), 2) for name, times in durations.items()},
            "max_ms": {name: round(max(times), 2) for name, times in durations.items()},
            "peak_process_rss_mib": peak_rss_mib()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--pages", type=int, choices=(1, 10, 50), help="Run a single size in the current process")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.pages:
        print(json.dumps(run(args.pages, args.runs)))
        return
    cases = [json.loads(subprocess.check_output(
        [sys.executable, "-B", "-m", "evaluation.benchmark_release", "--pages", str(pages), "--runs", str(args.runs)],
        text=True,
    )) for pages in (1, 10, 50)]
    print(json.dumps({
        "environment": {"os": platform.platform(), "cpu": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor(),
                        "logical_cpus": os.cpu_count(), "python": platform.python_version(),
                        "pymupdf": version("PyMuPDF"), "fastapi": version("fastapi")},
        "runs_per_size": args.runs, "warmup_per_size": 1, "cases": cases,
    }, indent=2))


if __name__ == "__main__":
    main()
