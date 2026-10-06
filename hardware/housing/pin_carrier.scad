// Holder for 2 x 16 standard male header pins (2.54 mm, 0.64 mm square pins,
// 2.5 mm plastic spacer): two 1 x 16 strips side by side, or four 1 x 8 strips
// laid two end to end per row (the owner's; the floor holes keep the pitch
// across the joint). Plugs straight into the car's 32-way Blue&Me plug (TE MQS,
// 2 x 16 at 2.54 mm), which gets glued or tied on (owner, 2026-09-28: keep it
// simple).
//
// The floor is the bottom of the holder, with a hole for each pin. The strips
// drop in from the top, tails down through the holes, until their spacers sit
// on the floor; pushing the holder onto the plug, the floor pushes the strips
// with it. The walls stand 0.5 mm above the spacers (4.0 mm from the bottom,
// owner), so the plug's face rests on the walls and the pins reach 0.5 mm less
// deep than on the spacers. Wires solder to the tails sticking out under the
// floor; a drop of glue there afterwards.
// No printed marks (they failed on a 0.6 mm nozzle): mark pins 1 and 17 with a
// pen, and put them to the corner of the plug moulded "1" / "17".
//
// Frame: X along the strips from the pin 16/32 end (the thicker end), Y across,
// Z up (mating side up, print this way).

pitch = 2.54;
spacer = [16 * pitch, 2 * pitch, 2.5];   // two rows side by side
fit = [1.1, 0.4];                        // slack round the spacers: 0.5 mm more lengthwise, the
                                         // owner's strips' plastic runs past the end pins
pocket = [spacer[0] + fit[0], spacer[1] + fit[1], spacer[2] + 0.5];   // walls 0.5 mm above the spacers (owner)
wall = 1.0;                              // long sides
end_wall = [2.4, 2.0];                   // pin 16/32 end 0.4 mm thicker than the pin 1/17 end,
                                         // as on the original (4.6 vs 4.2 mm to the nearest pin)
floor_t = 1.0;                           // leaves 2 mm of a standard 3 mm tail below it
tail_hole = 1.4;                         // square holes for 0.64 mm square tails: square for a
                                         // 0.6 mm nozzle, 1.14 mm (two lines) of floor between them
ear = [6, 2, 3.3];                       // length, thickness, M3 hole

outer = [pocket[0] + end_wall[0] + end_wall[1], pocket[1] + 2 * wall, floor_t + pocket[2]];
$fn = 32;

difference() {
    union() {
        cube(outer);
        for (x = [-ear[0], outer[0] - 0.5]) translate([x, 0, 0]) difference() {
            cube([ear[0] + 0.5, outer[1], ear[1]]);                      // 0.5 into the body
            translate([x < 0 ? ear[0] / 2 : ear[0] / 2 + 0.5, outer[1] / 2, -1]) cylinder(d = ear[2], h = ear[1] + 2);
        }
    }
    // pocket for the spacers, down to the floor
    translate([end_wall[0], wall, floor_t]) cube([pocket[0], pocket[1], pocket[2] + 1]);
    // a square hole through the floor for each pin, 2 x 16 round the pocket centre
    for (k = [0:15], row = [-1, 1])
        translate([end_wall[0] + pocket[0] / 2 + (k - 7.5) * pitch, wall + pocket[1] / 2 + row * pitch / 2, -1])
            translate([-tail_hole / 2, -tail_hole / 2, 0]) cube([tail_hole, tail_hole, floor_t + 2]);
}
