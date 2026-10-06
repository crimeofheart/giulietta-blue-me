// Sled for the Blue&Me case: everything inside the original case, no drilling.
//
//   frame  flat ring at the old board's level, on all four bosses; the lid posts
//          and case screws clamp it there as they clamped the board (1.6 mm pads).
//          A plate on its connector side carries the buck, hanging under it on
//          two zip ties, over the raised floor.
//   tray   on the case floor ribs, parts facing up, with three posts that the
//          frame screws onto (M3 from the top), so tray + frame are one rigid unit
//
// All print flat without supports: part = "tray" / "frame" / "tray_test";
// "assembly" shows them in place with the parts as boxes. tray_test is the tray's
// outline and cut-outs as a 0.8 mm sheet: lay it in the case first, it must lie
// flat on the ribs and every floor feature must come through its hole.
//
// Frame: the board frame from board_template.scad (lid view, x from the connector
// end, y across, origin the old board's bottom-left corner); z up from the top of
// the floor ribs (boss tips at 25). Floor features from two photos of the case
// (the bare case at ~15 cm, the template on the bosses at ~30 cm), each posed on
// the four boss tips and projected down to the floor, not the tips' plane (the
// first model did that: up to 9 mm off). The two photos agree within ~2 mm.
// Corrected from a third photo of the printed tray_test in the case (2026-09-28,
// camera and sheet fitted together, 0.5 mm rms): snap tabs, wall blade, disc.
// Then from the v2 sheet (photo 192939, 0.3 mm rms): step base, left wall line,
// blade, disc, clips; the sheet lay ~0.8 mm / 0.75 deg off, pushed by the step.

part = "assembly";

// ---- case ---------------------------------------------------------------
bosses = [[5.7, 77.1], [5.3, 6.2], [105.1, 4.4], [133.6, 62.6]];   // H1 H2 H4 H3
boss_z = 25;                    // floor ribs to boss tips
boss_keep = 9;                  // boss with its fins
raised = [68, 30.3, 10];        // connector corner: floor 10 mm higher for x < 68, y > 30.3 (step base)
plug_window = [7.2, 53, 62.6, 70.1];            // in the raised floor: the car's plug comes in here
tongues = [[84.1, 69.7, 137.3, 86.4], [84.1, -26.2, 135.9, -7.9]];   // flex outward
latches = [[38.8, -7.8, 46.8, 3.5], [66.1, -7.7, 73.2, 3.3]];        // snap clips in floor holes, 1.3 mm tall (owner)
cross_pins = [[118.3, 51.8], [117.8, 7.9]];     // cross-shaped pins, ~7 mm across
disc = [74.2, 30.0];            // round floor pad, ~12 mm across
wall_blade = [28, -54, 31, -44.5];   // slanted vent blade on the far wall, reaches the floor at y -44.5
case_posts = [[35, 111], [127.5, 96], [2, -35], [106, -48]];   // full-height, with fins
keep = 1;                       // clearance round all of the above (the photos are +-1-2 mm)

// ---- frame --------------------------------------------------------------
board = [138, 83];
step = [22, 2];                 // old board 79 mm over its first 22 mm
beam_w = 7;
beam_t = 3.0;                   // lid is 1.6 + 5 mm above the boss tips
pad_d = 18;                     // lid posts are 15 across
pad_t = 1.6;                    // as the old board
boss_hole = 5;                  // as the old board: case screws pass through
brace_w = 7;
buck_plate = [15, 76, 60, 108]; // over the raised floor, beside the plug window

// ---- tray ---------------------------------------------------------------
tray_t = 2.5;
tray_outline = [[13, -49.5], [82.5, -49.5], [82.5, -6], [112, -6], [112, 68], [77, 68], [77, 83],
                [70, 83], [70, 29.3], [10, 29.3], [10, -43], [13, -43]];   // >= 1 mm off the step and the walls
test_t = 0.8;                   // tray_test thickness
blade_notch = 8;                // notch for the wall blade, depth from the tray's far edge (owner)
post_xy = [[61, 3.5], [105, brace_y(105)], [73.5, 79.5]];   // under the bottom beam (between the clips, clear of
                                // the card's cable), the brace, the top beam
post_w = 6;
post_gap = 0.2;                 // posts a hair short: the frame seats on the bosses, not the posts
pilot_d = 2.5;                  // M3 into PETG
slot = [1.6, 3.6];              // zip-tie slot

