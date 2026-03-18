"""
Google Flow (labs.google) Image Generation API Server
Uses Playwright to automate the Flow UI for image generation.
Run: uvicorn flow_server:app --host 0.0.0.0 --port 5000
"""

import asyncio
import base64
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SESSION_TOKEN = (
    "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..SoCZnjlJ106sJvpP"
    ".6Haqr8Gp2HzI1h1b3rWI6Kc21gMjTFcKe3x31eYPMGY3j5QJMOO11m5wgHvAeZznZj7ynxHLTPE81ybUaIPlhbPqho49_sLELVkAfu50IywhDYHiPGJFrpkHNMCGbFqGbLdUfHb2S4HVPx1ugPFPoNI_cT_Jo81IF2Y8AGtFHvgWSge_PBfNUKrOLrHykpQrQF0D6MY8XU5ryOiXbx4dm9PW7I5namu-opMJp-Z3Wy6X7Wimg_r6_twFm-XHesdv1Gq6tBzA0ETn2Cka-34ZvpWqWq9bsE-umFbJTYJFRYQkFMmTE7perzn_M-TX_fhtR9uJDVOoDVXpp2IL1qM263aav0qfjPJDLCGTTDs0m7-iuWdlAGtCftpHZJmjgKHYji7eGJiythv0CmQVYR2PDevBEhwrNDkC1Hrbkt1rcKmqnp5GfWw9l7G3fGg51wdelIiZTber6pJecjEhlen8YxImMamziuSvA8ilo_aF7K7fYKMb6byyjhlEQ2-SjWldepFdLCJvsVyIwQLbQ0zphwTtGZ_BZhqXmkhxA9dtvkTmA6qdy_bi59FxBmaghK-SLZ3GpVpiW2rBNEr1X6oKmgpl1LiyfwATHCAuJow5KlLpDeQj-JK0UeA237j1FCZX39Oc3AjnCpE0Yh7q1edeS7e-J064CtC0gB16KqoLI-TXptohCcLJHWgz_pLyy8ntL6Pkttfd-2wpwfbQtyNj7gcLFnk9T-IKm6lSEwV413xcswmr4Xde7X_FO42KzFbqlddRAElrphuJ4A74VTaKMSECTyRf0HiXAedvu6KaXtJfKdVShp5MGnWqjkzacCxwEVYdyJa6ifnmh1ZeR7tKaaL-3RCI9xMr4WozpMfvEiyM2VwIMGMCr8Mz5fy7taaKLKKZr7go5tXSQ22rna4K__sspQY706VL7dXakJ4JShfzz1LERA-cFN8AHjAhVH_ChI8u_JwuQ1ONNnrDO1EUAh44X7PV2sPrd14mCV8f"
    ".svhPE_-lBq9OIKz_52avNw"
)

PROJECT_URL = "https://labs.google/fx"
COOKIE_DOMAIN = ".labs.google"
COOKIE_NAME = "__Secure-next-auth.session-token"

PORT = 5000
GENERATION_TIMEOUT = 150  # seconds max wait for image generation

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flow_server")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
pw_instance = None
browser: Browser = None
context: BrowserContext = None
page: Page = None
generation_lock = asyncio.Lock()


