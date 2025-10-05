# content_card.py — WhatsApp Review Order banners with robust font loading
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Tuple, Optional
import os, time, re, unicodedata, glob, sys
from dotenv import load_dotenv

load_dotenv()

DEFAULT_UPLOAD_DIR = os.getenv("DEFAULT_UPLOAD_DIR")
if not DEFAULT_UPLOAD_DIR:
    raise RuntimeError("Environment variable `default_upload_dir` must be set.")


# ---------- Public API ----------
def generate_review_order_card(
    out_dir: str = DEFAULT_UPLOAD_DIR,
    size: Tuple[int, int] = (1200, 628),
    variant: str = "og_header",  # "og_header" | "waba_header2x" | "square"
    # Font controls
    font_regular_path: Optional[str] = None,
    font_bold_path: Optional[str] = None,
    font_family: Optional[str] = None,  # e.g. "Inter" (auto-resolve per-OS)
    debug_print: bool = True,
    # Background
    background_path: Optional[str] = None,   # NEW
    overlay: bool = True,                    # NEW (translucent overlay)
    overlay_opacity: int = 60,               # 0–255
    # Header / brand
    brand_name: str = "bab-ai.com Procurement System",
    brand_pill_text: str = "Procurement",
    heading: str = "Review Order",
    # Meta
    site_name: str = "AS Elite, Kakinada",
    order_id: str = "MR-08A972B5",
    items_count_text: str = "3 materials",
    delivery_text: str = "Fri, 22 Aug",
    quotes_text: str = "3 in (best ₹—)",
    payment_text: str = "Credit available",
    # Items
    items: Optional[List[Dict[str, str]]] = None,
    # Summary
    total_value: str = "₹ 3,45,600",
    total_subnote: str = "incl. GST • freight extra",
    quotes_ready_count: int = 3,
    # Footer
    footer_hint: str = "Tap a button below to continue",
    # Back-compat & future-proof
    **unused
) -> str:
    """
      - variant="og_header": 1200x628 (1.91:1)
      - variant="waba_header2x": 1600x836 — crisp for WhatsApp
      - variant="square": 1080x1080 — fallback
    """ 
    def normalize_items(raw_items: List[Dict]) -> List[Dict[str, str]]:
        normalized = []
        for it in raw_items:
            if "name" in it and "qty" in it:
                normalized.append({"name": it["name"], "qty": it["qty"]})
                continue

            parts = []
            if it.get("material"):
                parts.append(str(it["material"]))
            if it.get("sub_type"):
                parts.append(str(it["sub_type"]))
            if it.get("dimensions"):
                dim = str(it["dimensions"])
                if it.get("dimension_units"):
                    dim += f" {it['dimension_units']}"
                parts.append(dim)

            name = " ".join(parts).strip()

            qty_val = it.get("quantity")
            if qty_val is None or str(qty_val).strip() == "":
                qty = "—"   # or "N/A"
            else:
                qty_units = it.get("quantity_units", "pcs")
                qty = f"{qty_val} {qty_units}"

            normalized.append({"name": name or "—", "qty": qty})
        return normalized

    if items is None:
        items = [{"name": "Please resend your request", "qty": "N/A"}]
    items = normalize_items(items)
    print("content Card::: Generate Review order Card::: items", items)
    if variant == "waba_header2x":
        factor = len(items) if items else 1
        size = (500, 500 * factor)
    elif variant == "square":
        size = (1080, 1080)

    W, H = size
    u = max(5, round(W / 24))

    # Palette (for fallback + text)
    bg = (242, 240, 237)
    fg = (57, 53, 44)
    muted = (88, 100, 112)
    accent = (0, 158, 150)
    rule = (222, 227, 234)
    zebra = (242, 240, 237)
    pill_bg = (220, 244, 240)
    card_bg = (230, 244, 242)

    # ---------- BACKGROUND HANDLING ----------
    if background_path and os.path.exists(background_path):
        bg_img = Image.open(background_path).convert("RGB")
        bg_img = bg_img.resize((W, H), Image.LANCZOS)
        im = bg_img.copy()
        if overlay: 
            overlay_layer = Image.new("RGBA", (W, H), (255, 255, 255, overlay_opacity))
            im = Image.alpha_composite(im.convert("RGBA"), overlay_layer).convert("RGB")
    else:
        im = Image.new("RGB", (W, H), bg)

    d = ImageDraw.Draw(im)

    # ---------- FONT RESOLUTION ----------
    reg_path, bold_path = _resolve_font_paths(font_regular_path, font_bold_path, font_family)
    if debug_print:
        print(f"[content_card] Regular font: {reg_path}")
        print(f"[content_card] Bold font   : {bold_path}")

    type_scale = 1
    H1   = _load_font(reg_path if bold_path is None else bold_path, int(1.8  * u * type_scale))
    H2   = _load_font(bold_path or reg_path,                         int(0.75  * u * type_scale))
    H3   = _load_font(bold_path or reg_path,                         int(1.0 * u * type_scale))
    Body = _load_font(reg_path,                                      int(1.0 * u * type_scale))
    Small= _load_font(reg_path,                                      int(0.95 * u * type_scale))

    # ---------- LAYOUT ----------
    pad = int(1.0 * u) if variant != "square" else int(1.2 * u)
    x, y = pad, pad

    # Brand row
    d.text((x, y), brand_name, font=H2, fill=fg)
    y += int(1.6 * u)

    # Heading
    d.text((x, y), heading, font=H1, fill=fg)
    y += int(2.8 * u)

    # Meta: 2 columns
    meta_left_x, meta_right_x = x, W // 2 + int(1.4 * u)
    meta_y_l, meta_y_r = y, y
    left_lines = [("Site", site_name), ("Order ID", order_id), ("Items", items_count_text)]
    right_lines= [("Order Date", delivery_text), ("Payment", payment_text)]
    row_gap, label_gap = int(2.8 * u), int(1.2 * u)

    for label, value in left_lines:
        d.text((meta_left_x, meta_y_l), label, font=Small, fill=muted)
        d.text((meta_left_x, meta_y_l + label_gap), value, font=Body, fill=fg)
        meta_y_l += row_gap
    for label, value in right_lines:
        d.text((meta_right_x, meta_y_r), label, font=Small, fill=muted)
        d.text((meta_right_x, meta_y_r + label_gap), value, font=Body, fill=fg)
        meta_y_r += row_gap

    y = max(meta_y_l, meta_y_r) + int(0.8 * u)

    # Divider
    d.line([(pad, y), (W - pad, y)], fill=rule, width=max(2, int(0.06 * u)))
    y += int(0.7 * u)

    # Items header
    d.text((x, y), "Items in this order", font=Small, fill=muted)
    y += int(1.8 * u)

    # Items table
    row_h = int(1.7 * u)
    max_name_w = W - pad * 2 - int(6.0 * u)
    normalized = [{"name": str(it.get("name") or ""), "qty": str(it.get("qty") or "")} for it in items]
    print("content Card::: Generate Review order Card::: normalized", normalized)
    for i, it in enumerate(normalized):
        print("content Card::: Generate Review order Card::: item name", it["name"])
        row_y = y + i * row_h
        if i % 2 == 0:
            d.rectangle([pad, row_y - int(0.2 * u), W - pad, row_y + row_h - int(0.6 * u)], fill=zebra)
        name = _truncate_to_width(d, it["name"], Body, max_name_w) 
        qty  = it["qty"]
        d.text((x, row_y), name, font=Body, fill=fg)
        q_w = d.textlength(qty, font=Body)
        d.text((W - pad - q_w, row_y), qty, font=Body, fill=fg)

    y = y + len(normalized) * row_h + int(0.8 * u)

    # Note for unclear items
    note = "* Some items are unclear\nPlease review before confirming"

    # Get bounding box of the multiline text
    bbox = d.multiline_textbbox((0, 0), note, font=Small, spacing=int(0.4 * u))
    note_w = bbox[2] - bbox[0]
    note_h = bbox[3] - bbox[1]

    # Center horizontally
    d.multiline_text(
        ((W - note_w) // 2, y),
        note,
        font=Small,
        fill=muted,   # muted red
        align="center",
        spacing=int(0.4 * u)
    )

    y += note_h + int(1.2 * u)
    # Footer
    fw = d.textlength(footer_hint, font=Small)
    fh = Small.getbbox(footer_hint)[3]
    d.text(((W - fw)//2, H - pad - fh), footer_hint, font=Small, fill=muted)

    # Save
    out_path = os.path.join(out_dir, _make_filename(heading, ext=".png", suffix=variant))
    im.save(out_path, format="PNG", optimize=True)
    return os.path.abspath(out_path)

# ---------- Font helpers ----------
def _resolve_font_paths(font_regular_path: Optional[str], font_bold_path: Optional[str], font_family: Optional[str]):
    # If explicit paths are valid, prefer them
    reg = _expand_if_dir(font_regular_path, prefer=("Regular","Book","Normal","Medium"))
    bold = _expand_if_dir(font_bold_path, prefer=("Bold","Semibold","SemiBold","DemiBold","Medium"))
    if reg or bold:
        return reg or bold, bold or reg

    # If family provided, search common OS dirs
    if font_family:
        cand = _search_system_fonts(font_family, prefer=("Regular","Book","Normal","Medium"))
        cand_b = _search_system_fonts(font_family, prefer=("Bold","Semibold","SemiBold","DemiBold","Medium"))
        if cand or cand_b:
            return cand or cand_b, cand_b or cand

    # Fallback to DejaVu
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "DejaVuSans.ttf"):
        if os.path.exists(p):
            return p, None
    return None, None

def _expand_if_dir(path: Optional[str], prefer=("Regular",)):
    if not path:
        return None
    # Raw filename
    if os.path.isfile(path):
        return path
    # Directory: search inside
    if os.path.isdir(path):
        files = glob.glob(os.path.join(path, "**", "*.[to]tf"), recursive=True)
        files = _rank_by_preference(files, prefer)
        return files[0] if files else None
    # If a pattern was passed
    if any(ch in path for ch in "*?"):
        files = glob.glob(path, recursive=True)
        files = _rank_by_preference(files, prefer)
        return files[0] if files else (path if os.path.isfile(path) else None)
    # Otherwise not found
    return None

def _search_system_fonts(family: str, prefer=("Regular",)):
    roots = []
    # Windows
    win = os.environ.get("WINDIR")
    if win:
        roots.append(os.path.join(win, "Fonts"))
    # macOS
    roots += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    # Linux
    roots += ["/usr/share/fonts", os.path.expanduser("~/.fonts")]
    pattern = f"*{family}*.*[to]tf"
    matches = []
    for root in roots:
        matches += glob.glob(os.path.join(root, "**", pattern), recursive=True)
    matches = _rank_by_preference(matches, prefer)
    return matches[0] if matches else None

def _rank_by_preference(paths, prefer):
    # Score by preferred weight keywords then shortest path (less random variants)
    def score(p):
        name = os.path.basename(p).lower()
        s = 0
        for i, key in enumerate(prefer[::-1]):
            if key.lower() in name:
                s += (i+1)*10
        if name.endswith(".ttf") or name.endswith(".otf"):
            s += 1
        return -s, len(name)
    return sorted(paths, key=score)

def _load_font(path: Optional[str], size: int):
    try:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception as e:
        print(f"[content_card] Failed to load font at {path}: {e}", file=sys.stderr)

    # Strong fallback
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # Linux
        "/System/Library/Fonts/Supplemental/Arial.ttf",      # macOS
        "C:/Windows/Fonts/arial.ttf"                         # Windows
    ]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)

    # Last-resort
    return ImageFont.load_default()

# ---------- Misc helpers ----------
def _truncate_to_width(draw, text, font, max_w):
    t = text
    while draw.textlength(t, font=font) > max_w and len(t) > 3:
        t = t[:-2]
    return t if t == text else (t + "…")

def _make_filename(seed: str, ext: str = ".png", suffix: str = "") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    slug = _slugify(seed)[:40] or "banner"
    suf = f"-{suffix}" if suffix else ""
    return f"{slug}{suf}-{ts}{ext}"

def _slugify(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "x"

def _rounded_rect(draw, x1, y1, x2, y2, radius, fill):
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill)
