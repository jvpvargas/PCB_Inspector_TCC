"""
PCB Inspector - compares live-camera PCB captures against a Golden Board
reference using OpenCV and highlights the differences.

Workflow:
    1. Click "Select Golden Board" and choose the reference PCB image (via
       camera capture, an image file, or a previously saved board setup).
       It is displayed on the left panel.
    2. (Optional but recommended) Click "Setup ROIs" to draw and name
       Regions of Interest (e.g. individual components) on the golden
       board. Each region is then evaluated as a single OK/NOK unit,
       so one misplaced/flipped component reports as one named defect
       instead of several unrelated diff blobs.
    3. As soon as the golden board is loaded, the live camera feed starts
       automatically in the right panel. Press Space or the Capture button
       to freeze a frame and compare it against the golden board. The
       result (OK/NOK boxes, or a highlighted whole-board diff if no ROIs
       were defined) is shown for a few seconds before the feed resumes
       automatically for the next part.
"""

import csv
import datetime
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# Must be set before cv2 is imported. With Media Foundation's hardware
# transforms on (the default), opening the camera and setting its resolution
# took ~17 s on the inspection camera (6 s to open + 5.5 s per size setting);
# with them off it takes ~0.8 s, at the same resolution and frame rate.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2  # noqa: E402
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

DISPLAY_MAX_W = 480
DISPLAY_MAX_H = 520
HISTORY_PANEL_W = 380  # px reserved for the Check History table; the image panels share the rest
PANEL_BG = "#1e1e1e"

MIN_DEFECT_AREA = 60  # pixels, filters out noise-sized contours

# Pixel-based settings below (sizes, distances, areas) were tuned on 640 px wide
# images. Distances on the board (search ranges, region context, defect sizes)
# are scaled by golden width / REFERENCE_WIDTH, so they mean the same physical
# size at any camera resolution; per-pixel noise filters (blur) are not.
REFERENCE_WIDTH = 640
ALIGN_MAX_WIDTH = 960  # global alignment (ORB) runs on a copy at most this wide, for speed

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
ROIS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rois")
GOLDEN_BOARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Golden Boards Images")

CAMERA_INDEX = 1  # external USB camera (0 is usually the PC's built-in webcam)
# Requested capture format. Measured on the inspection camera: 1920x1080 MJPG
# runs at ~27 fps through Media Foundation (MSMF); through DirectShow every mode
# above 640x480 dropped to ~1 fps. Without a request the driver default is 640x480.
CAMERA_BACKEND = cv2.CAP_MSMF
CAMERA_FOURCC = "MJPG"
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
CAMERA_POLL_MS = 33  # ~30 fps live preview
RESULT_DISPLAY_MS = 2000  # how long the OK/NOK result stays frozen on screen (was 3000, then 2250)

# Placement check (see check_placement): a capture that fails it is discarded,
# not inspected. Measured on 193 valid captures (2026-10-14): >= 415 matched
# points, scale 0.96-1.13, every region inside the frame. Absent board /
# covered camera: 0-1 points; half the board out of frame: 59-86% of the
# region area inside. Rotation is NOT a reason to discard: the alignment
# compensates it (boards rotated 13 and 180 degrees were inspected correctly).
PLACEMENT_MIN_INLIERS = 100  # feature matches consistent with the board's position
PLACEMENT_SCALE_RANGE = (0.8, 1.25)  # board size relative to the golden

ROI_DIFF_RATIO_THRESHOLD = 0.30  # "component" regions: fraction of pixels that must differ to flag NOK (see note below)
# Set on the 40-board log of 2026-10-14 (1080p): missing parts scored >= 0.369, present parts <= 0.276
# (an R6 with solder residue from reworking its neighbours). A false OK costs far more than a false NOK,
# so the threshold sits closer to the present parts than the midpoint: 0.069 below the lowest missing part.
ROI_MIN_SIZE = 5  # minimum width/height (original-image pixels) for a drawn region to be kept
ROI_LOCAL_SEARCH_MARGIN = 10  # px search radius for per-region micro-alignment (no fixture/jig)
ROI_ANCHOR_SEARCH = 4  # px each region may deviate from the board-wide median offset (see evaluate_rois)
ROI_CONTEXT = 8  # px of surrounding board included around each region for matching and blurring
ROI_BLUR_KERNEL = (5, 5)  # per-region smoothing (smaller than the whole-board one: regions can be ~10 px)
ROI_REFINE_SHIFT = 2  # px: the region is also compared shifted as a whole this far (placement/parallax)

# Inspection type of each region (chosen in Setup ROIs; stored as roi["kind"]).
ROI_KINDS = {
    "component": "Component (color)",  # SMD parts, ICs, LEDs: compared pixel by pixel in color
    "hole": "Through-hole terminal",  # pad with a hole: checks the hole is still open
    "metal": "Metal part (shape)",  # shiny parts (USB connector): compares edges, not brightness
}
DEFAULT_ROI_KIND = "component"
HOLE_CHROMA_THRESHOLD = 10  # Lab a/b distance (shadow-tolerant) of the hole's inside from the golden's to flag it closed
METAL_EDGE_RECALL_MIN = 0.80  # fraction of the golden's strong edges that must be found on the test
METAL_EDGE_RADIUS = 2  # px an edge may be off and still count as found
METAL_EDGE_ANGLE_TOL = 25  # degrees an edge's direction may differ and still count as found

DIFF_BLUR_KERNEL = (7, 7)  # smooths minor jitter/noise before comparing
DIFF_COLOR_THRESHOLD = 36  # Lab color-distance to call a pixel "different"; higher = more tolerant

ROI_SETUP_MAX_W = 900
ROI_SETUP_MAX_H = 700

ZOOM_LEVELS = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]  # multipliers applied on top of the fit-to-window scale
CROSSHAIR_COLOR = "#ff00ff"
ROI_LABEL_FONT_SCALE = 0.36  # OK/NOK label size on result images, at REFERENCE_WIDTH (scaled with the width)
ROI_COLOR = "#00e5ff"
ROI_SELECTED_COLOR = "#a020f0"  # purple: the region selected in the list, which can be moved/resized
ROI_HANDLE_SIZE = 7  # px (screen) of the resize handles drawn on the selected region
ROI_GRAB_TOLERANCE = 6  # px (screen) around the selected region's border that grabs it for resizing

# Mouse cursor shown over each resize handle of the selected region (Windows Tk names).
ROI_RESIZE_CURSORS = {
    "n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
    "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
}

LED_GREEN_ON = "#2ecc40"
LED_GREEN_OFF = "#1c3a1c"
LED_RED_ON = "#ff2d2d"
LED_RED_OFF = "#3a1c1c"
LED_YELLOW_ON = "#f4d035"
LED_YELLOW_OFF = "#4a4326"


def _scale_of(image):
    """How many pixels of this image correspond to one pixel of a
    REFERENCE_WIDTH-wide image of the same view."""
    return image.shape[1] / REFERENCE_WIDTH


def _px(value, scale):
    """A pixel distance tuned at REFERENCE_WIDTH, converted to this scale."""
    return max(1, int(round(value * scale)))


def _odd(value, scale):
    """Like _px, rounded up to an odd number (for kernel sizes)."""
    k = _px(value, scale)
    return k if k % 2 else k + 1


def open_camera():
    """Open the inspection camera in the requested format (see CAMERA_*).
    Returns the cv2.VideoCapture; check isOpened(). The driver may deliver a
    different size than requested - read it from the frames."""
    cap = cv2.VideoCapture(CAMERA_INDEX, CAMERA_BACKEND)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    return cap


