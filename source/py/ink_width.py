"""Widen ink inside fixed monospaced cells without changing advances."""
import json
from copy import deepcopy
from fontTools.misc.fixedTools import otRound
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates
from fontTools.ttLib.tables.ttProgram import Program

FACTOR = 1.05

def widen_ink(font):
    assert "glyf" in font and "fvar" not in font
    glyf, metrics = font["glyf"], font["hmtx"].metrics
    cmap = font.getBestCmap()
    protected = {
        glyph for cp, glyph in cmap.items()
        if 0x2500 <= cp <= 0x259F or 0x2800 <= cp <= 0x28FF
    }
    transforms, snapshots, replacements = {}, {}, {}
    samples = {ord(c) for c in "AHmW0中"}
    sample_names = {cmap[cp]: cp for cp in samples}
    limited = expanded = 0
    for name in font.getGlyphOrder():
        glyph = glyf[name]
        advance, old_lsb = metrics[name]
        coords, ends, flags = glyph.getCoordinates(glyf)
        points = list(coords)
        scale = FACTOR
        if name in protected:
            scale = 1.0
        elif advance and points:
            xmin, xmax = min(x for x, y in points), max(x for x, y in points)
            # Preserve existing overhangs; never create a new cell overhang.
            if xmin < 0 or xmax > advance:
                scale = 1.0
            else:
                if xmin < advance / 2:
                    scale = min(scale, advance / (advance - 2 * xmin))
                if xmax > advance / 2:
                    scale = min(scale, advance / (2 * xmax - advance))
        scale = max(1.0, scale)
        shift = (1.0 - scale) * advance / 2
        transforms[name] = scale, shift
        new_points = [(otRound(scale * x + shift), y) for x, y in points]
        assert [y for x, y in new_points] == [y for x, y in points]
        if advance and points and min(x for x, y in points) >= 0 and max(x for x, y in points) <= advance:
            assert min(x for x, y in new_points) >= 0
            assert max(x for x, y in new_points) <= advance
        new = Glyph()
        new.numberOfContours = len(ends)
        if points:
            new.coordinates = GlyphCoordinates(new_points)
            new.endPtsOfContours = list(ends)
            new.flags = flags[:]
            new.program = Program()
            new.program.fromBytecode([])
        replacements[name] = new
        if points:
            metrics[name] = (advance, min(x for x, y in new_points))
            if name in sample_names:
                snapshots[str(sample_names[name])] = {
                    "before": max(x for x, y in points) - min(x for x, y in points),
                    "after": max(x for x, y in new_points) - min(x for x, y in new_points),
                    "advance": advance,
                    "factor": scale,
                }
        if scale > 1:
            expanded += 1
        if scale < FACTOR:
            limited += 1
    # Decomposition prevents applying a transform twice to composite references.
    glyf.glyphs = replacements
    for glyph in replacements.values():
        glyph.recalcBounds(glyf)

    def anchor(a, name):
        if a is None:
            return None
        a = deepcopy(a)
        scale, shift = transforms[name]
        a.XCoordinate = otRound(scale * a.XCoordinate + shift)
        return a

    def marks(coverage, array):
        for name, record in zip(coverage.glyphs, array.MarkRecord):
            record.MarkAnchor = anchor(record.MarkAnchor, name)

    if "GPOS" in font:
        for lookup in font["GPOS"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                kind = lookup.LookupType
                if kind == 9:
                    kind, sub = sub.ExtensionLookupType, sub.ExtSubTable
                if kind == 3:
                    for name, record in zip(sub.Coverage.glyphs, sub.EntryExitRecord):
                        record.EntryAnchor = anchor(record.EntryAnchor, name)
                        record.ExitAnchor = anchor(record.ExitAnchor, name)
                elif kind == 4:
                    marks(sub.MarkCoverage, sub.MarkArray)
                    for name, record in zip(sub.BaseCoverage.glyphs, sub.BaseArray.BaseRecord):
                        record.BaseAnchor = [anchor(a, name) for a in record.BaseAnchor]
                elif kind == 5:
                    marks(sub.MarkCoverage, sub.MarkArray)
                    for name, record in zip(sub.LigatureCoverage.glyphs, sub.LigatureArray.LigatureAttach):
                        for component in record.ComponentRecord:
                            component.LigatureAnchor = [anchor(a, name) for a in component.LigatureAnchor]
                elif kind == 6:
                    marks(sub.Mark1Coverage, sub.Mark1Array)
                    for name, record in zip(sub.Mark2Coverage.glyphs, sub.Mark2Array.Mark2Record):
                        record.Mark2Anchor = [anchor(a, name) for a in record.Mark2Anchor]
    if "GDEF" in font:
        carets = font["GDEF"].table.LigCaretList
        if carets:
            for name, record in zip(carets.Coverage.glyphs, carets.LigGlyph):
                scale, shift = transforms[name]
                for value in record.CaretValue:
                    if hasattr(value, "Coordinate"):
                        value.Coordinate = otRound(scale * value.Coordinate + shift)
    font["meta"].data["hxsc"] = str(FACTOR).encode("ascii")
    font["meta"].data["hbud"] = json.dumps(snapshots, sort_keys=True).encode("ascii")
    print("Widen ink, advances preserved:", "expanded", expanded, "limited/protected", limited, snapshots)
