from __future__ import annotations

import numpy as np

UPSCALE = 2.0
FEATURES = 6000
LEVELS = 14
SCALE_STEP = 1.18
RATIO = 0.8
MIN_MATCHES = 8
MIN_INLIERS = 8
MIN_WIDTH_SHARE = 0.05
MAX_ASPECT_DRIFT = 1.6
CLOSEUP_COVER = 0.5
CLOSEUP_WIDTH = 0.85
RETRY_ZOOM = 2.0
SEARCH_SCALES = (0.5, 0.4, 1.0)
ANCHOR_TOL = 0.35
ANCHOR_SCALE = 1.4


OUTSIDE_LIMIT = 0.15
MAX_SHARE = 1.0


def plausible(box, frame_w, frame_h) -> bool:
    if not box:
        return False
    x, y, w, h = (float(v) for v in box)
    if w <= 8 or h <= 8:
        return False
    if w * h > frame_w * frame_h * MAX_SHARE:
        return False
    visible_w = max(0.0, min(x + w, frame_w) - max(x, 0.0))
    visible_h = max(0.0, min(y + h, frame_h) - max(y, 0.0))
    return (visible_w * visible_h) >= (w * h) * (1.0 - OUTSIDE_LIMIT)


class Locator:
    def __init__(self, photo, upscale=UPSCALE):
        import cv2

        self.cv2 = cv2
        image = cv2.imdecode(np.fromfile(str(photo), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("не читается снимок: %s" % photo)
        if upscale and upscale != 1.0:
            image = cv2.resize(image, None, fx=upscale, fy=upscale,
                               interpolation=cv2.INTER_CUBIC)
        self.height, self.width = image.shape[:2]
        self.aspect = self.width / float(self.height)
        self.orb = cv2.ORB_create(nfeatures=FEATURES, nlevels=LEVELS,
                                  scaleFactor=SCALE_STEP, edgeThreshold=12,
                                  fastThreshold=8)
        self.keypoints, self.descriptors = self.orb.detectAndCompute(
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.corners = np.float32([[0, 0], [self.width, 0],
                                   [self.width, self.height],
                                   [0, self.height]]).reshape(-1, 1, 2)

    def usable(self) -> bool:
        return self.descriptors is not None and len(self.keypoints) >= 40

    def find(self, frame):
        cv2 = self.cv2
        frame_h, frame_w = frame.shape[:2]
        best = None
        for scale in SEARCH_SCALES:
            if scale == 1.0:
                image = frame
            else:
                image = cv2.resize(frame, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_AREA)
            hit = self._match(image)
            if hit is None:
                continue
            if scale != 1.0:
                x, y, w, h = hit["box"]
                hit["box"] = (x / scale, y / scale, w / scale, h / scale)
            if not plausible(hit["box"], frame_w, frame_h):
                continue
            if best is None or hit["inliers"] > best["inliers"]:
                best = hit
        if best is not None:
            return best
        bigger = cv2.resize(frame, None, fx=RETRY_ZOOM, fy=RETRY_ZOOM,
                            interpolation=cv2.INTER_CUBIC)
        hit = self._match(bigger)
        if hit is None:
            return None
        x, y, w, h = hit["box"]
        hit["box"] = (x / RETRY_ZOOM, y / RETRY_ZOOM,
                      w / RETRY_ZOOM, h / RETRY_ZOOM)
        if not plausible(hit["box"], frame_w, frame_h):
            return None
        return hit

    def _match(self, frame):
        cv2 = self.cv2
        if not self.usable():
            return None
        keypoints, descriptors = self.orb.detectAndCompute(
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None)
        if descriptors is None or len(keypoints) < 10:
            return None
        good = []
        for pair in self.matcher.knnMatch(self.descriptors, descriptors, k=2):
            if len(pair) == 2 and pair[0].distance < RATIO * pair[1].distance:
                good.append(pair[0])
        if len(good) < MIN_MATCHES:
            return None
        source = np.float32([self.keypoints[m.queryIdx].pt
                             for m in good]).reshape(-1, 1, 2)
        target = np.float32([keypoints[m.trainIdx].pt
                             for m in good]).reshape(-1, 1, 2)
        matrix, mask = cv2.estimateAffinePartial2D(
            source, target, method=cv2.RANSAC, ransacReprojThreshold=8.0,
            maxIters=5000, confidence=0.995)
        if matrix is None or mask is None:
            return None
        mapped = cv2.transform(self.corners, matrix).reshape(-1, 2)
        x0, y0 = mapped.min(axis=0)
        x1, y1 = mapped.max(axis=0)
        box_w, box_h = float(x1 - x0), float(y1 - y0)
        frame_h, frame_w = frame.shape[:2]
        if box_w < frame_w * MIN_WIDTH_SHARE or box_h < 16:
            return None
        drift = (box_w / box_h) / self.aspect
        if drift > MAX_ASPECT_DRIFT or drift < 1.0 / MAX_ASPECT_DRIFT:
            return None
        visible = (max(0.0, min(x1, frame_w) - max(x0, 0.0))
                   * max(0.0, min(y1, frame_h) - max(y0, 0.0)))
        return {
            "box": (float(x0), float(y0), box_w, box_h),
            "inliers": int(mask.sum()),
            "matches": len(good),
            "cover": visible / float(frame_w * frame_h),
            "width_share": box_w / float(frame_w),
        }


def agreeing(picked):
    if len(picked) < 3:
        return picked
    areas = sorted(item["box"][2] * item["box"][3] for item in picked)
    middle = areas[len(areas) // 2]
    if middle <= 0:
        return picked
    close = [item for item in picked
             if 0.5 <= (item["box"][2] * item["box"][3]) / middle <= 2.0]
    return close or picked


def consensus(found, min_inliers=MIN_INLIERS):
    strong = [item for item in found if item["inliers"] >= min_inliers]
    picked = agreeing(strong or found)
    if not picked:
        return None
    values = list(zip(*[item["box"] for item in picked]))
    box = tuple(float(np.median(v)) for v in values)
    return {
        "box": box,
        "seen": len(picked),
        "sampled": len(found),
        "inliers": int(np.median([item["inliers"] for item in picked])),
        "cover": float(np.median([item["cover"] for item in picked])),
        "width_share": float(np.median([item["width_share"] for item in picked])),
        "confident": bool(strong),
    }


def median_box(boxes):
    if not boxes:
        return None
    return tuple(float(np.median(v)) for v in zip(*boxes))


def agrees(box, anchor, tol=ANCHOR_TOL) -> bool:
    if not box or not anchor:
        return False
    size = max(anchor[2], anchor[3], 1.0)
    if abs((box[0] + box[2] / 2.0) - (anchor[0] + anchor[2] / 2.0)) > size * tol:
        return False
    if abs((box[1] + box[3] / 2.0) - (anchor[1] + anchor[3] / 2.0)) > size * tol:
        return False
    ratio = max(box[2], 1.0) / max(anchor[2], 1.0)
    return 1.0 / ANCHOR_SCALE <= ratio <= ANCHOR_SCALE


def closeup(report) -> bool:
    if not report or not report.get("confident"):
        return False
    return (report["cover"] >= CLOSEUP_COVER
            or report["width_share"] >= CLOSEUP_WIDTH)


def crop_to_aspect(box, aspect, focus=None):
    x, y, w, h = (float(v) for v in box)
    if aspect <= 0 or w <= 0 or h <= 0:
        return box
    if w / h > aspect:
        keep_w, keep_h = h * aspect, h
    else:
        keep_w, keep_h = w, w / aspect
    cx, cy = x + w / 2.0, y + h / 2.0
    if focus:
        fx, fy, fw, fh = (float(v) for v in focus)
        cx, cy = fx + fw / 2.0, fy + fh / 2.0
    nx = min(max(cx - keep_w / 2.0, x), x + w - keep_w)
    ny = min(max(cy - keep_h / 2.0, y), y + h - keep_h)
    return nx, ny, keep_w, keep_h