async def init_browser():
    """Launch browser, set cookie, navigate to project."""
    global pw_instance, browser, context, page

    log.info("Launching Playwright Chromium (headed)...")
    pw_instance = await async_playwright().start()

    browser = await pw_instance.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="vi-VN",
    )

    # Set the session cookie BEFORE navigating
    await context.add_cookies([
        {
            "name": COOKIE_NAME,
            "value": SESSION_TOKEN,
            "domain": COOKIE_DOMAIN,
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ])
    log.info("Session cookie set.")

    page = await context.new_page()

    log.info(f"Navigating to: {PROJECT_URL}")
    await page.goto(PROJECT_URL, wait_until="networkidle", timeout=60000)
    log.info("Page loaded. Waiting for UI to settle...")
    await page.wait_for_timeout(5000)

    current_url = page.url
    log.info(f"Current URL: {current_url}")

    # Click Flow button using JavaScript to handle unicode/special chars
    try:
        clicked = await page.evaluate("""
            () => {
                // Find all links and buttons containing "FLOW" or "Flow"
                const els = [...document.querySelectorAll('a, button')];
                for (const el of els) {
                    const text = el.textContent || '';
                    if (text.toUpperCase().includes('FLOW') && !text.toUpperCase().includes('MUSIC')) {
                        el.click();
                        return el.textContent.trim();
                    }
                }
                return null;
            }
        """)
        if clicked:
            log.info(f"Clicked Flow button: '{clicked}'")
            await page.wait_for_timeout(8000)
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            current_url = page.url
            log.info(f"After clicking Flow, URL: {current_url}")
        else:
            log.warning("No Flow button found via JS")
    except Exception as e:
        log.warning(f"Error clicking Flow: {e}")

    await page.screenshot(path="/root/flow_init.png")
    log.info(f"Screenshot saved. Current URL: {page.url}")

    # On Flow main page, we need to click on a project to open it
    # Look for the project card or "Bạn muốn tạo gì?" input
    await page.wait_for_timeout(3000)

    # Try to find and click on the most recent project (first one)
    try:
        project_clicked = await page.evaluate("""
            () => {
                // Look for project cards/thumbnails
                const cards = document.querySelectorAll('[role="listitem"], [role="button"], a[href*="project"]');
                if (cards.length > 0) {
                    cards[0].click();
                    return 'Clicked first project card';
                }
                // Try clicking any thumbnail image in the grid
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100 && rect.top > 100) {
                        img.click();
                        return 'Clicked thumbnail: ' + img.alt;
                    }
                }
                return null;
            }
        """)
        if project_clicked:
            log.info(f"Project: {project_clicked}")
            await page.wait_for_timeout(5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            log.info(f"After project click, URL: {page.url}")
            await page.screenshot(path="/root/flow_project.png")
    except Exception as e:
        log.warning(f"Error clicking project: {e}")

    # Now look for the input field
    try:
        await page.wait_for_selector(
            'textarea, [contenteditable="true"], input[type="text"], [placeholder]',
            timeout=15000,
        )
        log.info("Input field found - UI ready.")
    except Exception:
        log.warning("Could not find input field.")
        await page.screenshot(path="/root/flow_init2.png")
        # Log all interactive elements for debugging
        elements = await page.evaluate("""
            () => {
                const els = document.querySelectorAll('input, textarea, [contenteditable], [role="textbox"]');
                return Array.from(els).map(e => ({
                    tag: e.tagName,
                    type: e.type,
                    placeholder: e.placeholder,
                    role: e.getAttribute('role'),
                    visible: e.offsetParent !== null,
                    rect: e.getBoundingClientRect()
                }));
            }
        """)
        log.info(f"Found elements: {elements}")

    log.info("Browser initialization complete.")


async def cleanup_browser():
    """Close browser gracefully."""
    global pw_instance, browser
    if browser:
        await browser.close()
    if pw_instance:
        await pw_instance.stop()
    log.info("Browser closed.")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_browser()
    yield
    await cleanup_browser()


app = FastAPI(title="Google Flow Image Generator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    prompt: str
    image_base64: Optional[str] = None  # base64-encoded image (no data URI prefix)


class GenerateResponse(BaseModel):
    images: list[str]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Helper: find the prompt input element
# ---------------------------------------------------------------------------
async def find_input_field() -> object:
    """Try multiple selectors to find the prompt input."""
    selectors = [
        'textarea[placeholder*="muốn tạo"]',
        'textarea[placeholder*="Bạn muốn"]',
        'div[contenteditable="true"]',
        'textarea',
        'input[placeholder*="muốn tạo"]',
        'input[placeholder*="Bạn muốn"]',
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            log.info(f"Found input field with selector: {sel}")
            return el
    raise RuntimeError("Could not find the prompt input field on the page.")


# ---------------------------------------------------------------------------
# Helper: find the send/submit button
# ---------------------------------------------------------------------------
async def find_send_button() -> object:
    """Try multiple selectors to find the send button."""
    selectors = [
        # Common: button with arrow icon near the input
        'button[aria-label*="Send"]',
        'button[aria-label*="send"]',
        'button[aria-label*="Submit"]',
        'button[aria-label*="Gửi"]',
        # Generic icon button near textarea
        'button:has(svg)',
    ]
    for sel in selectors:
        elements = await page.query_selector_all(sel)
        for el in elements:
            if await el.is_visible():
                log.info(f"Found send button with selector: {sel}")
                return el

    # Fallback: find button near the input area at bottom of page
    # Look for the last visible button in the page
    buttons = await page.query_selector_all('button')
    visible_buttons = []
    for btn in buttons:
        if await btn.is_visible():
            box = await btn.bounding_box()
            if box and box['y'] > 500:  # bottom half of page
                visible_buttons.append((btn, box))

    if visible_buttons:
        # Pick the rightmost button near the bottom (likely the send button)
        visible_buttons.sort(key=lambda x: x[1]['x'], reverse=True)
        log.info("Found send button via position heuristic.")
        return visible_buttons[0][0]

    raise RuntimeError("Could not find the send/submit button.")


# ---------------------------------------------------------------------------
# Helper: count existing gallery images
# ---------------------------------------------------------------------------
async def count_gallery_images() -> int:
    """Count images currently visible in the gallery/output area."""
    # Try multiple selectors for gallery images
    selectors = [
        'img[src*="generated"]',
        'img[src*="blob:"]',
        'img[src*="googleusercontent"]',
        'img[src*="lh3."]',
        '.gallery img',
        '[role="img"]',
        'img[alt]',
    ]

    max_count = 0
    for sel in selectors:
        imgs = await page.query_selector_all(sel)
        # Filter to visible ones in the main content area
        count = 0
        for img in imgs:
            if await img.is_visible():
                box = await img.bounding_box()
                if box and box['width'] > 80 and box['height'] > 80:
                    count += 1
        if count > max_count:
            max_count = count
            log.info(f"Gallery image count with '{sel}': {count}")

    return max_count


# ---------------------------------------------------------------------------
# Helper: extract image URLs from gallery
# ---------------------------------------------------------------------------
async def extract_image_urls() -> list[str]:
    """Extract all gallery image URLs from Flow UI."""
    result = await page.evaluate("""
        () => {
            const urls = new Set();
            // Method 1: img tags
            document.querySelectorAll('img').forEach(img => {
                const rect = img.getBoundingClientRect();
                if (rect.width > 50 && rect.height > 50 && img.src &&
                    !img.src.startsWith('data:') && !img.src.includes('avatar') &&
                    !img.src.includes('icon') && !img.src.includes('logo')) {
                    urls.add(img.src);
                }
            });
            // Method 2: background-image CSS
            document.querySelectorAll('div, span, figure').forEach(el => {
                const bg = getComputedStyle(el).backgroundImage;
                if (bg && bg !== 'none' && bg.startsWith('url(')) {
                    const url = bg.slice(5, -2);
                    if (url.length > 50 && !url.startsWith('data:')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 50) {
                            urls.add(url);
                        }
                    }
                }
            });
            // Method 3: source elements inside picture
            document.querySelectorAll('picture source, picture img').forEach(el => {
                const src = el.srcset || el.src;
                if (src && !src.startsWith('data:')) urls.add(src.split(' ')[0]);
            });
            return [...urls];
        }
    """)
    return result or []


# ---------------------------------------------------------------------------
# Helper: upload reference image
# ---------------------------------------------------------------------------
async def upload_reference_image(image_base64: str):
    """Upload a reference image via the '+' button in Flow UI.

    Flow UI workflow: click '+' → menu appears → click upload/image option → file chooser opens.
    Fallbacks: clipboard paste, hidden file input with event dispatch.
    """
    log.info("Uploading reference image...")

    img_data = base64.b64decode(image_base64)
    suffix = ".png"
    mime_type = "image/png"
    if img_data[:3] == b'\xff\xd8\xff':
        suffix = ".jpg"
        mime_type = "image/jpeg"
    elif img_data[:4] == b'\x89PNG':
        suffix = ".png"
        mime_type = "image/png"
    elif img_data[:4] == b'RIFF':
        suffix = ".webp"
        mime_type = "image/webp"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(img_data)
    tmp.close()
    tmp_path = tmp.name

    try:
        # ── Method 1: Click "+" → menu → click image/upload option → file chooser ──
        try:
            log.info("Method 1: Click '+' to open menu...")
            # Click the "+" button to open attachment menu
            plus_clicked = await page.evaluate("""
                () => {
                    const buttons = [...document.querySelectorAll('button')];
                    for (const btn of buttons) {
                        const rect = btn.getBoundingClientRect();
                        const text = btn.textContent || '';
                        const ariaLabel = btn.getAttribute('aria-label') || '';
                        if (rect.top > window.innerHeight * 0.5 &&
                            (text.trim() === '+' || text.includes('add') ||
                             ariaLabel.toLowerCase().includes('add') ||
                             ariaLabel.toLowerCase().includes('thêm') ||
                             ariaLabel.toLowerCase().includes('attach') ||
                             ariaLabel.toLowerCase().includes('đính kèm'))) {
                            btn.click();
                            return {text: text.trim(), label: ariaLabel, y: rect.top};
                        }
                    }
                    // Fallback: find button with just a "+" icon near bottom
                    for (const btn of buttons) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.top > window.innerHeight * 0.5 && rect.height < 60 && rect.width < 60) {
                            const svg = btn.querySelector('svg');
                            if (svg) {
                                btn.click();
                                return {text: 'svg-button', y: rect.top};
                            }
                        }
                    }
                    return null;
                }
            """)
            log.info(f"'+' button click result: {plus_clicked}")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="/root/flow_plus_menu.png")

            if plus_clicked:
                # Now look for image/upload option in the appeared menu
                # and wrap expect_file_chooser around THAT click
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    menu_click = await page.evaluate("""
                        () => {
                            // Look for menu items, popover options, etc.
                            const selectors = '[role="menuitem"], [role="option"], [role="listbox"] > *, li, [class*="menu"] button, [class*="popup"] button, [class*="popover"] button, [class*="dropdown"] button, [class*="overlay"] button';
                            const items = document.querySelectorAll(selectors);
                            for (const item of items) {
                                const text = (item.textContent || '').toLowerCase();
                                if (text.includes('image') || text.includes('ảnh') ||
                                    text.includes('hình') || text.includes('upload') ||
                                    text.includes('tải lên') || text.includes('file') ||
                                    text.includes('tệp') || text.includes('photo') ||
                                    text.includes('picture')) {
                                    item.click();
                                    return text.trim();
                                }
                            }
                            // If no specific menu item found, try clicking any newly visible element
                            // that might be a file upload trigger
                            const allButtons = document.querySelectorAll('button, a, [role="button"]');
                            for (const btn of allButtons) {
                                const rect = btn.getBoundingClientRect();
                                const text = (btn.textContent || '').toLowerCase();
                                // Look for overlay/popup buttons (not in the bottom bar)
                                if (rect.top > 200 && rect.top < window.innerHeight * 0.7 &&
                                    rect.width > 40 &&
                                    (text.includes('image') || text.includes('ảnh') || text.includes('upload'))) {
                                    btn.click();
                                    return 'fallback: ' + text.trim();
                                }
                            }
                            return null;
                        }
                    """)
                    log.info(f"Menu option click result: {menu_click}")

                file_chooser = await fc_info.value
                await file_chooser.set_files(tmp_path)
                log.info("Image uploaded via menu → file chooser.")
                await page.wait_for_timeout(5000)
                await page.screenshot(path="/root/flow_upload_success.png")
                return

        except Exception as e:
            log.warning(f"Method 1 (menu + file chooser) failed: {e}")
            await page.screenshot(path="/root/flow_upload_m1_fail.png")
            # Close any open menu by pressing Escape
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        # ── Method 2: Click "+" directly expecting file chooser (some UIs skip menu) ──
        try:
            log.info("Method 2: Direct '+' click → file chooser...")
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                await page.evaluate("""
                    () => {
                        const buttons = [...document.querySelectorAll('button')];
                        for (const btn of buttons) {
                            const rect = btn.getBoundingClientRect();
                            const text = btn.textContent || '';
                            const ariaLabel = btn.getAttribute('aria-label') || '';
                            if (rect.top > window.innerHeight * 0.5 &&
                                (text.trim() === '+' || ariaLabel.toLowerCase().includes('add') ||
                                 ariaLabel.toLowerCase().includes('thêm'))) {
                                btn.click();
                                return;
                            }
                        }
                    }
                """)
            file_chooser = await fc_info.value
            await file_chooser.set_files(tmp_path)
            log.info("Image uploaded via direct '+' → file chooser.")
            await page.wait_for_timeout(5000)
            return
        except Exception as e:
            log.warning(f"Method 2 (direct file chooser) failed: {e}")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        # ── Method 3: Clipboard paste into the input field ──
        try:
            log.info("Method 3: Clipboard paste via DataTransfer...")
            input_field = await find_input_field()
            await input_field.click()
            await page.wait_for_timeout(300)

            b64str = base64.b64encode(img_data).decode()
            pasted = await page.evaluate("""
                ([b64, mimeType, fileName]) => {
                    try {
                        const byteChars = atob(b64);
                        const byteArray = new Uint8Array(byteChars.length);
                        for (let i = 0; i < byteChars.length; i++) {
                            byteArray[i] = byteChars.charCodeAt(i);
                        }
                        const blob = new Blob([byteArray], { type: mimeType });
                        const file = new File([blob], fileName, { type: mimeType, lastModified: Date.now() });

                        const dt = new DataTransfer();
                        dt.items.add(file);

                        const target = document.querySelector('[contenteditable="true"]') ||
                                       document.querySelector('textarea') ||
                                       document.activeElement;

                        // Try paste event
                        const pasteEvt = new ClipboardEvent('paste', {
                            clipboardData: dt,
                            bubbles: true,
                            cancelable: true
                        });
                        const handled = !target.dispatchEvent(pasteEvt);

                        // Also try drop event as fallback
                        const dropEvt = new DragEvent('drop', {
                            dataTransfer: dt,
                            bubbles: true,
                            cancelable: true
                        });
                        target.dispatchEvent(dropEvt);

                        return handled ? 'paste-handled' : 'paste-dispatched';
                    } catch(e) {
                        return 'error: ' + e.message;
                    }
                }
            """, [b64str, mime_type, f"reference{suffix}"])

            log.info(f"Clipboard paste result: {pasted}")
            await page.wait_for_timeout(5000)
            await page.screenshot(path="/root/flow_paste_check.png")

            # Verify image appeared (check for thumbnail/preview near input)
            has_preview = await page.evaluate("""
                () => {
                    // Check if an image preview appeared near the input area
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        const rect = img.getBoundingClientRect();
                        if (rect.top > window.innerHeight * 0.4 &&
                            rect.width > 30 && rect.width < 300 &&
                            (img.src.startsWith('blob:') || img.src.startsWith('data:'))) {
                            return true;
                        }
                    }
                    return false;
                }
            """)
            if has_preview:
                log.info("Image preview detected after paste - upload successful!")
                return
            else:
                log.warning("No image preview detected after paste.")

        except Exception as e:
            log.warning(f"Method 3 (clipboard paste) failed: {e}")

        # ── Method 4: Hidden file input with dispatched events ──
        try:
            log.info("Method 4: Hidden file input + change event...")
            file_inputs = await page.query_selector_all('input[type="file"]')
            for fi in file_inputs:
                await fi.set_input_files(tmp_path)
                # Dispatch events to trigger React/framework state update
                await fi.evaluate("el => { el.dispatchEvent(new Event('change', {bubbles: true})); el.dispatchEvent(new Event('input', {bubbles: true})); }")
                log.info("Image set via hidden file input + dispatched change event.")
                await page.wait_for_timeout(5000)
                await page.screenshot(path="/root/flow_hidden_input_check.png")
                return
        except Exception as e:
            log.warning(f"Method 4 (hidden input) failed: {e}")

        log.warning("All upload methods failed. Proceeding with text-only prompt.")
        await page.screenshot(path="/root/flow_upload_fail.png")

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main generate endpoint
# ---------------------------------------------------------------------------
@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if not page:
        raise HTTPException(status_code=503, detail="Browser not initialized yet.")

    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    async with generation_lock:
        start_time = time.time()
        log.info(f"=== Generation request: prompt={req.prompt[:80]}... has_image={req.image_base64 is not None}")

        try:
            # Step 1: If image provided, click "+" first, upload, wait for load
            if req.image_base64:
                log.info("Uploading reference image via '+' button...")
                await upload_reference_image(req.image_base64)
                await page.wait_for_timeout(3000)
                log.info("Reference image uploaded.")

            # Step 2: Find and fill the prompt AFTER image is loaded
            input_field = await find_input_field()
            await input_field.click()
            await page.wait_for_timeout(300)

            # Clear existing text (Ctrl+A on Linux server, Meta+A on Mac)
            import platform
            select_all_key = "Meta+a" if platform.system() == "Darwin" else "Control+a"
            await page.keyboard.press(select_all_key)
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)

            # Type the prompt
            await input_field.fill(req.prompt)
            await page.wait_for_timeout(500)
            log.info("Prompt entered.")

            # Step 3: Press Enter to send
            await page.keyboard.press("Enter")
            send_time = time.time()
            log.info("Enter pressed. Waiting for generation...")

            # Step 4: Wait 8 seconds THEN start network listener
            # This skips reference image re-display URLs
            await page.wait_for_timeout(8000)

            new_image_urls = []
            seen_uuids = set()

            def on_response(response):
                url = response.url
                # Only capture media redirect URLs (not storage duplicates)
                if "media.getMediaUrlRedirect" in url:
                    # Extract UUID from ?name=UUID
                    import re as _re
                    uuid_match = _re.search(r'name=([a-f0-9-]{36})', url)
                    if uuid_match:
                        uuid = uuid_match.group(1)
                        if uuid not in seen_uuids:
                            seen_uuids.add(uuid)
                            new_image_urls.append(url)
                            log.info(f"Captured new image UUID={uuid}")

            page.on("response", on_response)

            try:
                # Step 5: Wait for new image URLs from network
                deadline = time.time() + GENERATION_TIMEOUT - 8

                while time.time() < deadline:
                    if new_image_urls:
                        # Wait a bit more for additional images
                        await page.wait_for_timeout(5000)
                        log.info(f"Found {len(new_image_urls)} image URL(s) from network!")
                        break

                    await page.wait_for_timeout(3000)
                    elapsed = time.time() - start_time
                    log.info(f"Still waiting... ({elapsed:.0f}s elapsed, {len(new_image_urls)} captured)")

                elapsed = time.time() - start_time

                if not new_image_urls:
                    # Fallback: try extracting from DOM - get only the newest images
                    log.warning("No URLs from network. Trying DOM fallback...")
                    new_image_urls = await page.evaluate("""
                        () => {
                            const imgs = [...document.querySelectorAll('img')];
                            // Get last few images (most recently added)
                            return imgs
                                .filter(img => {
                                    const r = img.getBoundingClientRect();
                                    return r.width > 80 && r.height > 80 && img.src &&
                                           !img.src.startsWith('data:') &&
                                           (img.src.includes('media') || img.src.includes('googleapis'));
                                })
                                .slice(-4)
                                .map(img => img.src);
                        }
                    """) or []

                if not new_image_urls:
                    raise HTTPException(
                        status_code=504,
                        detail=f"No new images after {elapsed:.0f}s.",
                    )

                # Deduplicate URLs (keep order)
                seen = set()
                unique_urls = []
                for u in new_image_urls:
                    if u not in seen:
                        seen.add(u)
                        unique_urls.append(u)

                # Convert to proxied URLs
                import urllib.parse
                proxied_urls = [f"/api/image?url={urllib.parse.quote(u, safe='')}" for u in unique_urls]
                log.info(f"Generation complete: {len(unique_urls)} unique images in {elapsed:.1f}s")
                return GenerateResponse(images=proxied_urls, elapsed_seconds=round(elapsed, 1))

            finally:
                page.remove_listener("response", on_response)

        except HTTPException:
            raise
        except Exception as e:
            log.exception("Generation failed")
            raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Image proxy - fetch Flow images using authenticated browser
# ---------------------------------------------------------------------------
@app.get("/image")
async def proxy_image(url: str):
    """Proxy Flow images through our server (they require auth cookies)."""
    from fastapi.responses import Response
    if not context:
        raise HTTPException(status_code=503, detail="Browser not initialized.")
    try:
        # Use the browser context's cookies to fetch the image
        api_context = context.request
        resp = await api_context.get(url, timeout=30000)
        content_type = resp.headers.get("content-type", "image/png")
        body = await resp.body()
        return Response(content=body, media_type=content_type)
    except Exception as e:
        log.error(f"Image proxy error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "browser_ready": page is not None,
        "project_url": PROJECT_URL,
    }


# ---------------------------------------------------------------------------
# Screenshot (for debugging)
# ---------------------------------------------------------------------------
@app.get("/screenshot")
async def screenshot():
    if not page:
        raise HTTPException(status_code=503, detail="Browser not initialized.")
    img_bytes = await page.screenshot(full_page=False)
    b64 = base64.b64encode(img_bytes).decode()
    from fastapi.responses import HTMLResponse
    return HTMLResponse(f'<img src="data:image/png;base64,{b64}" style="max-width:100%">')


# ---------------------------------------------------------------------------
# Debug: dump page HTML
# ---------------------------------------------------------------------------
@app.get("/debug-html")
async def debug_html():
    if not page:
        raise HTTPException(status_code=503, detail="Browser not initialized.")
    html = await page.content()
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(html[:50000])


# ---------------------------------------------------------------------------
# Reload page (in case UI gets stuck)
# ---------------------------------------------------------------------------
@app.post("/reload")
async def reload_page():
    if not page:
        raise HTTPException(status_code=503, detail="Browser not initialized.")
    log.info("Reloading Flow page...")
    await page.goto(PROJECT_URL, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(3000)
    log.info("Page reloaded.")
    return {"status": "reloaded"}


# ---------------------------------------------------------------------------
# Update session token
# ---------------------------------------------------------------------------
class UpdateTokenRequest(BaseModel):
    token: str


@app.post("/update-token")
async def update_token(req: UpdateTokenRequest):
    """Update the session cookie (useful when token expires)."""
    if not context:
        raise HTTPException(status_code=503, detail="Browser not initialized.")

    await context.add_cookies([
        {
            "name": COOKIE_NAME,
            "value": req.token,
            "domain": COOKIE_DOMAIN,
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ])
    log.info("Session token updated. Reloading page...")
    await page.reload(wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(3000)
    return {"status": "token_updated"}


# ---------------------------------------------------------------------------
# Run with uvicorn
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
