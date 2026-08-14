"""Duplicate/similar-image finder - perceptual hashing (imagehash.phash)
catches near-duplicates (resizes, re-encodes, minor edits), and OpenCV ORB
feature matching catches cropped/partial matches phash alone would miss
(a crop shifts every pixel's position, which perceptual hashing is not
designed to survive, but a shared set of local features still matches via
ORB's keypoint descriptors). Both are meant to run on a QThread - both
passes are exactly the kind of CPU-bound work that would otherwise freeze
the UI on a large photo folder, the same class of bug already fixed
elsewhere in this app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from PIL import Image

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
_PHASH_SIZE = 8
_PHASH_MAX_DISTANCE = 8  # out of 64 bits - empirically tuned, see test_image_scanner.py
_ORB_MIN_GOOD_MATCHES = 10


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS


def _list_images(root: str) -> List[str]:
    found = []
    for dirpath, _dirs, filenames in os.walk(root):
        for name in filenames:
            if is_image_file(name):
                found.append(os.path.join(dirpath, name))
    return found


@dataclass
class ImagePair:
    path_a: str
    path_b: str
    match_kind: str  # "hash" or "features"
    similarity: float  # 0-100, higher = more similar


def _phash(path: str):
    import imagehash

    try:
        with Image.open(path) as img:
            return imagehash.phash(img, hash_size=_PHASH_SIZE)
    except Exception:
        return None


def find_near_duplicate_images(
    root: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[ImagePair]:
    """Perceptual-hash pass - catches resizes, re-encodes, and minor edits
    that keep the overall image layout intact.
    """
    paths = _list_images(root)
    hashes = {}
    total = len(paths)
    for i, path in enumerate(paths):
        if should_cancel is not None and should_cancel():
            break
        h = _phash(path)
        if h is not None:
            hashes[path] = h
        if on_progress is not None:
            on_progress(i + 1, total)

    pairs: List[ImagePair] = []
    items = list(hashes.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            path_a, hash_a = items[i]
            path_b, hash_b = items[j]
            distance = hash_a - hash_b
            if distance <= _PHASH_MAX_DISTANCE:
                similarity = max(0.0, 100.0 * (1 - distance / 64.0))
                pairs.append(ImagePair(path_a, path_b, "hash", similarity))

    return pairs


def match_features_orb(path_a: str, path_b: str) -> Optional[int]:
    """Returns the count of "good" ORB feature matches between two images,
    or None if either image can't be read. A cropped/partial copy of an
    original shares many local features even though phash (which compares
    the image's overall coarse layout) would see them as unrelated.
    """
    import cv2

    img_a = cv2.imread(path_a, cv2.IMREAD_GRAYSCALE)
    img_b = cv2.imread(path_b, cv2.IMREAD_GRAYSCALE)
    if img_a is None or img_b is None:
        return None

    orb = cv2.ORB_create()
    _kp_a, des_a = orb.detectAndCompute(img_a, None)
    _kp_b, des_b = orb.detectAndCompute(img_b, None)
    if des_a is None or des_b is None:
        return 0

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des_a, des_b)
    good = [m for m in matches if m.distance < 50]
    return len(good)


def find_similar_images_by_features(
    root: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[ImagePair]:
    """Second pass for cropped/partial matches - meaningfully more
    expensive than the hash pass (pairwise feature matching is O(n^2) in
    image count, and each comparison is itself non-trivial), so this is
    meant to run after find_near_duplicate_images(), on whatever wasn't
    already caught there, not as a replacement for it.
    """
    paths = _list_images(root)
    total_pairs = max(len(paths) * (len(paths) - 1) // 2, 1)
    pairs: List[ImagePair] = []
    done = 0

    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            if should_cancel is not None and should_cancel():
                return pairs
            good_count = match_features_orb(paths[i], paths[j])
            done += 1
            if on_progress is not None:
                on_progress(done, total_pairs)
            if good_count is not None and good_count >= _ORB_MIN_GOOD_MATCHES:
                similarity = min(100.0, 100.0 * good_count / (good_count + 20))
                pairs.append(ImagePair(paths[i], paths[j], "features", similarity))

    return pairs
