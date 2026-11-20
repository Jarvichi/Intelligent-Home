"""The 'button pressed' picture: the tapped zone cut out of the frame on screen and inverted (black <-> white), so a button looks pressed
and keeps its label. The Kindle draws it at the zone's position the moment you tap."""
import io


def invert_crop(png, box):
    """PNG bytes of the frame, [x0, y0, x1, y1] -> PNG bytes of that region inverted, or None if it cannot be made."""
    try:
        from PIL import Image, ImageOps
        x0, y0, x1, y1 = (int(v) for v in box)
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
            if x1 - x0 < 2 or y1 - y0 < 2 or (x1 - x0) * (y1 - y0) > 1072 * 800:
                return None
            out = io.BytesIO()
            ImageOps.invert(image.convert('L').crop((x0, y0, x1, y1))).save(out, 'PNG')
            return out.getvalue()
    except Exception:
        return None
