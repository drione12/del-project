import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw  # noqa: E402

from core.image_scanner import find_near_duplicate_images, is_image_file, match_features_orb  # noqa: E402


def _make_textured_image(size=(200, 200), seed=0):
    img = Image.new("RGB", size, color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    for i in range(12):
        x = (i * 37 + seed) % (size[0] - 40)
        y = (i * 53 + seed) % (size[1] - 40)
        draw.rectangle([x, y, x + 30, y + 30], fill=((i * 20 + seed) % 255, 50, 200 - (i * 10) % 200))
        draw.ellipse([x + 5, y + 5, x + 40, y + 40], outline=(0, 0, 0), width=2)
    return img


def _make_random_shapes_image(size=(300, 300), seed=0):
    """A different, seed-randomized fixture just for the ORB comparison
    test below - _make_textured_image's shapes repeat the same couple of
    primitives at every seed (just shifted), which gave ORB near-identical
    local corner statistics even between "unrelated" seeds. Real random
    placement/size/color per shape gives genuinely non-overlapping local
    structure between different seeds, while a crop of one such image
    still shares its actual content with itself.
    """
    rng = random.Random(seed)
    img = Image.new("RGB", size, color=(230, 230, 230))
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x = rng.randint(0, size[0] - 30)
        y = rng.randint(0, size[1] - 30)
        w = rng.randint(10, 30)
        h = rng.randint(10, 30)
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        if rng.random() < 0.5:
            draw.rectangle([x, y, x + w, y + h], fill=color)
        else:
            draw.ellipse([x, y, x + w, y + h], fill=color)
    return img


def test_is_image_file():
    assert is_image_file("photo.jpg")
    assert is_image_file("photo.PNG")
    assert not is_image_file("document.txt")


def test_finds_near_duplicate_after_lossy_reencode():
    with tempfile.TemporaryDirectory() as tmp:
        img = _make_textured_image()
        img.save(os.path.join(tmp, "a.png"))
        img.save(os.path.join(tmp, "b.jpg"), quality=85)

        pairs = find_near_duplicate_images([tmp])

        assert len(pairs) == 1
        assert pairs[0].match_kind == "hash"
        assert pairs[0].similarity > 80.0


def test_does_not_match_unrelated_images():
    with tempfile.TemporaryDirectory() as tmp:
        _make_textured_image(seed=0).save(os.path.join(tmp, "a.png"))
        Image.new("RGB", (200, 200), color=(10, 200, 10)).save(os.path.join(tmp, "solid_green.png"))

        pairs = find_near_duplicate_images([tmp])

        assert pairs == []


def test_finds_near_duplicate_split_across_multiple_folders():
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        img = _make_textured_image()
        img.save(os.path.join(tmp_a, "a.png"))
        img.save(os.path.join(tmp_b, "b.jpg"), quality=85)

        pairs = find_near_duplicate_images([tmp_a, tmp_b])

        assert len(pairs) == 1
        assert {pairs[0].path_a, pairs[0].path_b} == {
            os.path.join(tmp_a, "a.png"),
            os.path.join(tmp_b, "b.jpg"),
        }


def test_overlapping_roots_do_not_duplicate_or_self_pair():
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "nested")
        os.makedirs(nested)
        _make_textured_image().save(os.path.join(nested, "a.png"))

        # tmp and its own subfolder both passed as roots - the same file
        # would be listed twice without de-duplication, which would make
        # it spuriously "pair" against itself.
        pairs = find_near_duplicate_images([tmp, nested])

        assert pairs == []


def test_orb_finds_more_matches_for_cropped_region_than_unrelated_image():
    with tempfile.TemporaryDirectory() as tmp:
        full = _make_random_shapes_image(size=(300, 300), seed=1)
        full_path = os.path.join(tmp, "full.png")
        full.save(full_path)

        cropped_path = os.path.join(tmp, "cropped.png")
        full.crop((50, 50, 250, 250)).save(cropped_path)

        unrelated_path = os.path.join(tmp, "unrelated.png")
        _make_random_shapes_image(size=(300, 300), seed=999).save(unrelated_path)

        cropped_matches = match_features_orb(full_path, cropped_path)
        unrelated_matches = match_features_orb(full_path, unrelated_path)

        assert cropped_matches is not None
        assert unrelated_matches is not None
        assert cropped_matches > unrelated_matches


def test_orb_returns_none_for_unreadable_image():
    with tempfile.TemporaryDirectory() as tmp:
        bad_path = os.path.join(tmp, "not_an_image.png")
        with open(bad_path, "wb") as f:
            f.write(b"not actually an image")
        good_path = os.path.join(tmp, "good.png")
        _make_textured_image().save(good_path)

        assert match_features_orb(bad_path, good_path) is None
