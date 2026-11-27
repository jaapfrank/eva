"""Pure-stdlib ISO/IEC 18004 QR Code SVG generator (Versions 1-10, ECC Level L, Byte mode).

Used to render 100% offline, privacy-preserving pairing QR codes for:
- Eva Pocket (iPhone / Android) & HoloStage screens on the local home network
- Single-use cryptographic Telegram owner & household invite links
- Secondary owner browser pairing links
"""

from __future__ import annotations

from urllib.parse import quote


# Version -> (total_codewords, ecc_codewords_per_block, num_blocks_group1, data_cw_group1, num_blocks_group2, data_cw_group2, alignment_centers)
# Source: ISO/IEC 18004 Table 7, Table 9 (Error Correction Level L = 0b01), Annex E.
_VERSION_SPECS_L: dict[int, tuple[int, int, int, int, int, int, tuple[int, ...]]] = {
    1: (26, 7, 1, 19, 0, 0, ()),
    2: (44, 10, 1, 34, 0, 0, (6, 18)),
    3: (70, 15, 1, 55, 0, 0, (6, 22)),
    4: (100, 20, 1, 80, 0, 0, (6, 26)),
    5: (134, 26, 1, 108, 0, 0, (6, 30)),
    6: (172, 18, 2, 68, 0, 0, (6, 34)),
    7: (196, 20, 2, 78, 0, 0, (6, 22, 38)),
    8: (242, 24, 2, 97, 0, 0, (6, 24, 42)),
    9: (292, 30, 2, 116, 0, 0, (6, 26, 46)),
    10: (346, 18, 2, 68, 2, 69, (6, 28, 50)),
}

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf256() -> None:
    val = 1
    for i in range(255):
        _GF_EXP[i] = val
        _GF_LOG[val] = i
        val <<= 1
        if val & 0x100:
            val ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf256()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        root = _GF_EXP[i]
        for j, coeff in enumerate(poly):
            next_poly[j] ^= coeff
            next_poly[j + 1] ^= _gf_mul(coeff, root)
        poly = next_poly
    return poly