def align_image_info(reference_bgr, moving_bgr):
    """Align moving_bgr onto reference_bgr using ORB feature matching + homography.

    Returns (aligned_image, valid_mask) where aligned_image is the warped
    moving image (same size as reference) and valid_mask is a uint8 mask
    (255 = real pixel data, 0 = area outside the warped source image, e.g.
    corners exposed by rotation) so callers can ignore warp artifacts.
    If not enough matches are found for a reliable homography, the moving
    image is simply resized and the whole area is considered valid.

    Features are detected on copies at most ALIGN_MAX_WIDTH wide (high
    resolution adds time, not alignment accuracy that the per-region
    matching doesn't already provide); the homography is then converted back
    to full resolution.

    Also returns info = {"inliers": number of matched points consistent
    with the homography (0 if none was found), "homography": the 3x3 matrix
    from moving to reference pixels, or None}, used by check_placement.
    """
    h, w = reference_bgr.shape[:2]
    full_valid = np.full((h, w), 255, dtype=np.uint8)

    ref_f = min(1.0, ALIGN_MAX_WIDTH / w)
    mov_f = min(1.0, ALIGN_MAX_WIDTH / moving_bgr.shape[1])
    ref_gray = cv2.cvtColor(
        cv2.resize(reference_bgr, None, fx=ref_f, fy=ref_f, interpolation=cv2.INTER_AREA)
        if ref_f < 1 else reference_bgr, cv2.COLOR_BGR2GRAY)
    mov_gray = cv2.cvtColor(
        cv2.resize(moving_bgr, None, fx=mov_f, fy=mov_f, interpolation=cv2.INTER_AREA)
        if mov_f < 1 else moving_bgr, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=5000)
    kp_ref, des_ref = orb.detectAndCompute(ref_gray, None)
    kp_mov, des_mov = orb.detectAndCompute(mov_gray, None)

    if des_ref is None or des_mov is None or len(kp_ref) < 4 or len(kp_mov) < 4:
        return cv2.resize(moving_bgr, (w, h)), full_valid, {"inliers": 0, "homography": None}

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = matcher.knnMatch(des_mov, des_ref, k=2)

    good = []
    for m_n in matches:
        if len(m_n) != 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:
            good.append(m)

    if len(good) < 10:
        return cv2.resize(moving_bgr, (w, h)), full_valid, {"inliers": 0, "homography": None}

    src_pts = np.float32([kp_mov[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp_ref[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if homography is None:
        return cv2.resize(moving_bgr, (w, h)), full_valid, {"inliers": 0, "homography": None}
    # Back to full resolution: full moving -> small moving -> small ref -> full ref.
    homography = np.diag([1 / ref_f, 1 / ref_f, 1.0]) @ homography @ np.diag([mov_f, mov_f, 1.0])

    aligned = cv2.warpPerspective(moving_bgr, homography, (w, h))

    mov_h, mov_w = moving_bgr.shape[:2]
    source_canvas = np.full((mov_h, mov_w), 255, dtype=np.uint8)
    valid_mask = cv2.warpPerspective(source_canvas, homography, (w, h))
    k = _odd(9, _scale_of(reference_bgr))
    erode_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    valid_mask = cv2.erode(valid_mask, erode_kernel, iterations=2)

    return aligned, valid_mask, {"inliers": int(mask.sum()), "homography": homography}


def align_image(reference_bgr, moving_bgr):
    """align_image_info without the quality info: (aligned_image, valid_mask)."""
    aligned, valid_mask, _info = align_image_info(reference_bgr, moving_bgr)
    return aligned, valid_mask


def check_placement(reference_bgr, moving_shape, info, rois):
    """Is the board in a condition to be inspected? Returns None if so, or
    the message to show when the capture must be discarded instead of being
    reported as a board with every component missing:
      - "Placa Ausente": too few points in common with the golden (no board,
        another object, camera covered);
      - "Placa Fora de Posição": some region of interest outside the camera
        image, or the board at a very different distance than the golden.
    A rotated board is not discarded: the alignment compensates rotation.
    Uses only what the alignment already computed (cheap)."""
    H = info.get("homography")
    if H is None or info.get("inliers", 0) < PLACEMENT_MIN_INLIERS:
        return "Placa Ausente"
    Hn = H / H[2, 2]
    scale = float(np.sqrt(abs(np.linalg.det(Hn[:2, :2]))))
    if not PLACEMENT_SCALE_RANGE[0] <= scale <= PLACEMENT_SCALE_RANGE[1]:
        return "Placa Fora de Posição"
    if rois:
        # Every region must be inside the captured frame: map the corners
        # back from golden to camera pixels.
        mh, mw = moving_shape[:2]
        corners = np.float32([
            [x, y] for r in rois
            for x, y in ((r["x"], r["y"]), (r["x"] + r["w"], r["y"]),
                         (r["x"] + r["w"], r["y"] + r["h"]), (r["x"], r["y"] + r["h"]))
        ]).reshape(-1, 1, 2)
        cam = cv2.perspectiveTransform(corners, np.linalg.inv(Hn)).reshape(-1, 2)
        if (cam < 0).any() or (cam[:, 0] > mw - 1).any() or (cam[:, 1] > mh - 1).any():
            return "Placa Fora de Posição"
    return None


def draw_placement_box(frame_bgr, golden_shape, rois, message):
    """The captured frame with a yellow box where the board should be (the
    area of the regions of interest on the golden, or most of the image if
    there are none) and the message as its label, in the same style as the
    OK/NOK boxes. Drawn with PIL so the accented text renders."""
    result = frame_bgr.copy()
    h, w = result.shape[:2]
    scale = _scale_of(result)
    gh, gw = golden_shape[:2]
    if rois:
        sx, sy = w / gw, h / gh  # golden -> frame pixels (same camera, same framing)
        margin = _px(8, scale)
        x0 = int(min(r["x"] for r in rois) * sx) - margin
        y0 = int(min(r["y"] for r in rois) * sy) - margin
        x1 = int(max(r["x"] + r["w"] for r in rois) * sx) + margin
        y1 = int(max(r["y"] + r["h"] for r in rois) * sy) + margin
    else:
        x0, y0, x1, y1 = w // 10, h // 10, w * 9 // 10, h * 9 // 10
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1)
    yellow = (0, 210, 255)  # BGR
    cv2.rectangle(result, (x0, y0), (x1, y1), yellow, _px(3, scale))

    pil = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    size = max(12, int(round(ROI_LABEL_FONT_SCALE * scale * 30)))
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    pad = _px(3, scale)
    left, top, right, bottom = draw.textbbox((0, 0), message, font=font)
    lw, lh = right - left + 2 * pad, bottom - top + 2 * pad
    lx = min(max(x0, 0), max(w - lw, 0))
    ly = y0 - lh if y0 - lh >= 0 else y0  # above the box, or inside its top
    draw.rectangle([lx, ly, lx + lw, ly + lh], fill=(255, 210, 0))
    draw.text((lx + pad - left, ly + pad - top), message, font=font, fill=(0, 0, 0))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def compute_diff_mask(reference_bgr, aligned_bgr, valid_mask=None):
    """Return a binary mask of differences between the two (same-size) images.

    Comparison is done in Lab color space (Euclidean distance across all
    three channels) rather than grayscale, so color-only defects (e.g. a
    wrong-color component or solder splash) are detected even when their
    luminance is close to the reference.

    valid_mask, if given, restricts the comparison to the region actually
    covered by warped source data (excludes corners/edges exposed by
    rotation/perspective correction during alignment).
    """
    ref_blur = cv2.GaussianBlur(reference_bgr, DIFF_BLUR_KERNEL, 0)
    test_blur = cv2.GaussianBlur(aligned_bgr, DIFF_BLUR_KERNEL, 0)

    ref_lab = cv2.cvtColor(ref_blur, cv2.COLOR_BGR2LAB).astype(np.float32)
    test_lab = cv2.cvtColor(test_blur, cv2.COLOR_BGR2LAB).astype(np.float32)

    dist = np.linalg.norm(ref_lab - test_lab, axis=2)
    dist = np.clip(dist, 0, 255).astype(np.uint8)

    _, mask = cv2.threshold(dist, DIFF_COLOR_THRESHOLD, 255, cv2.THRESH_BINARY)

    scale = _scale_of(reference_bgr)
    k = _odd(5, scale)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=2)

    # Ignore differences right at the image border (alignment artifacts).
    border = _px(4, scale)
    mask[:border, :] = 0
    mask[-border:, :] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0

    if valid_mask is not None:
        mask = cv2.bitwise_and(mask, valid_mask)

    return mask


def highlight_differences(aligned_bgr, mask):
    """Draw bounding boxes / outlines around defect regions on a copy of aligned_bgr."""
    result = aligned_bgr.copy()
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    scale = _scale_of(aligned_bgr)
    defect_count = 0
    for c in contours:
        if cv2.contourArea(c) < MIN_DEFECT_AREA * scale * scale:
            continue
        defect_count += 1
        x, y, w, h = cv2.boundingRect(c)
        pad = _px(4, scale)
        x0, y0 = max(x - pad, 0), max(y - pad, 0)
        x1, y1 = min(x + w + pad, result.shape[1]), min(y + h + pad, result.shape[0])
        cv2.rectangle(result, (x0, y0), (x1, y1), (0, 0, 255), 2)

    return result, defect_count


def _find_local_offset(ref_patch, aligned_bgr, x0, y0, x1, y1, margin=ROI_LOCAL_SEARCH_MARGIN):
    """Without a mechanical fixture, the board is never placed in exactly the
    same spot twice, and a single global homography rarely lines up every
    small component perfectly. Search a small neighborhood around the
    expected location (x0, y0)-(x1, y1) of ref_patch in aligned_bgr for the
    best-matching window and return its (dx, dy) offset.

    ref_patch should include some board around the component (ROI_CONTEXT):
    pads and silkscreen anchor the match, so it still lands in the right
    place when the component itself is missing. Matching is done in color
    (Lab), where a black resistor body and the blue board differ clearly -
    in grayscale they are close. Returns (0, 0) if there isn't enough room
    to search or no confident match is found.
    """
    h, w = aligned_bgr.shape[:2]
    rh, rw = ref_patch.shape[:2]

    if rw < 6 or rh < 6:
        return 0, 0

    sx0, sy0 = max(0, x0 - margin), max(0, y0 - margin)
    sx1, sy1 = min(w, x1 + margin), min(h, y1 + margin)
    search_region = aligned_bgr[sy0:sy1, sx0:sx1]

    if search_region.shape[0] < rh or search_region.shape[1] < rw:
        return 0, 0

    ref_lab = cv2.cvtColor(ref_patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    search_lab = cv2.cvtColor(search_region, cv2.COLOR_BGR2LAB).astype(np.float32)

    result = cv2.matchTemplate(search_lab, ref_lab, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < 0.3:
        # No confident local match - keep the original, globally-aligned
        # position rather than drifting toward an unrelated area of the board.
        return 0, 0

    return sx0 + max_loc[0] - x0, sy0 + max_loc[1] - y0


def _roi_lab(image_bgr):
    """Blurred Lab version of a whole image, for the "component" comparison.
    The blur filters per-pixel sensor noise, so it is NOT scaled with the
    resolution: finer images keep their extra detail."""
    return cv2.cvtColor(
        cv2.GaussianBlur(image_bgr, ROI_BLUR_KERNEL, 0), cv2.COLOR_BGR2LAB
    ).astype(np.float32)


def find_hole(roi_bgr):
    """Locate the round hole of a through-hole terminal in a (golden) region
    crop. Returns (cx, cy, radius) in crop pixels, or None if no circle is
    found."""
    lightness = cv2.GaussianBlur(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)[..., 0], (0, 0), 1.0)
    h, w = lightness.shape
    circles = cv2.HoughCircles(
        lightness, cv2.HOUGH_GRADIENT, dp=1, minDist=w, param1=80, param2=10,
        minRadius=int(min(w, h) * 0.2), maxRadius=int(min(w, h) * 0.5),
    )
    if circles is None:
        return None
    cx, cy, r = circles[0][0]
    return float(cx), float(cy), float(r)


def _hole_color(patch_bgr, cx, cy, r):
    """Median Lab color (L, a, b - with a/b centered on neutral) of the
    inside of the hole (disk of 0.6 r)."""
    lab = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    yy, xx = np.mgrid[:lab.shape[0], :lab.shape[1]]
    inside = np.hypot(xx - cx, yy - cy) <= 0.6 * r
    return np.median(lab[inside], axis=0) - (0.0, 128.0, 128.0)


def _hole_color_distance(golden, test):
    """How far the test hole's color (a/b) is from the golden's, allowing for
    shadow. Seen darker, the same surface keeps its hue but loses a/b in
    proportion - e.g. the tray under a board that sits a little higher. So
    when the test hole is darker than the golden's, its a/b may lie anywhere
    between the golden's and the golden's scaled down by the lightness ratio;
    the distance is taken to that segment. A hole that is as bright or
    brighter gets no such allowance: solder is bright and neutral."""
    g_ab, t_ab = golden[1:], test[1:]
    k_min = float(np.clip(test[0] / max(golden[0], 1.0), 0.0, 1.0))
    norm2 = float(g_ab @ g_ab)
    k = 1.0 if norm2 == 0 else float(np.clip((t_ab @ g_ab) / norm2, k_min, 1.0))
    return float(np.linalg.norm(t_ab - k * g_ab))


def _edges(patch_bgr):
    """Gradient magnitude and (polarity-free) direction of the lightness."""
    lightness = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)
    lightness = cv2.GaussianBlur(lightness, (0, 0), 1.0)
    gx = cv2.Sobel(lightness, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lightness, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy), np.mod(np.arctan2(gy, gx), np.pi)


def _edge_recall(ref_patch, test_patch, box):
    """Fraction of the golden's strongest edges inside box (the top 20%,
    and at least a clear step) that the test also has, within
    METAL_EDGE_RADIUS px and METAL_EDGE_ANGLE_TOL degrees. Dark-to-bright
    and bright-to-dark count the same, so a reflection that flips or
    changes brightness doesn't matter; a missing/covered part does."""
    ix, iy, w, h = box
    ref_mag, ref_ang = _edges(ref_patch)
    test_mag, test_ang = _edges(test_patch)
    ref_mag, ref_ang = ref_mag[iy:iy + h, ix:ix + w], ref_ang[iy:iy + h, ix:ix + w]
    strong = ref_mag >= max(np.percentile(ref_mag, 80), 40.0)
    if not strong.any():
        return 1.0
    angle_tol = np.deg2rad(METAL_EDGE_ANGLE_TOL)
    found = np.zeros_like(strong)
    rad = METAL_EDGE_RADIUS
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            y0, x0 = iy + dy, ix + dx
            if y0 < 0 or x0 < 0 or y0 + h > test_mag.shape[0] or x0 + w > test_mag.shape[1]:
                continue
            mag = test_mag[y0:y0 + h, x0:x0 + w]
            diff = np.abs(test_ang[y0:y0 + h, x0:x0 + w] - ref_ang)
            diff = np.minimum(diff, np.pi - diff)
            found |= (mag >= 0.35 * ref_mag) & (diff <= angle_tol)
    return float((found & strong).sum() / strong.sum())


def evaluate_rois(reference_bgr, aligned_bgr, valid_mask, rois):
    """Evaluate each named region of interest as a single OK/NOK unit.

    Comparing per-region (rather than the whole board at once) avoids a
    single misplaced/flipped component being reported as multiple unrelated
    diff blobs: all the pixel differences that fall inside one component's
    region collapse into one pass/fail result for that component.

    Placement: each region is compared together with ROI_CONTEXT px of
    surrounding board (pads, silkscreen), which is matched on the test image
    to absorb the drift left by the global alignment (no fixture/jig). The
    drift is nearly the same all over the board, so the median of all the
    regions' drifts is taken, and each region may only deviate
    ROI_ANCHOR_SEARCH px from it - small regions next to repeating
    silkscreen could otherwise lock onto the wrong spot. Then, since each
    physical board places its parts slightly differently (and tall parts
    show parallax), the region itself is also tried shifted as a whole by up
    to ROI_REFINE_SHIFT px, keeping the best result. A rigid shift can't
    make a missing part look present. These distances are scaled with the
    image resolution (see REFERENCE_WIDTH).

    The check depends on the region's kind (roi["kind"], see ROI_KINDS):
      component: fraction of pixels differing in color > ROI_DIFF_RATIO_THRESHOLD,
        at full resolution.
      hole: the inside of the terminal's hole (found on the golden) changed
        color (a/b, see _hole_color_distance) by more than
        HOLE_CHROMA_THRESHOLD - an open hole shows the tray behind the board,
        bright or in shadow; solder is bright and neutral.
      metal: fewer than METAL_EDGE_RECALL_MIN of the golden's edges are found -
        reflections change brightness, not shape.
    "hole" and "metal" are shape/color checks tuned at REFERENCE_WIDTH and
    gain nothing from more pixels, so they run on copies scaled to it.
    Pixels outside the warped camera image (valid_mask == 0) are black and
    therefore count as different.

    Returns a list of dicts: {name, kind, status, score, bbox}; score is the
    differing-pixel ratio, hole color distance or edge recall, and bbox is
    where the region was found on the test image.
    """
    h, w = reference_bgr.shape[:2]
    scale = _scale_of(reference_bgr)
    context = _px(ROI_CONTEXT, scale)
    refine = _px(ROI_REFINE_SHIFT, scale)

    boxes = []  # (roi, x0, y0, x1, y1, cx0, cy0, cx1, cy1) for the regions inside the image
    for roi in rois:
        x0, y0 = max(0, roi["x"]), max(0, roi["y"])
        x1, y1 = min(w, roi["x"] + roi["w"]), min(h, roi["y"] + roi["h"])
        if x1 <= x0 or y1 <= y0:
            continue
        cx0, cy0 = max(0, x0 - context), max(0, y0 - context)
        cx1, cy1 = min(w, x1 + context), min(h, y1 + context)
        boxes.append((roi, x0, y0, x1, y1, cx0, cy0, cx1, cy1))

    offsets = [
        _find_local_offset(
            reference_bgr[cy0:cy1, cx0:cx1], aligned_bgr, cx0, cy0, cx1, cy1,
            margin=_px(ROI_LOCAL_SEARCH_MARGIN, scale),
        )
        for _roi, _x0, _y0, _x1, _y1, cx0, cy0, cx1, cy1 in boxes
    ]
    if len(offsets) >= 3:
        mdx, mdy = (int(v) for v in np.round(np.median(np.array(offsets), axis=0)))
        offsets = []
        for _roi, _x0, _y0, _x1, _y1, cx0, cy0, cx1, cy1 in boxes:
            dx, dy = _find_local_offset(
                reference_bgr[cy0:cy1, cx0:cx1], aligned_bgr,
                cx0 + mdx, cy0 + mdy, cx1 + mdx, cy1 + mdy,
                margin=_px(ROI_ANCHOR_SEARCH, scale),
            )
            offsets.append((mdx + dx, mdy + dy))

    kinds = [roi.get("kind", DEFAULT_ROI_KIND) for roi, *_ in boxes]
    if DEFAULT_ROI_KIND in kinds:
        ref_lab, test_lab = _roi_lab(reference_bgr), _roi_lab(aligned_bgr)
    if any(k != DEFAULT_ROI_KIND for k in kinds):
        # Copies at REFERENCE_WIDTH for the "hole"/"metal" checks.
        sw, sh = REFERENCE_WIDTH, max(1, int(round(h / scale)))
        ref_small = reference_bgr if scale == 1 else cv2.resize(reference_bgr, (sw, sh), interpolation=cv2.INTER_AREA)
        test_small = aligned_bgr if scale == 1 else cv2.resize(aligned_bgr, (sw, sh), interpolation=cv2.INTER_AREA)

    results = []
    for (roi, x0, y0, x1, y1, cx0, cy0, cx1, cy1), (dx, dy), kind in zip(boxes, offsets, kinds):
        if kind == DEFAULT_ROI_KIND:
            score, fx, fy = _component_score(
                ref_lab, test_lab, (x0, y0, x1, y1), (cx0, cy0, cx1, cy1), dx, dy, refine)
            status = "NOK" if score > ROI_DIFF_RATIO_THRESHOLD else "OK"
        else:
            # Same region, offset and context at REFERENCE_WIDTH.
            sbox = tuple(int(round(v / scale)) for v in (x0, y0, x1, y1))
            score, fx, fy = _shape_score(
                kind, ref_small, test_small, sbox, int(round(dx / scale)), int(round(dy / scale)))
            fx, fy = int(round(fx * scale)), int(round(fy * scale))
            if kind == "hole":
                # No hole found on the golden: can't verify it, so it doesn't pass.
                status = "NOK" if score is None or score > HOLE_CHROMA_THRESHOLD else "OK"
            else:
                status = "NOK" if score < METAL_EDGE_RECALL_MIN else "OK"

        results.append(
            {
                "name": roi["name"],
                "kind": kind,
                "status": status,
                "score": score,
                "bbox": (fx, fy, x1 - x0, y1 - y0),
            }
        )
    return results


def _component_score(ref_lab, test_lab, box, ctx_box, dx, dy, refine):
    """Lowest fraction of the region's pixels differing in color (Lab
    distance > DIFF_COLOR_THRESHOLD) over rigid shifts of up to +-refine px
    around offset (dx, dy). Returns (ratio, x, y) - x, y being where the
    region was found."""
    h, w = ref_lab.shape[:2]
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = ctx_box
    rw, rh = x1 - x0, y1 - y0
    ref = ref_lab[y0:y1, x0:x1]
    best = None
    for sy in range(-refine, refine + 1):
        for sx in range(-refine, refine + 1):
            # Shift the region with its context patch, keeping that patch inside the image.
            tx = min(max(cx0 + dx + sx, 0), w - (cx1 - cx0)) + (x0 - cx0)
            ty = min(max(cy0 + dy + sy, 0), h - (cy1 - cy0)) + (y0 - cy0)
            dist = np.linalg.norm(ref - test_lab[ty:ty + rh, tx:tx + rw], axis=2)
            ratio = float(np.count_nonzero(dist > DIFF_COLOR_THRESHOLD)) / dist.size
            if best is None or ratio < best[0]:
                best = (ratio, tx, ty)
    return best


def _shape_score(kind, ref_small, test_small, box, dx, dy):
    """"hole"/"metal" check on REFERENCE_WIDTH copies: the hole's color
    distance (lower is better; None if the golden shows no hole) or the edge
    recall (higher is better), best over rigid shifts of +-ROI_REFINE_SHIFT
    px. Returns (score, x, y) in the copies' pixels."""
    h, w = ref_small.shape[:2]
    x0, y0, x1, y1 = box
    cx0, cy0 = max(0, x0 - ROI_CONTEXT), max(0, y0 - ROI_CONTEXT)
    cx1, cy1 = min(w, x1 + ROI_CONTEXT), min(h, y1 + ROI_CONTEXT)
    ref_patch = ref_small[cy0:cy1, cx0:cx1]
    pw, ph = cx1 - cx0, cy1 - cy0
    ix, iy = x0 - cx0, y0 - cy0  # region inside the patch

    hole = None
    if kind == "hole":
        hole = find_hole(ref_small[y0:y1, x0:x1])
        if hole is not None:
            hole = (hole[0] + ix, hole[1] + iy, hole[2])
            golden_color = _hole_color(ref_patch, *hole)

    best = None  # (score, x, y), "best" meaning closest to the golden
    for sy in range(-ROI_REFINE_SHIFT, ROI_REFINE_SHIFT + 1):
        for sx in range(-ROI_REFINE_SHIFT, ROI_REFINE_SHIFT + 1):
            tx0 = min(max(cx0 + dx + sx, 0), w - pw)
            ty0 = min(max(cy0 + dy + sy, 0), h - ph)
            test_patch = test_small[ty0:ty0 + ph, tx0:tx0 + pw]
            if kind == "hole":
                if hole is None:
                    score = None
                else:
                    score = _hole_color_distance(golden_color, _hole_color(test_patch, *hole))
                better = best is None or (score is not None and score < best[0])
            else:
                score = _edge_recall(ref_patch, test_patch, (ix, iy, x1 - x0, y1 - y0))
                better = best is None or score > best[0]
            if better:
                best = (score, tx0 + ix, ty0 + iy)
    return best


def draw_roi_results(aligned_bgr, roi_results):
    """Draw a labeled box around each evaluated region: green if OK, red if NOK.

    Text and lines are sized relative to the image width (ROI_LABEL_FONT_SCALE
    at REFERENCE_WIDTH), so labels stay readable when a high-resolution image
    is shrunk to fit the panel. Each label takes the first free spot around
    its box (above, below, right, left) so labels don't cover each other or
    a NOK box; NOK labels are placed and drawn last, on top."""
    result = aligned_bgr.copy()
    img_h, img_w = result.shape[:2]
    scale = _scale_of(result)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = ROI_LABEL_FONT_SCALE * scale
    text_thickness = max(1, int(round(scale * 0.8)))
    pad = _px(2, scale)

    for r in roi_results:
        x, y, w, h = r["bbox"]
        color = (0, 200, 0) if r["status"] == "OK" else (0, 0, 255)
        cv2.rectangle(result, (x, y), (x + w, y + h), color, _px(2 if r["status"] == "OK" else 3, scale))

    # Rectangles labels should avoid: labels already drawn, and the NOK boxes
    # (a defect must never be hidden behind an OK label).
    placed = [
        (r["bbox"][0], r["bbox"][1], r["bbox"][0] + r["bbox"][2], r["bbox"][1] + r["bbox"][3])
        for r in roi_results if r["status"] == "NOK"
    ]

    def overlap(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))

    for r in sorted(roi_results, key=lambda r: r["status"] == "NOK"):
        x, y, w, h = r["bbox"]
        color = (0, 200, 0) if r["status"] == "OK" else (0, 0, 255)
        label = f"{r['name']}: {r['status']}"
        (text_w, text_h), baseline = cv2.getTextSize(label, font, font_scale, text_thickness)
        lw, lh = text_w + 2 * pad, text_h + baseline + 2 * pad
        candidates = [
            (x, y - lh - 1),  # above, left-aligned
            (x + w - lw, y - lh - 1),  # above, right-aligned
            (x, y + h + 1),  # below
            (x + w + 1, y),  # right
            (x - lw - 1, y),  # left
        ]
        best = None
        for cx, cy in candidates:
            cx = min(max(cx, 0), max(img_w - lw, 0))
            cy = min(max(cy, 0), max(img_h - lh, 0))
            rect = (cx, cy, cx + lw, cy + lh)
            cost = sum(overlap(rect, q) for q in placed)
            if best is None or cost < best[0]:
                best = (cost, rect)
            if cost == 0:
                break
        x0, y0, x1, y1 = best[1]
        placed.append(best[1])
        cv2.rectangle(result, (x0, y0), (x1, y1), color, -1)
        cv2.putText(
            result, label, (x0 + pad, y1 - pad - baseline), font, font_scale,
            (255, 255, 255), text_thickness, cv2.LINE_AA,
        )
    return result


def roi_file_path(golden_path):
    """Path to the saved ROI definitions for a given golden board image, keyed
    by the golden board's filename (without extension)."""
    golden_name = os.path.splitext(os.path.basename(golden_path))[0]
    return os.path.join(ROIS_DIR, f"{golden_name}.json")


def load_rois(golden_path):
    """Load previously saved ROI definitions for this golden board, if any."""
    if not golden_path:
        return []
    path = roi_file_path(golden_path)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("regions", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_rois(golden_path, rois):
    """Persist ROI definitions for this golden board to disk, together with
    the golden board's image path so the whole (image + regions) setup can
    later be reloaded by name via list_saved_boards()."""
    os.makedirs(ROIS_DIR, exist_ok=True)
    path = roi_file_path(golden_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"golden_image_path": golden_path, "regions": rois}, f, indent=2)
    return path


def _resolve_golden_path(image_path):
    """Return the saved golden image path if it exists; otherwise look for a
    file with the same name in GOLDEN_BOARDS_DIR (the ROI files store an
    absolute path, which breaks when the project is copied to another
    folder, computer or operating system). None if neither exists."""
    if not image_path:
        return None
    if os.path.isfile(image_path):
        return image_path
    local = os.path.join(GOLDEN_BOARDS_DIR, image_path.replace("\\", "/").rsplit("/", 1)[-1])
    return local if os.path.isfile(local) else None


def list_saved_boards():
    """List previously saved golden board + ROI combinations, so a board
    setup can be reloaded by name instead of browsing for the image file and
    redefining its regions again. Only boards whose source image still
    exists on disk are listed."""
    boards = []
    if not os.path.isdir(ROIS_DIR):
        return boards

    for filename in sorted(os.listdir(ROIS_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(ROIS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        image_path = _resolve_golden_path(data.get("golden_image_path"))
        if image_path is None:
            continue

        boards.append(
            {
                "name": os.path.splitext(filename)[0],
                "image_path": image_path,
                "region_count": len(data.get("regions", [])),
            }
        )
    return boards


def make_session_folder(golden_path, timestamp=None):
    """Build the path for one testing session's log folder:
    logs/<goldenboardfilename>_<date>_<time>/. Both the CSV log and every
    result image captured while this golden board is active are stored
    inside it, so PCBs checked against different golden boards (or the same
    board on different days) stay in separate folders."""
    golden_name = os.path.splitext(os.path.basename(golden_path))[0]
    ts = timestamp or datetime.datetime.now()
    folder_name = f"{golden_name}_{ts:%Y-%m-%d}_{ts:%H-%M-%S}"
    return os.path.join(LOGS_DIR, folder_name)


def save_result_image(session_folder, date_str, time_str, part_count, status, result_bgr, raw_bgr=None):
    """Save the annotated comparison result (the same image shown in the
    Test Board panel, with OK/NOK region boxes) into the current session's
    log folder, so the check history log has visual evidence for every part
    checked. If raw_bgr (the unannotated camera frame) is given, it is saved
    next to it with a "_raw" suffix, so past captures can be re-inspected
    (e.g. to tune thresholds) without the drawn boxes in the way. Returns the
    annotated image's file path."""
    os.makedirs(session_folder, exist_ok=True)

    safe_time = time_str.replace(":", "-")
    filename = f"{date_str}_{safe_time}_part{part_count:04d}_{status}.png"
    filepath = os.path.join(session_folder, filename)
    cv2.imwrite(filepath, result_bgr)
    if raw_bgr is not None:
        cv2.imwrite(os.path.splitext(filepath)[0] + "_raw.png", raw_bgr)
    return filepath


def save_discarded_capture(session_folder, raw_bgr, message):
    """Keep a discarded capture (board absent / out of position) for
    traceability, in <session>/descartadas/ - outside the inspection log."""
    folder = os.path.join(session_folder, "descartadas")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    kind = "ausente" if "Ausente" in message else "fora_de_posicao"
    path = os.path.join(folder, f"{stamp}_{kind}_raw.png")
    cv2.imwrite(path, raw_bgr)
    return path


def cv2_to_tk(bgr_image, max_w=DISPLAY_MAX_W, max_h=DISPLAY_MAX_H, allow_upscale=False):
    """Convert a BGR OpenCV image to a Tkinter-displayable PhotoImage, scaled to fit."""
    photo, _scale = cv2_to_tk_with_scale(bgr_image, max_w, max_h, allow_upscale)
    return photo


def cv2_to_tk_with_scale(bgr_image, max_w=DISPLAY_MAX_W, max_h=DISPLAY_MAX_H, allow_upscale=False):
    """Like cv2_to_tk, but also returns the scale factor applied (display/original)."""
    h, w = bgr_image.shape[:2]
    scale = min(max_w / w, max_h / h)
    if not allow_upscale:
        scale = min(scale, 1.0)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(bgr_image, (new_w, new_h), interpolation=interpolation)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    return ImageTk.PhotoImage(pil_img), scale


class ChoiceDialog(tk.Toplevel):
    """Simple modal dialog offering a stack of button choices. self.result is
    set to the chosen option's key, or stays None if cancelled/closed."""

    def __init__(self, parent, title, message, options):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None

        ttk.Label(self, text=message, padding=(15, 15, 15, 5), wraplength=320).pack()

        btns = ttk.Frame(self, padding=(15, 5, 15, 15))
        btns.pack(fill=tk.X)
        for key, label in options:
            ttk.Button(btns, text=label, command=lambda k=key: self._choose(k)).pack(
                fill=tk.X, pady=4
            )
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(fill=tk.X, pady=(10, 0))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _choose(self, key):
        self.result = key
        self.destroy()


class SavedBoardPickerDialog(tk.Toplevel):
    """Modal list picker for previously saved Golden Board + ROI setups.
    self.result is set to the chosen board dict, or stays None if
    cancelled/closed."""

    def __init__(self, parent, boards):
        super().__init__(parent)
        self.title("Load Saved Board Setup")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self._boards = boards

        ttk.Label(
            self, text="Select a previously configured golden board:", padding=(15, 15, 15, 5)
        ).pack(anchor="w")

        self.listbox = tk.Listbox(self, width=50, height=min(12, len(boards)) or 1)
        self.listbox.pack(padx=15, pady=5, fill=tk.BOTH, expand=True)
        for b in boards:
            self.listbox.insert(tk.END, f"{b['name']}  ({b['region_count']} region(s))")
        self.listbox.selection_set(0)
        self.listbox.bind("<Double-Button-1>", lambda e: self._choose())

        btn_row = ttk.Frame(self, padding=15)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Load", command=self._choose).pack(side=tk.RIGHT, padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _choose(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.result = self._boards[sel[0]]
        self.destroy()


class CameraCaptureDialog(tk.Toplevel):
    """Live USB camera preview for shooting a new Golden Board reference
    image. Click Capture to grab the current frame; the caller (on_captured)
    is then responsible for naming/saving it."""

    def __init__(self, parent, on_captured):
        super().__init__(parent)
        self.title("Capture Golden Board")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.on_captured = on_captured
        self._last_frame = None
        self._running = False
        self._photo = None
        self._after_id = None

        self.cap = open_camera()
        if not self.cap.isOpened():
            messagebox.showerror(
                "Camera Error",
                "Could not open the USB camera. Check that it is connected "
                "and not already in use by another application.",
            )
            self.destroy()
            return

        self.video_label = tk.Label(self, background="#000")
        self.video_label.pack(padx=10, pady=10)

        btn_row = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Capture", command=self._on_capture).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._running = True
        self._update_frame()

    def _update_frame(self):
        if not self._running:
            return
        ok, frame = self.cap.read()
        if ok:
            self._last_frame = frame
            self._photo = cv2_to_tk(frame, max_w=800, max_h=600)
            self.video_label.configure(image=self._photo)
        self._after_id = self.after(CAMERA_POLL_MS, self._update_frame)

    def _on_capture(self):
        if self._last_frame is None:
            messagebox.showwarning("No frame", "No camera frame available yet.")
            return
        frame = self._last_frame.copy()
        self._stop()
        self.destroy()
        self.on_captured(frame)

    def _on_cancel(self):
        self._stop()
        self.destroy()

    def _stop(self):
        self._running = False
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()


class ROISetupDialog(tk.Toplevel):
    """Modal dialog for drawing and naming Regions of Interest on the golden
    board image. Click-drag-release draws a rectangle, then the user is
    prompted to name it (e.g. "Diode D1", "Capacitor C3"). Selecting an
    existing region in the list highlights it in purple; it can then be
    dragged to a new position, resized by dragging its border/handles (both
    also possible from the keyboard), or deleted.
    Nothing is persisted until "Save & Close".

    A toggleable alignment grid and zoom (with scrollable panning) make it
    practical to place precise regions on dense/complex boards.
    """

    def __init__(self, parent, golden_bgr, golden_path, existing_rois, on_saved):
        super().__init__(parent)
        self.title(f"Setup Regions of Interest - {os.path.basename(golden_path)}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.golden_bgr = golden_bgr
        self.golden_path = golden_path
        self.on_saved = on_saved
        # Work on a deep-enough copy so Cancel leaves the caller's data untouched.
        self.rois = [dict(r) for r in existing_rois]
        self._roi_items = {}  # id(roi) -> (rect_id, text_id)
        self._selected_roi = None  # roi dict selected in the list (drawn purple, movable)
        # While dragging the selected roi: (mode, canvas x, canvas y, original roi box),
        # mode being "move" or the edges being resized, e.g. "e" or "nw".
        self._edit_start = None
        self._handle_ids = []  # resize handles drawn on the selected roi
        self._start_xy = None
        self._temp_rect_id = None
        self._photo = None

        h, w = golden_bgr.shape[:2]
        self.min_size = _px(ROI_MIN_SIZE, _scale_of(golden_bgr))  # in golden pixels
        self.base_scale = min(ROI_SETUP_MAX_W / w, ROI_SETUP_MAX_H / h, 1.0)
        self.zoom_index = 0  # index into ZOOM_LEVELS; 0 = fit-to-window
        self.scale = self.base_scale
        self.show_crosshair = tk.BooleanVar(value=True)
        self._crosshair_h = None
        self._crosshair_v = None

        ttk.Label(
            self,
            text="Click and drag on the image to draw a region, then name it. "
            "Use Zoom for tight/dense areas; the crosshair lines up with your mouse "
            "for precise placement. Select a region in the list to highlight it: drag it "
            "to reposition, or drag its border/handles to resize. Keyboard: arrows move "
            "1 px, Ctrl+arrows resize 1 px (add Shift for 10 px). Delete removes it, Esc "
            "clears the selection. Set each region's Inspection type: through-hole terminals "
            "and shiny metal parts (e.g. the USB connector) need their own type.",
            padding=(10, 10, 10, 0),
            wraplength=ROI_SETUP_MAX_W + 220,
        ).pack(side=tk.TOP, anchor="w")

        toolbar = ttk.Frame(self, padding=(10, 6, 10, 0))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Checkbutton(
            toolbar, text="Show Crosshair", variable=self.show_crosshair, command=self._redraw
        ).pack(side=tk.LEFT)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(toolbar, text="Zoom Out", command=self._zoom_out).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Zoom In", command=self._zoom_in).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(toolbar, text="Reset Zoom", command=self._zoom_reset).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        self.zoom_label_var = tk.StringVar()
        ttk.Label(toolbar, textvariable=self.zoom_label_var).pack(side=tk.LEFT, padx=12)

        body = ttk.Frame(self, padding=10)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        canvas_frame = ttk.Frame(body)
        canvas_frame.pack(side=tk.LEFT)

        self.canvas = tk.Canvas(
            canvas_frame,
            width=ROI_SETUP_MAX_W,
            height=ROI_SETUP_MAX_H,
            highlightthickness=1,
            highlightbackground="#888",
            cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)

        side_panel = ttk.Frame(body, padding=(10, 0, 0, 0))
        side_panel.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(side_panel, text="Regions", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        self.listbox = tk.Listbox(side_panel, width=28, height=22, exportselection=False)
        self.listbox.pack(side=tk.TOP, fill=tk.Y, expand=True, pady=(4, 8))
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # Arrow keys move the selected region, Ctrl+arrows resize it (right and
        # bottom edges), Shift makes either step 10 px. Bound on the listbox too
        # (with "break") so they don't change the list selection instead.
        for key, (dx, dy) in {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}.items():
            for widget in (self, self.listbox):
                for mods, step in (("", 1), ("Shift-", 10)):
                    widget.bind(
                        f"<{mods}{key}>",
                        lambda e, dx=dx * step, dy=dy * step: self._nudge(dx, dy),
                    )
                    widget.bind(
                        f"<Control-{mods}{key}>",
                        lambda e, dx=dx * step, dy=dy * step: self._resize_by(dx, dy),
                    )
        self.bind("<Escape>", lambda e: self._select_roi(None))

        ttk.Label(side_panel, text="Inspection type:").pack(anchor="w")
        self.kind_var = tk.StringVar()
        self.kind_combo = ttk.Combobox(
            side_panel, textvariable=self.kind_var, values=list(ROI_KINDS.values()),
            state=tk.DISABLED,
        )
        self.kind_combo.pack(side=tk.TOP, fill=tk.X, pady=(2, 8))
        self.kind_combo.bind("<<ComboboxSelected>>", self._on_kind_changed)

        ttk.Button(side_panel, text="Delete Selected", command=self._on_delete).pack(
            side=tk.TOP, fill=tk.X
        )

        btn_row = ttk.Frame(self, padding=10)
        btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Save & Close", command=self._on_save).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        for roi in self.rois:
            self.listbox.insert(tk.END, self._list_label(roi))

        self._redraw()

    @staticmethod
    def _list_label(roi):
        kind = roi.get("kind", DEFAULT_ROI_KIND)
        return roi["name"] if kind == DEFAULT_ROI_KIND else f"{roi['name']}  [{kind}]"

    def _on_kind_changed(self, event=None):
        roi = self._selected_roi
        if roi is None:
            return
        label = self.kind_var.get()
        roi["kind"] = next(k for k, v in ROI_KINDS.items() if v == label)
        index = self.rois.index(roi)
        self.listbox.delete(index)
        self.listbox.insert(index, self._list_label(roi))
        self.listbox.selection_set(index)
        if roi["kind"] == "hole":
            crop = self.golden_bgr[roi["y"]:roi["y"] + roi["h"], roi["x"]:roi["x"] + roi["w"]]
            if find_hole(crop) is None:
                messagebox.showwarning(
                    "No hole found",
                    f"No round hole was found inside '{roi['name']}'. Draw the region around "
                    "the whole pad, with the hole inside it; until then this region will "
                    "always be reported NOK.",
                    parent=self,
                )

    def _zoom_in(self):
        if self.zoom_index < len(ZOOM_LEVELS) - 1:
            self.zoom_index += 1
            self._redraw()

    def _zoom_out(self):
        if self.zoom_index > 0:
            self.zoom_index -= 1
            self._redraw()

    def _zoom_reset(self):
        self.zoom_index = 0
        self._redraw()

    def _on_ctrl_wheel(self, event):
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _redraw(self):
        """Re-render the golden board at the current zoom level, then redraw
        every existing region and the crosshair guide on top, all in canvas
        coordinates."""
        self._start_xy = None
        self._temp_rect_id = None
        self._edit_start = None

        self.scale = self.base_scale * ZOOM_LEVELS[self.zoom_index]
        self.zoom_label_var.set(f"Zoom: {int(round(ZOOM_LEVELS[self.zoom_index] * 100))}%")

        h, w = self.golden_bgr.shape[:2]
        img_w, img_h = max(1, int(w * self.scale)), max(1, int(h * self.scale))
        interp = cv2.INTER_AREA if self.scale <= 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(self.golden_bgr, (img_w, img_h), interpolation=interp)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, img_w, img_h))

        self._roi_items = {}
        for roi in self.rois:
            self._draw_roi(roi)

        self._crosshair_h = None
        self._crosshair_v = None
        if self.show_crosshair.get():
            self._crosshair_h = self.canvas.create_line(
                0, 0, img_w, 0, fill=CROSSHAIR_COLOR, width=1, dash=(4, 2)
            )
            self._crosshair_v = self.canvas.create_line(
                0, 0, 0, img_h, fill=CROSSHAIR_COLOR, width=1, dash=(4, 2)
            )

    def _update_crosshair(self, cx, cy):
        if self._crosshair_h is None or self._crosshair_v is None:
            return
        h, w = self.golden_bgr.shape[:2]
        img_w, img_h = w * self.scale, h * self.scale
        self.canvas.coords(self._crosshair_h, 0, cy, img_w, cy)
        self.canvas.coords(self._crosshair_v, cx, 0, cx, img_h)

    def _on_motion(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self._update_crosshair(cx, cy)
        mode = self._grab_mode(cx, cy)
        if mode is None:
            cursor = "crosshair"
        elif mode == "move":
            cursor = "fleur"
        else:
            cursor = ROI_RESIZE_CURSORS[mode]
        self.canvas.configure(cursor=cursor)

    def _draw_roi(self, roi):
        selected = roi is self._selected_roi
        color = ROI_SELECTED_COLOR if selected else ROI_COLOR
        rect_id = self.canvas.create_rectangle(0, 0, 0, 0, outline=color, width=3 if selected else 2)
        text_id = self.canvas.create_text(
            0, 0, anchor="nw", text=roi["name"], fill=color, font=("Segoe UI", 9, "bold")
        )
        self._roi_items[id(roi)] = (rect_id, text_id)
        self._place_roi_items(roi)

    def _place_roi_items(self, roi):
        """Move roi's rectangle and label to its current x/y/w/h."""
        rect_id, text_id = self._roi_items[id(roi)]
        x, y = roi["x"] * self.scale, roi["y"] * self.scale
        w, h = roi["w"] * self.scale, roi["h"] * self.scale
        self.canvas.coords(rect_id, x, y, x + w, y + h)
        self.canvas.coords(text_id, x + 3, y + 3)
        if roi is self._selected_roi:
            self._draw_handles()

    def _draw_handles(self):
        """(Re)draw the 8 resize handles on the selected roi's corners/edges."""
        for item in self._handle_ids:
            self.canvas.delete(item)
        self._handle_ids = []
        roi = self._selected_roi
        if roi is None or id(roi) not in self._roi_items:
            return
        x0, y0 = roi["x"] * self.scale, roi["y"] * self.scale
        x1, y1 = x0 + roi["w"] * self.scale, y0 + roi["h"] * self.scale
        xm, ym, r = (x0 + x1) / 2, (y0 + y1) / 2, ROI_HANDLE_SIZE / 2
        for hx, hy in ((x0, y0), (xm, y0), (x1, y0), (x1, ym), (x1, y1), (xm, y1), (x0, y1), (x0, ym)):
            self._handle_ids.append(
                self.canvas.create_rectangle(
                    hx - r, hy - r, hx + r, hy + r, fill="white", outline=ROI_SELECTED_COLOR
                )
            )

    def _select_roi(self, roi):
        """Highlight roi (or nothing, if None) in purple and sync the list."""
        previous, self._selected_roi = self._selected_roi, roi
        for r in (previous, roi):
            if r is not None and id(r) in self._roi_items:
                selected = r is roi
                color = ROI_SELECTED_COLOR if selected else ROI_COLOR
                rect_id, text_id = self._roi_items[id(r)]
                self.canvas.itemconfigure(rect_id, outline=color, width=3 if selected else 2)
                self.canvas.itemconfigure(text_id, fill=color)
        if roi is not None:
            rect_id, text_id = self._roi_items[id(roi)]
            self.canvas.tag_raise(rect_id)  # on top of overlapping regions
            self.canvas.tag_raise(text_id)
        self._draw_handles()

        self.listbox.selection_clear(0, tk.END)
        if roi is not None:
            index = self.rois.index(roi)
            self.listbox.selection_set(index)
            self.listbox.see(index)
            self.kind_var.set(ROI_KINDS[roi.get("kind", DEFAULT_ROI_KIND)])
            self.kind_combo.configure(state="readonly")
        else:
            self.kind_var.set("")
            self.kind_combo.configure(state=tk.DISABLED)

    def _on_list_select(self, event=None):
        sel = self.listbox.curselection()
        self._select_roi(self.rois[sel[0]] if sel else None)

    def _grab_mode(self, cx, cy):
        """What pressing at canvas point (cx, cy) would do to the selected roi:
        None (outside it), "move" (inside), or the edges to resize when on
        or near its border ("n", "se", ...)."""
        roi = self._selected_roi
        if roi is None:
            return None
        x0, y0 = roi["x"] * self.scale, roi["y"] * self.scale
        x1, y1 = x0 + roi["w"] * self.scale, y0 + roi["h"] * self.scale
        # On small regions the grab band shrinks so the middle can still be dragged.
        tol_x = min(ROI_GRAB_TOLERANCE, (x1 - x0) / 4)
        tol_y = min(ROI_GRAB_TOLERANCE, (y1 - y0) / 4)
        if not (x0 - tol_x <= cx <= x1 + tol_x and y0 - tol_y <= cy <= y1 + tol_y):
            return None
        vertical = "n" if abs(cy - y0) <= tol_y else "s" if abs(cy - y1) <= tol_y else ""
        horizontal = "w" if abs(cx - x0) <= tol_x else "e" if abs(cx - x1) <= tol_x else ""
        mode = vertical + horizontal
        if mode:
            return mode
        return "move" if x0 <= cx <= x1 and y0 <= cy <= y1 else None

    def _set_roi_box(self, roi, x0, y0, x1, y1):
        """Set roi to the original-image box (x0, y0)-(x1, y1), inside the image."""
        roi["x"], roi["y"], roi["w"], roi["h"] = int(x0), int(y0), int(x1 - x0), int(y1 - y0)
        self._place_roi_items(roi)

    def _apply_edit(self, roi, mode, box, dx, dy):
        """Move (mode "move") or resize the given edges of roi, starting from
        its original-image box (x0, y0, x1, y1), by (dx, dy) original pixels.
        The region stays inside the image and at least ROI_MIN_SIZE wide/tall
        (scaled to the golden's resolution)."""
        img_h, img_w = self.golden_bgr.shape[:2]
        x0, y0, x1, y1 = box
        if mode == "move":
            dx = min(max(dx, -x0), img_w - x1)
            dy = min(max(dy, -y0), img_h - y1)
            self._set_roi_box(roi, x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            return
        if "w" in mode:
            x0 = min(max(x0 + dx, 0), x1 - self.min_size)
        if "e" in mode:
            x1 = max(min(x1 + dx, img_w), x0 + self.min_size)
        if "n" in mode:
            y0 = min(max(y0 + dy, 0), y1 - self.min_size)
        if "s" in mode:
            y1 = max(min(y1 + dy, img_h), y0 + self.min_size)
        self._set_roi_box(roi, x0, y0, x1, y1)

    @staticmethod
    def _roi_box(roi):
        return roi["x"], roi["y"], roi["x"] + roi["w"], roi["y"] + roi["h"]

    def _nudge(self, dx, dy):
        roi = self._selected_roi
        if roi is not None:
            self._apply_edit(roi, "move", self._roi_box(roi), dx, dy)
        return "break"

    def _resize_by(self, dw, dh):
        roi = self._selected_roi
        if roi is not None:
            self._apply_edit(roi, "e", self._roi_box(roi), dw, 0)
            self._apply_edit(roi, "s", self._roi_box(roi), 0, dh)
        return "break"

    def _on_press(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        mode = self._grab_mode(cx, cy)
        if mode is not None:
            self._edit_start = (mode, cx, cy, self._roi_box(self._selected_roi))
            self._update_crosshair(cx, cy)
            return

        self._start_xy = (cx, cy)
        self._temp_rect_id = self.canvas.create_rectangle(
            cx, cy, cx, cy, outline="yellow", width=2
        )
        self._update_crosshair(cx, cy)

    def _on_drag(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self._edit_start is not None:
            mode, start_cx, start_cy, box = self._edit_start
            self._apply_edit(
                self._selected_roi,
                mode,
                box,
                round((cx - start_cx) / self.scale),
                round((cy - start_cy) / self.scale),
            )
            self._update_crosshair(cx, cy)
            return
        if self._temp_rect_id is None or self._start_xy is None:
            return
        x0, y0 = self._start_xy
        self.canvas.coords(self._temp_rect_id, x0, y0, cx, cy)
        self._update_crosshair(cx, cy)

    def _on_release(self, event):
        if self._edit_start is not None:
            self._edit_start = None  # roi was already updated while dragging
            return
        if self._temp_rect_id is None or self._start_xy is None:
            return

        x0, y0 = self._start_xy
        x1, y1 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.canvas.delete(self._temp_rect_id)
        self._temp_rect_id = None
        self._start_xy = None

        cx0, cx1 = sorted((x0, x1))
        cy0, cy1 = sorted((y0, y1))

        orig_x0 = int(cx0 / self.scale)
        orig_y0 = int(cy0 / self.scale)
        orig_w = int((cx1 - cx0) / self.scale)
        orig_h = int((cy1 - cy0) / self.scale)

        if orig_w < self.min_size or orig_h < self.min_size:
            return  # accidental click / too small to be a real region

        name = simpledialog.askstring(
            "Name Region", "Name for this region (e.g. 'Diode D1'):", parent=self
        )
        if not name:
            return

        roi = {
            "name": name.strip(), "x": orig_x0, "y": orig_y0, "w": orig_w, "h": orig_h,
            "kind": DEFAULT_ROI_KIND,
        }
        self.rois.append(roi)
        self._draw_roi(roi)
        self.listbox.insert(tk.END, self._list_label(roi))

    def _on_delete(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        index = sel[0]
        roi = self.rois.pop(index)
        if roi is self._selected_roi:
            self._selected_roi = None
            self._draw_handles()
            self.kind_var.set("")
            self.kind_combo.configure(state=tk.DISABLED)
        rect_id, text_id = self._roi_items.pop(id(roi), (None, None))
        if rect_id is not None:
            self.canvas.delete(rect_id)
            self.canvas.delete(text_id)
        self.listbox.delete(index)

    def _on_save(self):
        save_rois(self.golden_path, self.rois)
        self.on_saved(self.rois)
        self.destroy()


class PCBInspectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PCB Inspector - Golden Board Comparison")
        self.root.geometry("1420x740")
        self.root.minsize(1100, 600)

        self.golden_bgr = None
        self.golden_path = None
        # Last image shown in each panel (BGR), kept so it can be re-fitted
        # whenever the panel is resized. Keyed by the panel's Canvas widget.
        self._panel_images = {}
        self._panel_photos = {}
        self._resize_after_id = None

        self.rois = []  # list of {"name","x","y","w","h"} in golden-image pixel space

        self.part_count = 0
        self.log_rows = []  # list of (date, time, part_count, status, errors, details, image_path)
        self.session_folder = None  # logs/<goldenboard>_<date>_<time>/ for the active golden board

        # Live camera testing (compare side)
        self.live_cap = None
        self.live_running = False
        self.awaiting_capture = False
        self._live_frame = None
        self._live_after_id = None
        self._result_after_id = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<space>", self._on_capture_pressed)

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.select_btn = ttk.Button(
            toolbar, text="Select Golden Board", command=self.on_select_golden
        )
        self.select_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.setup_roi_btn = ttk.Button(
            toolbar, text="Setup ROIs", command=self.on_setup_rois, state=tk.DISABLED
        )
        self.setup_roi_btn.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Step 1: select a golden board reference image.")
        status_label = ttk.Label(toolbar, textvariable=self.status_var, foreground="#444")
        status_label.pack(side=tk.LEFT, padx=20)

        led_frame = ttk.Frame(toolbar)
        led_frame.pack(side=tk.RIGHT, padx=(10, 0))

        self.led_hold_canvas = tk.Canvas(led_frame, width=22, height=22, highlightthickness=0)
        self.led_hold_canvas.pack(side=tk.LEFT, padx=(0, 4))
        self.led_hold_circle = self.led_hold_canvas.create_oval(
            2, 2, 20, 20, fill=LED_YELLOW_OFF, outline="#0a0a0a"
        )
        ttk.Label(led_frame, text="ON HOLD", font=("Segoe UI", 10, "bold")).pack(
            side=tk.LEFT, padx=(0, 16)
        )

        self.led_ok_canvas = tk.Canvas(led_frame, width=22, height=22, highlightthickness=0)
        self.led_ok_canvas.pack(side=tk.LEFT, padx=(0, 4))
        self.led_ok_circle = self.led_ok_canvas.create_oval(
            2, 2, 20, 20, fill=LED_GREEN_OFF, outline="#0a0a0a"
        )
        ttk.Label(led_frame, text="OK", font=("Segoe UI", 10, "bold")).pack(
            side=tk.LEFT, padx=(0, 16)
        )

        self.led_nok_canvas = tk.Canvas(led_frame, width=22, height=22, highlightthickness=0)
        self.led_nok_canvas.pack(side=tk.LEFT, padx=(0, 4))
        self.led_nok_circle = self.led_nok_canvas.create_oval(
            2, 2, 20, 20, fill=LED_RED_OFF, outline="#0a0a0a"
        )
        ttk.Label(led_frame, text="NOK", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

        panes = ttk.Frame(self.root, padding=10)
        panes.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Image panels share all the free space; the history table keeps a
        # fixed width (it scrolls horizontally).
        panes.columnconfigure(0, weight=1, uniform="image")
        panes.columnconfigure(1, weight=1, uniform="image")
        panes.columnconfigure(2, weight=0)
        panes.rowconfigure(1, weight=1)

        ttk.Label(panes, text="Golden Board (reference)", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, pady=(0, 6)
        )
        ttk.Label(panes, text="Live Camera / Test Board", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=1, pady=(0, 6)
        )
        ttk.Label(panes, text="Check History", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=2, pady=(0, 6)
        )

        # Canvases (not Labels) so the panels don't grow to fit the image:
        # the grid decides the panel size and the image is scaled to fill it.
        self.left_canvas = tk.Canvas(
            panes, background=PANEL_BG, highlightthickness=0, width=1, height=1
        )
        self.left_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        self.right_canvas = tk.Canvas(
            panes, background=PANEL_BG, highlightthickness=0, width=1, height=1
        )
        self.right_canvas.grid(row=1, column=1, sticky="nsew", padx=(8, 8))

        for canvas in (self.left_canvas, self.right_canvas):
            canvas.bind("<Configure>", self._on_panel_resized)

        self._build_table(panes)

        right_controls = ttk.Frame(panes)
        right_controls.grid(row=2, column=1, pady=(6, 0), sticky="w")

        self.capture_btn = ttk.Button(
            right_controls,
            text="Capture (Space)",
            command=self._on_capture_pressed,
            state=tk.DISABLED,
        )
        self.capture_btn.pack(side=tk.LEFT, padx=(0, 12))

        self.defect_var = tk.StringVar(value="")
        ttk.Label(right_controls, textvariable=self.defect_var, font=("Segoe UI", 10)).pack(
            side=tk.LEFT
        )

    def _show_in_panel(self, canvas, bgr_image):
        """Display bgr_image (or clear, if None) centered in canvas, scaled
        to fill the panel while keeping its aspect ratio."""
        self._panel_images[canvas] = bgr_image
        canvas.delete("all")
        if bgr_image is None:
            self._panel_photos.pop(canvas, None)
            return

        panel_w, panel_h = canvas.winfo_width(), canvas.winfo_height()
        if panel_w < 2 or panel_h < 2:
            # Not laid out yet; <Configure> will call back once it has a size.
            return

        photo = cv2_to_tk(bgr_image, max_w=panel_w, max_h=panel_h, allow_upscale=True)
        self._panel_photos[canvas] = photo  # keep a reference, or Tk drops the image
        canvas.create_image(panel_w // 2, panel_h // 2, image=photo, anchor="center")

    def _on_panel_resized(self, event=None):
        # Debounced: redraw once the window stops changing size.
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(60, self._refit_panels)

    def _refit_panels(self):
        self._resize_after_id = None
        for canvas, bgr_image in list(self._panel_images.items()):
            self._show_in_panel(canvas, bgr_image)

    def _build_table(self, parent):
        table_frame = ttk.Frame(parent, width=HISTORY_PANEL_W)
        table_frame.grid(row=1, column=2, sticky="nsew", padx=(8, 0))
        table_frame.grid_propagate(False)  # keep HISTORY_PANEL_W instead of the columns' total width
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("date", "time", "count", "status", "errors", "details", "image")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=15
        )
        self.tree.heading("date", text="Date")
        self.tree.heading("time", text="Time")
        self.tree.heading("count", text="Part #")
        self.tree.heading("status", text="Status")
        self.tree.heading("errors", text="Errors")
        self.tree.heading("details", text="Failed Regions")
        self.tree.heading("image", text="Image (double-click to open)")
        self.tree.column("date", width=85, anchor="center")
        self.tree.column("time", width=75, anchor="center")
        self.tree.column("count", width=55, anchor="center")
        self.tree.column("status", width=55, anchor="center")
        self.tree.column("errors", width=50, anchor="center")
        self.tree.column("details", width=180, anchor="w")
        self.tree.column("image", width=180, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.tag_configure("ok", foreground="#178a17")
        self.tree.tag_configure("nok", foreground="#c81e1e")

        self.tree.bind("<Double-1>", self._on_table_double_click)

    def _on_table_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        values = self.tree.item(item_id, "values")
        if len(values) < 7:
            return
        image_path = values[6]
        if image_path and os.path.isfile(image_path):
            try:
                os.startfile(image_path)
            except OSError as exc:
                messagebox.showerror("Could not open image", str(exc))

    def set_leds(self, status):
        """status: 'OK', 'NOK', 'HOLD', or None (all off)."""
        ok_color = LED_GREEN_ON if status == "OK" else LED_GREEN_OFF
        nok_color = LED_RED_ON if status == "NOK" else LED_RED_OFF
        hold_color = LED_YELLOW_ON if status == "HOLD" else LED_YELLOW_OFF
        self.led_ok_canvas.itemconfig(self.led_ok_circle, fill=ok_color)
        self.led_nok_canvas.itemconfig(self.led_nok_circle, fill=nok_color)
        self.led_hold_canvas.itemconfig(self.led_hold_circle, fill=hold_color)

    def export_log(self):
        """Write the current check history to a CSV file inside this session's
        log folder (logs/<goldenboard>_<date>_<time>/), alongside the result
        images already saved there during the session. Returns the saved CSV
        file path, or None if there was nothing to save."""
        if not self.log_rows or not self.session_folder:
            return None

        os.makedirs(self.session_folder, exist_ok=True)
        csv_filename = os.path.basename(self.session_folder) + ".csv"
        filepath = os.path.join(self.session_folder, csv_filename)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            # Semicolon delimiter: matches the default CSV list separator in
            # Excel's pt-BR locale. Since a field's own text may also contain
            # "; " (multiple failed region names), csv.writer automatically
            # quotes that field so it still opens as a single column instead
            # of splitting mid-list.
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Date", "Time", "Part Count", "Status", "Errors", "Failed Regions"])
            writer.writerows(row[:-1] for row in self.log_rows)  # drop the image path column

        return filepath

    def on_select_golden(self):
        if self.live_running:
            self._stop_live_camera()

        dlg = ChoiceDialog(
            self.root,
            "Select Golden Board",
            "How would you like to provide the Golden Board image?",
            [
                ("camera", "Capture from Camera"),
                ("file", "Open Image File"),
                ("saved", "Load Saved Board Setup"),
            ],
        )
        self.root.wait_window(dlg)

        if dlg.result == "camera":
            self._select_golden_from_camera()
        elif dlg.result == "file":
            self._select_golden_from_file()
        elif dlg.result == "saved":
            self._select_golden_from_saved()

    def _select_golden_from_file(self):
        path = filedialog.askopenfilename(
            title="Select Golden Board image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        image = cv2.imread(path)
        if image is None:
            messagebox.showerror("Error", f"Could not read image:\n{path}")
            return

        self._load_golden_image(image, path)

    def _select_golden_from_camera(self):
        def on_captured(frame):
            name = simpledialog.askstring(
                "Name Golden Board", "Name for this Golden Board:", parent=self.root
            )
            if not name or not name.strip():
                return
            name = name.strip()

            os.makedirs(GOLDEN_BOARDS_DIR, exist_ok=True)
            path = os.path.join(GOLDEN_BOARDS_DIR, f"{name}.png")
            cv2.imwrite(path, frame)
            self._load_golden_image(frame, path)

        CameraCaptureDialog(self.root, on_captured)

    def _select_golden_from_saved(self):
        boards = list_saved_boards()
        if not boards:
            messagebox.showinfo(
                "No Saved Boards",
                "No previously saved golden board setups were found. "
                "Load a board and Setup ROIs first to create one.",
            )
            return

        dlg = SavedBoardPickerDialog(self.root, boards)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return

        image = cv2.imread(dlg.result["image_path"])
        if image is None:
            messagebox.showerror("Error", f"Could not read image:\n{dlg.result['image_path']}")
            return

        self._load_golden_image(image, dlg.result["image_path"])

    def _load_golden_image(self, image, path):
        """Shared golden-board loading logic used by all three sources
        (camera capture, file open, saved-setup pick)."""
        if self.live_running:
            self._stop_live_camera()

        saved_path = self.export_log()

        self.golden_bgr = image
        self.golden_path = path
        self.session_folder = make_session_folder(path)
        self._show_in_panel(self.left_canvas, image)
        self._show_in_panel(self.right_canvas, None)
        self.defect_var.set("")

        self.rois = load_rois(path)

        self.part_count = 0
        self.log_rows = []
        self.tree.delete(*self.tree.get_children())
        self.set_leds(None)

        self.setup_roi_btn.configure(state=tk.NORMAL)

        board_name = os.path.splitext(os.path.basename(path))[0]
        if self.rois:
            self.status_var.set(
                f"Golden board loaded: {board_name} ({len(self.rois)} region(s) loaded)."
            )
        else:
            self.status_var.set(
                f"Golden board loaded: {board_name}. You can Setup ROIs any time."
            )

        if saved_path:
            messagebox.showinfo("Log Saved", "Current data log saved")

        self._start_live_camera()

    def on_setup_rois(self):
        if self.golden_bgr is None:
            messagebox.showwarning("No golden board", "Select a golden board first.")
            return

        def on_saved(new_rois):
            self.rois = new_rois
            self.status_var.set(f"{len(self.rois)} region(s) saved for this golden board.")

        ROISetupDialog(self.root, self.golden_bgr, self.golden_path, self.rois, on_saved)

    def _process_test_image(self, test_image):
        """Run alignment + ROI/whole-board evaluation on a captured test
        image, then update the right panel, LEDs, history table, CSV log
        row, and saved result image. Returns the resulting status
        ('OK'/'NOK'), "DISCARDED" if the board is absent or out of position
        (see check_placement; nothing is logged or counted), or None if
        comparison failed."""
        self.status_var.set("Aligning and comparing images...")
        self.root.update_idletasks()

        try:
            aligned, valid_mask, align_info = align_image_info(self.golden_bgr, test_image)

            message = check_placement(self.golden_bgr, test_image.shape, align_info, self.rois)
            if message is not None:
                self._show_in_panel(
                    self.right_canvas,
                    draw_placement_box(test_image, self.golden_bgr.shape, self.rois, message),
                )
                self.set_leds("HOLD")
                save_discarded_capture(self.session_folder, test_image, message)
                self.defect_var.set(f"{message} - captura descartada.")
                self.status_var.set("Captura descartada (não contabilizada).")
                return "DISCARDED"

            if self.rois:
                roi_results = evaluate_rois(self.golden_bgr, aligned, valid_mask, self.rois)
                result = draw_roi_results(aligned, roi_results)
                failed_names = [r["name"] for r in roi_results if r["status"] == "NOK"]
                defect_count = len(failed_names)
                details = "; ".join(failed_names) if failed_names else "All regions OK"
            else:
                mask = compute_diff_mask(self.golden_bgr, aligned, valid_mask)
                result, defect_count = highlight_differences(aligned, mask)
                failed_names = []
                details = (
                    "No ROIs defined - whole-board comparison"
                    if defect_count == 0
                    else f"No ROIs defined - {defect_count} unnamed diff region(s)"
                )
        except Exception as exc:
            messagebox.showerror("Comparison failed", str(exc))
            self.status_var.set("Comparison failed.")
            return None

        self._show_in_panel(self.right_canvas, result)

        status = "OK" if defect_count == 0 else "NOK"
        self.set_leds(status)

        self.part_count += 1
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        image_path = save_result_image(
            self.session_folder, date_str, time_str, self.part_count, status, result,
            raw_bgr=test_image,
        )

        row = (date_str, time_str, self.part_count, status, defect_count, details, image_path)
        self.log_rows.append(row)
        tag = "ok" if status == "OK" else "nok"
        self.tree.insert("", 0, values=row, tags=(tag,))

        if defect_count == 0:
            self.defect_var.set("No differences detected." if not self.rois else "All regions OK.")
        elif self.rois:
            self.defect_var.set(", ".join(f"{n}: NOK" for n in failed_names))
        else:
            self.defect_var.set(f"{defect_count} difference region(s) highlighted in red.")

        self.status_var.set("Comparison complete.")
        return status

    def _start_live_camera(self):
        if self.golden_bgr is None:
            return
        if self.live_running:
            return

        self.live_cap = open_camera()
        if not self.live_cap.isOpened():
            messagebox.showerror(
                "Camera Error",
                "Could not open the USB camera. Check that it is connected "
                "and not already in use by another application.",
            )
            self.live_cap = None
            return

        self.live_running = True
        self.awaiting_capture = True
        self.capture_btn.configure(state=tk.NORMAL)
        self.set_leds("HOLD")
        self.status_var.set(f"Live camera running ({self._camera_size()}) - press Space or Capture to test a board.")
        self._camera_tick()

    def _camera_size(self):
        """Resolution the camera is actually delivering, e.g. "1920x1080"."""
        if self.live_cap is None:
            return "?"
        w = int(self.live_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.live_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return f"{w}x{h}"

    def _stop_live_camera(self):
        self.live_running = False
        self.awaiting_capture = False

        if self._live_after_id is not None:
            self.root.after_cancel(self._live_after_id)
            self._live_after_id = None
        if self._result_after_id is not None:
            self.root.after_cancel(self._result_after_id)
            self._result_after_id = None

        if self.live_cap is not None:
            self.live_cap.release()
            self.live_cap = None

        self.capture_btn.configure(state=tk.DISABLED)
        self.set_leds(None)

    def _camera_tick(self):
        if not self.live_running or not self.awaiting_capture:
            return
        ok, frame = self.live_cap.read()
        if ok:
            self._live_frame = frame
            self._show_in_panel(self.right_canvas, frame)
        self._live_after_id = self.root.after(CAMERA_POLL_MS, self._camera_tick)

    def _on_capture_pressed(self, event=None):
        if not self.live_running or not self.awaiting_capture or self._live_frame is None:
            return

        self.awaiting_capture = False
        if self._live_after_id is not None:
            self.root.after_cancel(self._live_after_id)
            self._live_after_id = None

        frame = self._live_frame.copy()
        self.capture_btn.configure(state=tk.DISABLED)
        self._process_test_image(frame)

        self._result_after_id = self.root.after(RESULT_DISPLAY_MS, self._resume_after_result)

    def _resume_after_result(self):
        self._result_after_id = None
        if not self.live_running:
            return

        self.awaiting_capture = True
        self.capture_btn.configure(state=tk.NORMAL)
        self.set_leds("HOLD")
        self.status_var.set(f"Live camera running ({self._camera_size()}) - press Space or Capture to test a board.")
        self._camera_tick()

    def on_close(self):
        if not messagebox.askyesno(
            "Confirm Exit", "Are you sure you want to close the program?"
        ):
            return

        if self.live_running:
            self._stop_live_camera()

        saved_path = self.export_log()
        if saved_path:
            messagebox.showinfo("Log Saved", "Current data log saved")

        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    app = PCBInspectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
