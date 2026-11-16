"""
universal_checks.py — Evaluates Universal Checks U1 to U10 on every chat response.
"""

from __future__ import annotations

import re
from typing import Any, Optional


FORBIDDEN_CHARS = {
    "\u202f": "narrow no-break space (U+202F)",
    "\u2009": "thin space (U+2009)",
    "\u200a": "hair space (U+200A)",
    "\u00a0": "no-break space (U+00A0)",
}

TECHNICAL_LEAKAGE_TERMS = [
    r"\bdata_ref\b",
    r"\bdf_[0-9a-fA-F]+\b",
    r"\bTaskResult\b",
    r"\bfallback\b",
    r"\brow_count\b",
    r"\binverter[-_]days?\b",
    r"\w+_tool\b",
    r"\b[tT][1-9]\b",  # t1, t2, T1
    r"```(?:json|python)?",
    r"\{[\"'][a-zA-Z0-9_]+[\"']\s*:",  # raw JSON objects like {"key":
]

WARNING_LEAKAGE_TERMS = [
    r"\bplant_ids?\b",
    r"\bfilter\b",
    r"\bparams?\b",
    r"\btool\b",
    r"\bdataframe\b",
]

CAUSAL_PHRASES = [
    r"because of high temperature",
    r"overheat",
    r"communication glitch",
    r"\bgrid\b",
    r"\bdue to\b",
    r"\bcaused by\b",
    r"\bbecause\b",
]


def check_universal(
    payload: dict[str, Any],
    elapsed_ms: float,
    llm_calls: dict[str, Any],
    is_out_of_scope: bool = False,
    known_numbers: set[float] | None = None,
) -> dict[str, Any]:
    """
    Evaluates U1 through U10.
    Returns:
      {
         "passed": bool,
         "failures": list[dict], # {id, severity, message}
         "reviews": list[dict],
         "warnings": list[str]
      }
    """
    failures = []
    reviews = []
    notes = []

    headline = payload.get("headline") or payload.get("short_answer") or ""
    explanation = payload.get("explanation") or ""
    key_points = payload.get("key_points") or []
    notes_list = payload.get("notes") or []
    period = payload.get("period")

    all_text = " ".join([
        str(headline),
        str(explanation),
        " ".join(str(kp) for kp in key_points),
        " ".join(str(n) for n in notes_list),
        payload.get("answer_markdown", ""),
    ])

    # U1: Headline exists and is non-empty
    if not str(headline).strip():
        failures.append({"id": "U1", "severity": "FAIL", "message": "Headline is empty or missing."})

    # U2: Period stated for data answers (unless out of scope)
    if not is_out_of_scope:
        has_period_obj = isinstance(period, dict) and bool(period.get("start")) and bool(period.get("end"))
        has_period_text = any(
            w in all_text.lower() for w in [
                "2026", "august", "september", "july", "october", "period", "last 30 days", "last 28 days", "dates"
            ]
        )
        if not (has_period_obj or has_period_text):
            failures.append({"id": "U2", "severity": "FAIL", "message": "Period not stated in payload or answer text."})

    # U3: Technical leakage
    leakage_found = []
    for pat in TECHNICAL_LEAKAGE_TERMS:
        m = re.search(pat, all_text, re.IGNORECASE)
        if m:
            leakage_found.append(m.group(0))
    if leakage_found:
        failures.append({"id": "U3", "severity": "FAIL", "message": f"Technical leakage found: {list(set(leakage_found))}"})

    # U4: Forbidden whitespace characters
    found_chars = []
    for char, name in FORBIDDEN_CHARS.items():
        if char in all_text:
            found_chars.append(name)
    if found_chars:
        failures.append({"id": "U4", "severity": "FAIL", "message": f"Special whitespace characters found: {found_chars}"})

    # U5: Number formatting (>2 decimal places or raw snake_case)
    # Look for floats with >2 decimal digits: e.g. 12.3456
    decimals_over_two = re.findall(r"\b\d+\.\d{3,}\b", all_text)
    # Exclude harmless ISO timestamps or version numbers if any
    decimals_over_two = [d for d in decimals_over_two if not d.startswith("127.0.0")]
    if decimals_over_two:
        failures.append({"id": "U5", "severity": "FAIL", "message": f"Numbers with >2 decimal places found: {decimals_over_two[:5]}"})

    snake_case_matches = re.findall(r"\b[a-z]+_[a-z]+(?:_[a-z]+)*\b", all_text)
    allowed_snake = {"log_date", "inverter_id", "plant_id", "block_id", "block_a", "block_b", "block_c", "block_d", "block_e", "block_f"}
    bad_snake = [s for s in snake_case_matches if s.lower() not in allowed_snake and not s.startswith("inv_")]
    if bad_snake:
        failures.append({"id": "U5", "severity": "FAIL", "message": f"Raw snake_case column names found: {list(set(bad_snake))[:5]}"})

    # U7: Efficiency checks
    total_calls = llm_calls.get("total", 0) if isinstance(llm_calls, dict) else 0
    if total_calls > 12:
        failures.append({"id": "U7", "severity": "FAIL", "message": f"Exceeded max 12 LLM calls ({total_calls} calls made)."})

    if elapsed_ms > 60000:
        failures.append({"id": "U7", "severity": "FAIL", "message": f"Elapsed time exceeded 60 s ({elapsed_ms/1000.0:.1f} s)."})
    elif elapsed_ms > 30000:
        notes.append(f"WARN: Elapsed time was high ({elapsed_ms/1000.0:.1f} s > 30 s).")

    # U8: Warnings in plain language
    warnings_list = payload.get("warnings") or []
    for w in warnings_list:
        w_text = str(w)
        for pat in WARNING_LEAKAGE_TERMS:
            if re.search(pat, w_text, re.IGNORECASE):
                failures.append({"id": "U8", "severity": "FAIL", "message": f"Technical leakage in warning: '{w_text}' matches '{pat}'"})
                break

    # U9: Invented cause (Flag as REVIEW)
    causal_found = []
    for pat in CAUSAL_PHRASES:
        m = re.search(pat, all_text, re.IGNORECASE)
        if m:
            causal_found.append(m.group(0))
    if causal_found:
        reviews.append({"id": "U9", "severity": "REVIEW", "message": f"Potential unverified causal phrase(s): {list(set(causal_found))}"})

    # U10: Contradictory statements (Flag as REVIEW)
    has_no_faults = bool(re.search(r"\bno\s+faults?\b", all_text, re.IGNORECASE))
    has_fault_flag = bool(re.search(r"\bfaulty\b|\btripped\b|\bstatus\s*code\s*[234]\b", all_text, re.IGNORECASE))
    if has_no_faults and has_fault_flag:
        reviews.append({"id": "U10", "severity": "REVIEW", "message": "Contradictory fault statements: mentions 'no faults' alongside fault flags."})

    passed = len(failures) == 0
    return {
        "passed": passed,
        "failures": failures,
        "reviews": reviews,
        "notes": notes,
    }