// ---- parts (x0, y0, x1, y1) ------------------------------------------------
pi = [15.5, -44.5, 80.5, -14.5];    // Pi Zero 65 x 30, header to the far wall, SD end right, ports up (+y):
                                    // PWR at x 26.5, OTG at x 39 (unused: the card's cable is soldered to
                                    // the test pads underneath, owner 2026-09-29);
                                    // 2.8 mm clear of the wall blade at the Pi's underside
perf = [13, -44.5, 83, -14.5];  // 30 x 70 perfboard on its header
pi_holes = [[3.5, 3.5], [61.5, 3.5], [3.5, 26.5], [61.5, 26.5]];
pi_standoff = 5;                // room under it for the pad joints and the card's wires
cable_tie = [55, -7];           // tie point for the card's cable, between the Pi and the MCP
mcp = [17, 0.5, 56.7, 28];      // MCP2515 39.7 x 27.5, CAN terminal left (to the plug window)
mcp_holes = [[2.35, 2], [37.35, 2], [2.35, 25.5], [37.35, 25.5]];   // 35 x 23.5 centre to centre, holes 4 mm (owner): M3
mcp_standoff = 4;               // room for its solder tails; passes over the 1.3 mm clip
card = [73.5, 15, 100.5, 61];   // sound card 27 x 46, jacks up (+y), cable down
plugs = [[78.5, 84.5], [89.5, 95.5]];   // x ranges of the two 3.5 mm plugs, 38 mm long
card_ties = [18, 44];           // y of the zip ties across it (clear of the disc hole)
buck = [16, 83, 56, 105];       // buck 40 x 22 x 12, no holes: hangs under buck_plate
buck_ties = [22, 50];           // x of the zip ties across it
standoff_d = 6;

$fn = 48;

function brace_y(x) = 6.2 + (x - 5.3) * (62.6 - 6.2) / (133.6 - 5.3);

module board_outline() {
    difference() { square(board); square(step); translate([0, board[1] - step[1]]) square(step); }
}

module frame() {
    difference() {
        linear_extrude(beam_t) difference() {
            union() {
                intersection() {                // nothing past the old board's edge: it hits the case walls (owner)
                    board_outline();
                    union() {
                        difference() { board_outline(); offset(delta = -beam_w) board_outline(); }
                        hull() { translate(bosses[1]) circle(d = brace_w); translate(bosses[3]) circle(d = brace_w); }
                        for (b = bosses) translate(b) circle(d = pad_d);
                        for (p = post_xy) translate(p) circle(d = 9);
                    }
                }
                difference() {
                    translate([buck_plate[0], buck_plate[1]]) square([buck_plate[2] - buck_plate[0], buck_plate[3] - buck_plate[1]]);
                    translate(case_posts[0]) circle(r = 6);
                }
            }
            // zip ties round the buck, across its width
            for (x = buck_ties, y = [buck[1] - slot[0] - 0.6, buck[3] + 0.6])
                translate([x - slot[1] / 2, y]) square([slot[1], slot[0]]);
        }
        // pads: 1.6 mm under the lid posts
        for (b = bosses) translate([b[0], b[1], pad_t]) cylinder(d = pad_d + 0.01, h = beam_t);
        for (b = bosses) translate([b[0], b[1], -1]) cylinder(d = boss_hole, h = beam_t + 2);
        for (p = post_xy) translate([p[0], p[1], -1]) cylinder(d = 3.4, h = beam_t + 2);
    }
}

module rect(r, grow = 0) {
    translate([r[0] - grow, r[1] - grow]) square([r[2] - r[0] + 2 * grow, r[3] - r[1] + 2 * grow]);
}

module tray_2d() {
    difference() {
        polygon(tray_outline);
        for (b = bosses) translate(b) circle(r = boss_keep);
        for (t = concat(tongues, latches)) rect(t, keep);
        for (p = cross_pins) translate(p) circle(r = 6);      // 3.5 + keep + their unknown height
        translate(disc) circle(r = 8);
        translate([wall_blade[0] - keep - 0.5, tray_outline[0][1] - 1])
            square([wall_blade[2] - wall_blade[0] + 2 * keep + 1, blade_notch + 1]);
        for (p = case_posts) translate(p) circle(r = 5);
    }
}

module zip_slots(x0, x1, y) {           // a pair either side of a part, tie across it
    for (x = [x0 - slot[0] - 0.6, x1 + 0.6]) translate([x, y - slot[1] / 2, -1]) cube([slot[0], slot[1], tray_t + 2]);
}

