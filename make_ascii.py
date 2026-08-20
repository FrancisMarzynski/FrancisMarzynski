#!/usr/bin/env python3
"""Turn a photo into the ASCII portrait used on the profile card.

Pure standard library - no Pillow, no system tools. Reads an 8-bit,
non-interlaced PNG, averages it into character cells, stretches the
contrast, and maps each cell onto a density ramp.

Usage:
    python3 make_ascii.py                    # re-render from photo.png
    python3 make_ascii.py --preview          # print, don't save
    python3 make_ascii.py --box 122,58,235,250 --gamma 0.7

Tuning, in the order worth trying:
    --box    crop as left,top,right,bottom in pixels. Tighter on the
             face is almost always better. Start here.
    --invert on by default: dark pixels become dense characters, which
             suits a dark subject on a bright background. Pass
             --no-invert for a bright subject on a dark background.
    --gamma  below 1.0 lifts the midtones and shows more detail in the
             darker half; above 1.0 crushes them.

The single biggest win is the source image: a tight, high-contrast
head-and-shoulders crop converts far better than a wide shot.
"""
import argparse, struct, sys, zlib

RAMP = " .'\",:;!~*=+oxOX%8&@#"          # sparse -> dense


def load_png(path):
    """-> (width, height, colour_type, palette, [row bytes])"""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"{path} is not a PNG. Convert it first: sips -s format png in.jpg --out photo.png")
    pos, idat, palette = 8, b"", None
    width = height = ctype = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        name, chunk = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if name == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or interlace:
                sys.exit("Need an 8-bit, non-interlaced PNG. Re-save it: sips -s format png photo.png --out photo.png")
        elif name == b"PLTE":
            palette = chunk
        elif name == b"IDAT":
            idat += chunk
        elif name == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = width * channels
    rows, previous, p = [], bytearray(stride), 0
    for _ in range(height):
        filter_type, p = raw[p], p + 1
        line, p = bytearray(raw[p:p + stride]), p + stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            upleft = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 255
            elif filter_type == 2:
                line[i] = (line[i] + up) & 255
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif filter_type == 4:
                pa, pb, pc = abs(up - upleft), abs(left - upleft), abs(left + up - 2 * upleft)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else upleft)) & 255
        rows.append(bytes(line))
        previous = line
    return width, height, ctype, palette, rows


def luminance(ctype, palette, row, x):
    if ctype == 6:
        r, g, b, _ = row[x * 4:x * 4 + 4]
    elif ctype == 2:
        r, g, b = row[x * 3:x * 3 + 3]
    elif ctype == 0:
        r = g = b = row[x]
    elif ctype == 4:
        r = g = b = row[x * 2]
    else:
        i = row[x]
        r, g, b = palette[i * 3:i * 3 + 3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def convert(path, cols, rows_out, box, gamma, invert):
    width, height, ctype, palette, pixels = load_png(path)
    x0, y0, x1, y1 = box or (0, 0, width, height)
    cell_w, cell_h = (x1 - x0) / cols, (y1 - y0) / rows_out

    grid = []
    for ry in range(rows_out):
        line = []
        for rx in range(cols):
            total = count = 0.0
            ya, yb = int(y0 + ry * cell_h), int(y0 + (ry + 1) * cell_h)
            xa, xb = int(x0 + rx * cell_w), int(x0 + (rx + 1) * cell_w)
            for sy in range(ya, max(ya + 1, yb)):
                if not 0 <= sy < height:
                    continue
                row = pixels[sy]
                for sx in range(xa, max(xa + 1, xb)):
                    if 0 <= sx < width:
                        total += luminance(ctype, palette, row, sx)
                        count += 1
            line.append(total / count if count else 0.0)
        grid.append(line)

    flat = sorted(v for line in grid for v in line)
    low = flat[int(len(flat) * 0.01)]
    high = flat[min(len(flat) - 1, int(len(flat) * 0.99))]
    span = max(1e-6, high - low)

    art = []
    for line in grid:
        chars = []
        for value in line:
            t = min(1.0, max(0.0, (value - low) / span)) ** gamma
            if invert:
                t = 1.0 - t
            chars.append(RAMP[min(len(RAMP) - 1, int(t * len(RAMP)))])
        art.append("".join(chars).rstrip())
    return art


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="photo.png")
    parser.add_argument("--out", default="ascii_art.txt")
    parser.add_argument("--cols", type=int, default=46)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--box", default="185,110,700,855", help="left,top,right,bottom in pixels, or 'full'")
    parser.add_argument("--gamma", type=float, default=0.70)
    parser.add_argument("--no-invert", dest="invert", action="store_false")
    parser.add_argument("--preview", action="store_true", help="print only, do not write the file")
    args = parser.parse_args()

    box = None if args.box == "full" else tuple(int(n) for n in args.box.split(","))
    art = convert(args.source, args.cols, args.rows, box, args.gamma, args.invert)

    print("\n".join(art))
    if not args.preview:
        with open(args.out, "w") as handle:
            handle.write("\n".join(art) + "\n")
        print(f"\nWrote {args.out} ({args.cols}x{args.rows}). Run 'python3 today.py --offline' to redraw the cards.", file=sys.stderr)


if __name__ == "__main__":
    main()
