#!/usr/bin/env python
"""Check a PNG produced by a screenshot step.

Two questions in one pass:

  * is it a real PNG?            (signature)
  * is it a single flat colour?  (a blank capture: the OS denied screen
                                  recording, the window could not be read, or a
                                  GPU frame came back black)

The second check exists so a screenshot step can FAIL LOUDLY instead of
reporting "OK" on an empty image. A silently blank artifact is worse than an
error, because nobody re-checks a file that claims success.

Exit codes:
  0  PNG with visible content (>= 2 distinct sampled colours)
  1  PNG that is a single flat colour  -> blank capture
  2  not a PNG (bad signature)
  3  cannot analyse (unsupported PNG variant / decode error / unreadable)
     Deliberately NOT reported as blank: a limitation of THIS checker must not
     be turned into a claim that the capture failed. Callers warn and continue.

Only 8-bit, non-interlaced PNGs are decoded - that is what screencapture,
ImageMagick, grim and PrintWindow produce. Anything else reports 3.

Usage: python png_check.py <file.png> [--quiet]
"""
import sys
import zlib

SIG = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}   # grey / rgb / palette / grey+a / rgba


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def load(path):
    """Return (width, height, channels, rows) or None if not decodable."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != SIG:
        return None

    pos, ihdr, idat = 8, None, bytearray()
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            ihdr = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        pos += 12 + length

    if ihdr is None or len(ihdr) < 13:
        return None
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    depth, ctype = ihdr[8], ihdr[9]
    interlace = ihdr[12]
    if depth != 8 or interlace != 0 or ctype not in CHANNELS:
        return None
    ch = CHANNELS[ctype]
    if width <= 0 or height <= 0:
        return None

    raw = zlib.decompress(bytes(idat))
    stride = width * ch
    rows, prev, off = [], bytearray(stride), 0
    for _ in range(height):
        if off + 1 + stride > len(raw):
            return None
        ft = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        if ft == 1:                                   # Sub
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ft == 2:                                 # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:                                 # Average
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:                                 # Paeth
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                c = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ft != 0:
            return None
        rows.append(bytes(line))
        prev = line
    return width, height, ch, rows


def sampled_colours(width, height, ch, rows, grid=16):
    """Distinct colours on a coarse grid; stops early once two are found."""
    seen = set()
    for y in range(0, height, max(1, height // grid)):
        row = rows[y]
        for x in range(0, width, max(1, width // grid)):
            seen.add(row[x * ch:(x + 1) * ch])
            if len(seen) >= 2:
                return seen
    return seen


def main():
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    args = [a for a in argv if a != "--quiet"]
    if len(args) != 1:
        sys.stderr.write("usage: png_check.py <file.png> [--quiet]\n")
        return 3
    path = args[0]

    def say(msg):
        if not quiet:
            print(msg)

    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        say("cannot read %s: %s" % (path, exc))
        return 3
    if head != SIG:
        say("not a PNG (signature %r): %s" % (head, path))
        return 2
    try:
        loaded = load(path)
    except Exception as exc:                           # noqa: BLE001
        say("cannot analyse %s (%s)" % (path, exc))
        return 3
    if loaded is None:
        say("cannot analyse %s (unsupported PNG variant)" % path)
        return 3

    width, height, ch, rows = loaded
    colours = sampled_colours(width, height, ch, rows)
    if len(colours) < 2:
        say("blank capture: %s is one flat colour (%dx%d)" % (path, width, height))
        return 1
    say("PNG ok: %s (%dx%d, %d distinct sampled colours)" % (path, width, height, len(colours)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
