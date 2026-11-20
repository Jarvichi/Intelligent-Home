"""The 'button pressed' picture: the tapped zone cut out of the frame on screen and inverted (black <-> white), so a button looks pressed
and keeps its label. The Kindle draws it at the zone's position the moment you tap."""
import io


def invert_crop(png, box):
    """PNG bytes of that region of the frame, drawn pressed, or None if it cannot be made.

    Only the button itself is inverted: the light page around it (the corners of a rounded button) is left alone, and a 3 px black
    outline stays so the button reads as 'fill gone, outline left'. If no button shape is found the whole region is inverted."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
        x0, y0, x1, y1 = (int(v) for v in box)
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
            if x1 - x0 < 2 or y1 - y0 < 2 or (x1 - x0) * (y1 - y0) > 1072 * 800:
                return None
            crop = image.convert('L').crop((x0, y0, x1, y1))
        flipped = ImageOps.invert(crop)
        # Light pixels reachable from the edge of the region are the page, not the button (1 px padding joins all four sides).
        mask = ImageOps.expand(crop.point(lambda v: 255 if v > 180 else 0), 1, 255)
        ImageDraw.floodfill(mask, (0, 0), 128)
        mask = mask.crop((1, 1, mask.width - 1, mask.height - 1))
        inside = mask.point(lambda v: 0 if v == 128 else 255)
        core = inside.filter(ImageFilter.MinFilter(7))
        if core.getbbox() is None:
            result = flipped
        else:
            result = crop.copy()
            result.paste(0, mask=ImageChops.subtract(inside, core))   # the outline
            result.paste(flipped, mask=core)                           # the fill, swapped
        out = io.BytesIO()
        result.save(out, 'PNG')
        return out.getvalue()
    except Exception:
        return None