module standoff(xy, h, hole) {
    translate([xy[0], xy[1], 0]) difference() {
        cylinder(d = standoff_d, h = tray_t + h);
        translate([0, 0, 1]) cylinder(d = hole, h = tray_t + h);
    }
}

module corner_stops(r, h) {             // L-shaped stops holding a part's four corners
    for (cx = [0, 1], cy = [0, 1]) {
        x = cx ? r[2] : r[0];
        y = cy ? r[3] : r[1];
        translate([x - 1.5, y - 1.5, 0]) difference() {
            cube([3, 3, tray_t + h]);
            translate([cx ? -0.01 : 1.5, cy ? -0.01 : 1.5, tray_t]) cube([1.51, 1.51, h + 1]);
        }
    }
}

module tray() {
    difference() {
        linear_extrude(tray_t) tray_2d();
        for (y = card_ties) zip_slots(card[0], card[2], y);
        for (dy = [-4, 4]) translate([cable_tie[0] - slot[1] / 2, cable_tie[1] + dy - slot[0] / 2, -1]) cube([slot[1], slot[0], tray_t + 2]);
    }
    for (p = post_xy) translate([p[0] - post_w / 2, p[1] - post_w / 2, 0]) difference() {
        cube([post_w, post_w, boss_z - post_gap]);
        translate([post_w / 2, post_w / 2, boss_z - post_gap - 10]) cylinder(d = pilot_d, h = 11);
    }
    for (h = pi_holes) standoff([pi[0] + h[0], pi[1] + h[1]], pi_standoff, 2.2);     // M2.5
    for (h = mcp_holes) standoff([mcp[0] + h[0], mcp[1] + h[1]], mcp_standoff, 2.8); // M3
    corner_stops(card, 4);
}

module tray_test() {
    difference() {
        linear_extrude(test_t) tray_2d();
        for (p = post_xy) translate([p[0], p[1], -1]) cylinder(d = 3, h = test_t + 2);   // post marks
    }
}

module part_box(r, z0, h, c) {
    color(c, 0.6) translate([r[0], r[1], z0]) cube([r[2] - r[0], r[3] - r[1], h]);
}

module case_features() {
    color("DimGray", 0.5) {
        translate([-4, raised[1], 0]) cube([raised[0] + 4, 115 - raised[1], raised[2]]);
        for (t = tongues) translate([t[0], t[1], -0.5]) cube([t[2] - t[0], t[3] - t[1], 0.5]);
        for (l = latches) translate([l[0], l[1], 0]) cube([l[2] - l[0], l[3] - l[1], 1.3]);
        for (p = cross_pins) translate([p[0], p[1], 0]) cylinder(d = 7, h = 6);
        translate([disc[0], disc[1], 0]) cylinder(d = 13, h = 1);
        hull() {                                      // the blade, slanting up to the wall
            translate([wall_blade[0], wall_blade[3] - 1, 0]) cube([wall_blade[2] - wall_blade[0], 1, 1]);
            translate([wall_blade[0], wall_blade[1], 20]) cube([wall_blade[2] - wall_blade[0], 1, 1]);
        }
    }
    color("Red", 0.5) {
        for (b = bosses) translate([b[0], b[1], 0]) cylinder(d = 7, h = boss_z);
        for (p = case_posts) translate([p[0], p[1], 0]) cylinder(d = 8, h = 30);
    }
    color("White", 0.8) translate([plug_window[0], plug_window[1], raised[2] - 0.4]) cube([plug_window[2] - plug_window[0], plug_window[3] - plug_window[1], 0.5]);
}

if (part == "tray") tray();
else if (part == "frame") frame();
else if (part == "tray_test") tray_test();
else {
    case_features();
    color("SteelBlue") tray();
    color("Gold") translate([0, 0, boss_z]) frame();
    part_box(perf, tray_t + pi_standoff, 17, "RoyalBlue");                 // Pi + perfboard on its header
    part_box(mcp, tray_t + mcp_standoff, 13, "MediumOrchid");
    part_box(card, tray_t, 10, "LimeGreen");
    for (p = plugs) part_box([p[0], card[3], p[1], card[3] + 38], tray_t + 2, 6, "PaleGreen");
    part_box(buck, boss_z - 12, 12, "DarkOrange");
}
