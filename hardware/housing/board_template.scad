// Fit template for the Blue&Me case (50521871): the old board's outline and its
// four 5 mm holes, as a thin frame. Print it, drop it onto the four 27 mm bosses
// in the connector half: every boss tip must pass through its hole. If one does
// not, measure how far off it is and correct `holes` below.
//
// Frame: the old board seen from its solder side (the side that faced the lid),
// connector end on the left, origin at the board's bottom-left corner.
// Board: 138 x 83 mm (owner's calipers: 22 + 116 along the long edge, 79 at the
// connector end, 83 at the far end). Holes: fitted to the owner's six caliper
// distances between bosses (sides 71 / 100 / 65 / 129 mm, diagonals H1-H4 123 and
// H2-H3 140 mm; all met within 0.2 mm), the photo only choosing among the fits.
// Turned 1.0 deg anticlockwise (seen from the lid side) about the board centre on
// 2026-09-28: the first print fitted the bosses but sat ~1 deg anticlockwise in
// the case, and the old board's own edges showed the same 1.0 deg against its holes.

board = [138, 83];
step = [22, 2];          // connector end: 79 mm, not 83, over its first 22 mm (2 mm off each long edge)
hole_d = 5;              // old board's holes
holes = [                // [x, y] from the bottom-left corner
    [5.7, 77.1],         // H1  connector end
    [5.3, 6.2],          // H2  connector end
    [133.6, 62.6],       // H3  far end, on the edge
    [105.1, 4.4],        // H4  far end, 29 mm in from the edge
];
t = 1.2;                 // plate thickness
rim = 7;                 // frame width
ring_d = 12;

$fn = 48;

module outline() {
    difference() {
        square(board);
        square(step);
        translate([0, board[1] - step[1]]) square(step);
    }
}

module frame() {
    difference() {
        outline();
        offset(delta = -rim) outline();
    }
}

linear_extrude(t) difference() {
    union() {
        frame();
        for (h = holes) translate(h) circle(d = ring_d);
        // one diagonal brace so the frame stays flat
        hull() { translate(holes[1]) circle(d = 6); translate(holes[2]) circle(d = 6); }
    }
    for (h = holes) translate(h) circle(d = hole_d);
}