def _rs_remainder(data: bytes | list[int], degree: int) -> list[int]:
    gen = _rs_generator_poly(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor != 0:
            for j in range(degree):
                rem[j] ^= _gf_mul(gen[j + 1], factor)
    return rem


def _select_version(payload_len: int) -> int:
    for ver in range(1, 11):
        _, _, g1_blocks, g1_data, g2_blocks, g2_data, _ = _VERSION_SPECS_L[ver]
        total_data_cw = g1_blocks * g1_data + g2_blocks * g2_data
        header_bits = 4 + (8 if ver <= 9 else 16)
        capacity_bytes = (total_data_cw * 8 - header_bits) // 8
        if payload_len <= capacity_bytes:
            return ver
    raise ValueError("QR payload exceeds Version 10-L capacity (max 271 bytes)")


def _encode_data_codewords(payload: bytes, version: int) -> list[int]:
    _, _, g1_blocks, g1_data, g2_blocks, g2_data, _ = _VERSION_SPECS_L[version]
    total_data_cw = g1_blocks * g1_data + g2_blocks * g2_data

    bits: list[int] = []

    def append_bits(val: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            bits.append((val >> shift) & 1)

    # Byte mode indicator = 0100
    append_bits(0b0100, 4)
    count_bits = 8 if version <= 9 else 16
    append_bits(len(payload), count_bits)
    for b in payload:
        append_bits(b, 8)

    max_bits = total_data_cw * 8
    terminator = min(4, max_bits - len(bits))
    append_bits(0, terminator)
    while len(bits) % 8 != 0:
        bits.append(0)

    codewords = [
        sum(bits[i + k] << (7 - k) for k in range(8))
        for i in range(0, len(bits), 8)
    ]
    pad_bytes = (0xEC, 0x11)
    idx = 0
    while len(codewords) < total_data_cw:
        codewords.append(pad_bytes[idx % 2])
        idx += 1
    return codewords


def _interleave_codewords(data_cw: list[int], version: int) -> list[int]:
    _, ecc_len, g1_blocks, g1_data, g2_blocks, g2_data, _ = _VERSION_SPECS_L[version]
    data_blocks: list[list[int]] = []
    ecc_blocks: list[list[int]] = []
    pos = 0
    for _ in range(g1_blocks):
        blk = data_cw[pos : pos + g1_data]
        pos += g1_data
        data_blocks.append(blk)
        ecc_blocks.append(_rs_remainder(blk, ecc_len))
    for _ in range(g2_blocks):
        blk = data_cw[pos : pos + g2_data]
        pos += g2_data
        data_blocks.append(blk)
        ecc_blocks.append(_rs_remainder(blk, ecc_len))

    out: list[int] = []
    max_data = max(len(b) for b in data_blocks)
    for col in range(max_data):
        for blk in data_blocks:
            if col < len(blk):
                out.append(blk[col])
    for col in range(ecc_len):
        for blk in ecc_blocks:
            out.append(blk[col])
    return out


def _format_bits_level_l(mask_id: int) -> int:
    # Level L = 0b01
    data = (0b01 << 3) | (mask_id & 0b111)
    rem = data << 10
    for shift in range(14, 9, -1):
        if (rem >> shift) & 1:
            rem ^= 0x537 << (shift - 10)
    return ((data << 10) | rem) ^ 0x5412


def _version_info_bits(version: int) -> int:
    rem = version << 12
    for shift in range(17, 11, -1):
        if (rem >> shift) & 1:
            rem ^= 0x1F25 << (shift - 12)
    return (version << 12) | rem


def _mask_bit(mask_id: int, r: int, c: int) -> bool:
    if mask_id == 0:
        return (r + c) % 2 == 0
    if mask_id == 1:
        return r % 2 == 0
    if mask_id == 2:
        return c % 3 == 0
    if mask_id == 3:
        return (r + c) % 3 == 0
    if mask_id == 4:
        return ((r // 2) + (c // 3)) % 2 == 0
    if mask_id == 5:
        return ((r * c) % 2 + (r * c) % 3) == 0
    if mask_id == 6:
        return (((r * c) % 2 + (r * c) % 3) % 2) == 0
    return (((r + c) % 2 + (r * c) % 3) % 2) == 0


def _penalty_score(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    # Rule 1: runs of >= 5 identical modules in rows and cols
    for r in range(size):
        run_color = modules[r][0]
        run_len = 1
        for c in range(1, size):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    for c in range(size):
        run_color = modules[0][c]
        run_len = 1
        for r in range(1, size):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    # Rule 2: 2x2 solid blocks
    for r in range(size - 1):
        for c in range(size - 1):
            val = modules[r][c]
            if (
                modules[r][c + 1] == val
                and modules[r + 1][c] == val
                and modules[r + 1][c + 1] == val
            ):
                score += 3

    # Rule 4: dark module balance
    dark = sum(1 for row in modules for cell in row if cell)
    total = size * size
    k = abs(dark * 20 - total * 10) // total
    score += k * 10
    return score


def encode_qr_matrix(text: str) -> list[list[bool]]:
    """Encode UTF-8 text (1..271 bytes) into a boolean QR module matrix."""
    if not isinstance(text, str) or not text:
        raise ValueError("QR text must be non-empty")
    payload = text.encode("utf-8")
    version = _select_version(len(payload))
    size = 17 + 4 * version
    _, _, _, _, _, _, align_coords = _VERSION_SPECS_L[version]

    modules = [[False] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, dark: bool) -> None:
        if 0 <= r < size and 0 <= c < size:
            modules[r][c] = dark
            is_func[r][c] = True

    def draw_finder(top: int, left: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = top + dr, left + dc
                if 0 <= rr < size and 0 <= cc < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6
                    on_ring = dr in (0, 6) or dc in (0, 6)
                    in_core = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_func(rr, cc, bool(in_outer and (on_ring or in_core)))

    draw_finder(0, 0)
    draw_finder(0, size - 7)
    draw_finder(size - 7, 0)

    # Timing patterns
    for i in range(8, size - 8):
        set_func(6, i, i % 2 == 0)
        set_func(i, 6, i % 2 == 0)

    # Alignment patterns
    for ar in align_coords:
        for ac in align_coords:
            if (
                (ar == 6 and ac == 6)
                or (ar == 6 and ac == size - 7)
                or (ar == size - 7 and ac == 6)
            ):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dist = max(abs(dr), abs(dc))
                    set_func(ar + dr, ac + dc, dist != 1)

    # Dark module & reserved format areas
    set_func(size - 8, 8, True)
    for i in range(9):
        if not is_func[8][i]:
            set_func(8, i, False)
        if not is_func[i][8]:
            set_func(i, 8, False)
    for i in range(8):
        if not is_func[8][size - 1 - i]:
            set_func(8, size - 1 - i, False)
        if not is_func[size - 1 - i][8]:
            set_func(size - 1 - i, 8, False)

    # Version info for V >= 7
    if version >= 7:
        vbits = _version_info_bits(version)
        for i in range(18):
            bit = bool((vbits >> i) & 1)
            r = i // 3
            c = size - 11 + (i % 3)
            set_func(r, c, bit)
            set_func(c, r, bit)

    # Place data + ECC bits in zigzag columns
    full_cw = _interleave_codewords(_encode_data_codewords(payload, version), version)
    bit_stream: list[bool] = [
        bool((byte >> shift) & 1)
        for byte in full_cw
        for shift in range(7, -1, -1)
    ]

    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        row_iter = range(size - 1, -1, -1) if upward else range(size)
        for r in row_iter:
            for dc in (0, -1):
                c = col + dc
                if not is_func[r][c]:
                    modules[r][c] = bit_stream[bit_idx] if bit_idx < len(bit_stream) else False
                    bit_idx += 1
        upward = not upward
        col -= 2

    def write_format_bits(target: list[list[bool]], mask_id: int) -> None:
        fbits = _format_bits_level_l(mask_id)
        # Copy 1 around top-left finder (bit 0 = LSB at (0,8) .. bit 14 = MSB at (8,0))
        coords_a = [
            (0, 8), (1, 8), (2, 8), (3, 8), (4, 8), (5, 8),
            (7, 8), (8, 8), (8, 7), (8, 5), (8, 4), (8, 3), (8, 2), (8, 1), (8, 0),
        ]
        for idx, (rr, cc) in enumerate(coords_a):
            target[rr][cc] = bool((fbits >> idx) & 1)
        # Copy 2 split across top-right (bits 0..7) and bottom-left (bits 8..14)
        for idx in range(8):
            target[8][size - 1 - idx] = bool((fbits >> idx) & 1)
        for idx in range(8, 15):
            target[size - 15 + idx][8] = bool((fbits >> idx) & 1)

    best_matrix: list[list[bool]] | None = None
    best_penalty: int | None = None
    for mask_id in range(8):
        cand = [row[:] for row in modules]
        for r in range(size):
            for c in range(size):
                if not is_func[r][c] and _mask_bit(mask_id, r, c):
                    cand[r][c] = not cand[r][c]
        write_format_bits(cand, mask_id)
        pen = _penalty_score(cand)
        if best_penalty is None or pen < best_penalty:
            best_penalty = pen
            best_matrix = cand

    assert best_matrix is not None
    return best_matrix


def render_qr_svg(text: str, *, border: int = 3) -> str:
    """Return a compact, crisp SVG string for the given QR payload."""
    matrix = encode_qr_matrix(text)
    size = len(matrix)
    pad = max(2, min(8, int(border)))
    dim = size + pad * 2
    parts: list[str] = []
    for r, row in enumerate(matrix):
        for c, cell in enumerate(row):
            if cell:
                parts.append(f"M{c + pad},{r + pad}h1v1h-1z")
    path_d = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR-code">'
        f'<rect width="{dim}" height="{dim}" fill="#ffffff" rx="1.5"/>'
        f'<path d="{path_d}" fill="#07111c"/>'
        f"</svg>"
    )


def render_qr_data_uri(text: str, *, border: int = 3) -> str:
    """Return a `data:image/svg+xml` URI suitable for `<img src="...">`."""
    svg = render_qr_svg(text, border=border)
    return "data:image/svg+xml;utf8," + quote(svg, safe=",:;/=#")


__all__ = ["encode_qr_matrix", "render_qr_data_uri", "render_qr_svg"]
