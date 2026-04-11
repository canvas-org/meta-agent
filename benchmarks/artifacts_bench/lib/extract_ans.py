"""Answer extraction from ArtifactsBench (src/extract_ans.py) — unmodified."""

import re


def find_pattern_matches(patterns, data):
    for pattern in patterns:
        matches = re.findall(pattern, data)
        if matches:
            return matches[-1][1]
    return None


def _compute_from_dimensions(data: str):
    """Fallback: average per-dimension 'score' fields (0-10 scale) and map to 0-100."""
    import json as _json
    try:
        start = data.find("{")
        end = data.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        parsed = _json.loads(data[start:end])
        scores = []
        for v in parsed.values():
            if isinstance(v, dict) and "score" in v:
                scores.append(float(v["score"]))
        if scores:
            return round(sum(scores) / len(scores) * 10, 1)
    except Exception:
        pass
    return None


def extract_mllm_overall(line_num, data):
    patterns = [
        r'"Overall Score":\s*(")?(\d+(\.\d+)?|\d+-\d+)(")?',
        r'"总体打分":\s*(")?(\d+(\.\d+)?|\d+-\d+)(")?',
    ]

    try:
        overall_score = find_pattern_matches(patterns, data)
        if overall_score:
            return overall_score

        dim_score = _compute_from_dimensions(data)
        if dim_score is not None:
            return dim_score

        print(
            f"Line {line_num} - 'Overall Score' or 'total_score' field not found"
        )
        return None
    except Exception as e:
        print(f"Line {line_num} - Parsing error: {e}")
        return None


def extract_last_match(pattern, text):
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[-1]
    return None


def extract_last_html_or_svg_block(text):
    try:
        html_block = extract_last_match(
            r"(<html[^>]*>.*?</html>)", text
        )
        svg_block = extract_last_match(
            r"(<svg[^>]*>.*?</svg>)", text
        )

        if html_block:
            return {"type": "html", "content": html_block}
        elif svg_block:
            return {"type": "svg", "content": svg_block}

        return {"type": "None", "content": "None"}
    except Exception as e:
        print(f"Parsing error: {e}")
        return {"type": "None", "content": "None"}
