import Foundation
import CoreGraphics
let lin = CGColorSpace(name: CGColorSpace.extendedLinearSRGB)!
for (label, n) in [("itur_709", CGColorSpace.itur_709), ("sRGB", CGColorSpace.sRGB), ("displayP3", CGColorSpace.displayP3), ("itur_2020", CGColorSpace.itur_2020)] {
    let cs = CGColorSpace(name: n)!
    if let icc = cs.copyICCData() as Data? { try! icc.write(to: URL(fileURLWithPath: CommandLine.arguments[1] + "/named_" + label + ".icc")) }
    var row: [String] = []
    for v in [0.05, 0.18, 0.5] {
        let o = CGColor(colorSpace: cs, components: [CGFloat(v), CGFloat(v), CGFloat(v), 1])!.converted(to: lin, intent: .relativeColorimetric, options: nil)!
        row.append(String(format: "%.2f->%.5f", v, o.components![1]))
    }
    print(label, row.joined(separator: "  "))
}
