#!/usr/bin/env python3
"""Code parser from ArtifactsBench (src/code_parser.py) — unmodified."""

import re
import sys


def parse_code(project_value):
    """
    Parses the project value to extract code blocks, classifies them by language,
    and returns a list of dictionaries containing file names, languages, code, and position of each block.
    """
    result = []
    if isinstance(project_value, dict):
        for key, value in project_value.items():
            result.append({"file_name": key, "content": value})
        return result

    all_codes = {}
    css_file_names = []
    js_file_names = []

    matches = re.finditer(r"```(\w+)\n(.*?)```", project_value, flags=re.DOTALL)

    for match in matches:
        begin, end = match.span()
        language = match.group(1).lower()
        code = match.group(2)

        if language == "html":
            result.append(
                {
                    "file_name": "index.html",
                    "language": language,
                    "content": code,
                    "pos": [begin, end],
                }
            )
            css_file_names, js_file_names = extract_css_js_files(code)

        if language not in all_codes:
            all_codes[language] = []
        all_codes[language].append({"code": code, "pos": [begin, end]})

    result.extend(
        process_css_js_files(project_value, all_codes, css_file_names, js_file_names)
    )

    return result


def extract_css_js_files(code):
    css_files = re.findall(
        r'<link[^>]*rel=["\'](stylesheet|preload)["\'][^>]*href=["\']((?!https?:\/\/).*?)["\']',
        code,
    )
    js_files = re.findall(r'<script[^>]*src=["\']((?!https?:\/\/).*?)["\']', code)

    css_file_names = [match[1] for match in css_files]
    js_file_names = js_files
    return css_file_names, js_file_names


def process_css_js_files(project_value, all_codes, css_file_names, js_file_names):
    result = []
    last_line = ""

    for line in project_value.split("\n"):
        if not re.match(r"```(\w+)", line):
            last_line = line
            continue
        elif re.match(r"```css", line, re.IGNORECASE):
            result.extend(
                process_css_block(last_line, all_codes["css"], css_file_names)
            )
        elif re.match(r"```javascript", line, re.IGNORECASE):
            result.extend(
                process_js_block(last_line, all_codes["javascript"], js_file_names)
            )

    return result


def process_css_block(last_line, css_codes, css_file_names):
    result = []
    last_line_match = re.findall(r"([\w\-/]+\.css)", last_line, re.IGNORECASE)
    code_info = css_codes.pop(0)

    if len(css_file_names) == 0:
        result.append(
            {
                "file_name": "styles.css",
                "language": "css",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )
    elif last_line_match:
        result.append(
            {
                "file_name": last_line_match[0],
                "language": "css",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )
        try:
            css_file_names.remove(last_line_match[0])
        except BaseException:
            pass
    else:
        result.append(
            {
                "file_name": css_file_names.pop(0),
                "language": "css",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )

    return result


def process_js_block(last_line, js_codes, js_file_names):
    result = []
    last_line_match = re.findall(r"([\w\-/]+\.js)", last_line, re.IGNORECASE)
    code_info = js_codes.pop(0)

    if len(js_file_names) == 0:
        result.append(
            {
                "file_name": "script.js",
                "language": "javascript",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )
    elif last_line_match:
        result.append(
            {
                "file_name": last_line_match[0],
                "language": "javascript",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )
        try:
            js_file_names.remove(last_line_match[0])
        except BaseException:
            pass
    else:
        result.append(
            {
                "file_name": js_file_names.pop(0),
                "language": "javascript",
                "content": code_info["code"],
                "pos": code_info["pos"],
            }
        )

    return result


def insert_unmatched_files(html_content, files_to_insert, tag_type, tag_end):
    for file in files_to_insert:
        html_content = re.sub(
            tag_end,
            f"<{tag_type}>/* {file} not found in result */\n{tag_end}",
            html_content,
            1,
        )
    return html_content


def replace_references_with_code(result):
    html_content = next(
        item["content"] for item in result if item["file_name"] == "index.html"
    )

    file_content_map = {
        item["file_name"]: item["content"]
        for item in result
        if item["language"] in ["css", "javascript"]
    }

    css_to_insert = [item["file_name"] for item in result if item["language"] == "css"]
    js_to_insert = [
        item["file_name"] for item in result if item["language"] == "javascript"
    ]

    html_content = re.sub(
        r'<link[^>]*href=["\'](.*?)["\'][^>]*>',
        lambda match: replace_link_tag(match, file_content_map, css_to_insert),
        html_content,
    )
    html_content = re.sub(
        r'<script[^>]*src=["\'](.*?)["\'][^>]*></script>',
        lambda match: replace_script_tag(match, file_content_map, js_to_insert),
        html_content,
    )

    html_content = insert_unmatched_files(
        html_content, css_to_insert, "style", "</head>"
    )

    html_content = insert_unmatched_files(
        html_content, js_to_insert, "script", "</body>"
    )

    return html_content


def replace_link_tag(match, file_content_map, css_to_insert):
    href = match.group(1)
    if href in file_content_map and href.endswith(".css"):
        css_to_insert.remove(href)
        return f"<style>{file_content_map[href]}</style>"
    return match.group(0)


def replace_script_tag(match, file_content_map, js_to_insert):
    src = match.group(1)
    if src in file_content_map and src.endswith(".js"):
        js_to_insert.remove(src)
        return f"<script>{file_content_map[src]}</script>"
    return match.group(0)


def extract_html(project_value):
    all_code = parse_code(project_value)
    html_code = replace_references_with_code(all_code)
    return html_code
