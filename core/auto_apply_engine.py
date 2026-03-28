"""
Selenium-based **autofill only** for simple application forms.

SAFETY:
- Never clicks submit — no automated form submission.
- Skipped for LinkedIn / Naukri / Indeed at the orchestrator (browser open only).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.common.by import By

from utils.helpers import logger


def apply_job(job_link: str, name: str, email: str, resume_path: str) -> Any:
    """
    Open ``job_link`` in Chrome, wait briefly, then attempt light autofill
    (name / email text inputs and file inputs). Does **not** submit the form.

    Returns:
        The WebDriver instance (caller may quit when done).
    """
    link = (job_link or "").strip()
    if not link:
        raise ValueError("apply_job: empty job_link")

    driver: webdriver.Chrome | None = None
    try:
        try:
            driver = webdriver.Chrome()
        except Exception as first:
            logger.debug("[AutoApplyEngine] webdriver.Chrome() failed: %s — trying webdriver-manager", first)
            from selenium.webdriver.chrome.service import Service

            try:
                from webdriver_manager.chrome import ChromeDriverManager

                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
            except Exception as second:
                raise RuntimeError(f"Could not start Chrome WebDriver: {second}") from second

        driver.get(link)
        time.sleep(5)

        try:
            inputs = driver.find_elements(By.TAG_NAME, "input")

            for inp in inputs:
                try:
                    placeholder = (inp.get_attribute("placeholder") or "").lower()
                    name_attr = (inp.get_attribute("name") or "").lower()

                    if "name" in placeholder or "name" in name_attr:
                        inp.send_keys(name)

                    elif "email" in placeholder or "email" in name_attr:
                        inp.send_keys(email)
                except Exception as per_inp:
                    logger.debug("[AutoApplyEngine] input skip: %s", per_inp)

            rp = Path(resume_path).expanduser()
            if rp.is_file():
                file_inputs = driver.find_elements(By.XPATH, "//input[@type='file']")
                for f in file_inputs:
                    try:
                        f.send_keys(str(rp.resolve()))
                    except Exception as fe:
                        logger.debug("[AutoApplyEngine] file input skip: %s", fe)
            else:
                logger.warning("[AutoApplyEngine] resume file not found: %s", resume_path)

            print("Autofill done")
            logger.info("[AutoApplyEngine] Autofill done (no submit) for %s…", link[:80])
        except Exception as e:
            print("Autofill error:", e)
            logger.warning("[AutoApplyEngine] Autofill error: %s", e)

        return driver
    except Exception:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        raise
