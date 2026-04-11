"""Screenshot capture using Playwright — extracted from ArtifactsBench src/utils.py"""

import time
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright


def capture_html_screenshots(
    html_path: str,
    img_path: List[str],
    num_screenshots: int = 3,
    interval: int = 1,
    max_retries: int = 2,
    timeout: int = 600000,
) -> None:
    """Capture screenshots of HTML content using Playwright."""
    try:
        html_path_obj = Path(html_path) if not isinstance(html_path, Path) else html_path
        for attempt in range(1, max_retries + 1):
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    try:
                        context = browser.new_context()
                        page = context.new_page()
                        page.set_default_timeout(timeout)
                        page.goto(
                            f"file://{html_path_obj.resolve()}", timeout=timeout
                        )
                        page.wait_for_load_state("networkidle", timeout=timeout)
                        for i in range(num_screenshots):
                            page.screenshot(
                                path=img_path[i], full_page=True, timeout=timeout
                            )
                            if i < num_screenshots - 1:
                                time.sleep(interval)
                        break
                    finally:
                        if context:
                            context.close()
                        if browser:
                            browser.close()
            except Exception as e:
                if attempt == max_retries:
                    print(f"Screenshot attempt {attempt} failed: {e}")
                    return None
    except Exception:
        pass
