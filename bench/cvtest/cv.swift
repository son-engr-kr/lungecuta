import Foundation
import CoreVideo
import CoreGraphics

func dump(_ label: String, _ att: [CFString: Any]) {
    guard let cs = CVImageBufferCreateColorSpaceFromAttachments(att as CFDictionary)?.takeRetainedValue() else {
        print(label, "-> nil colorspace"); return
    }
    let name = (cs.name as String?) ?? "(no name)"
    let path = FileManager.default.temporaryDirectory.path  // unused
    _ = path
    if let icc = cs.copyICCData() as Data? {
        let out = CommandLine.arguments[1] + "/" + label + ".icc"
        try! icc.write(to: URL(fileURLWithPath: out))
        print(label, "-> name:", name, "icc bytes:", icc.count, "saved", out)
    } else {
        print(label, "-> name:", name, "no ICC")
    }
    // Convert a few gray code values to linear-light in a linear sRGB space to see the decode
    let lin = CGColorSpace(name: CGColorSpace.extendedLinearSRGB)!
    var row: [String] = []
    for v in [0.1, 0.18, 0.5, 0.8] {
        let c = CGColor(colorSpace: cs, components: [CGFloat(v), CGFloat(v), CGFloat(v), 1])!
        let o = c.converted(to: lin, intent: .relativeColorimetric, options: nil)!
        row.append(String(format: "%.2f->%.5f", v, o.components![1]))
    }
    print("   code->linear(G):", row.joined(separator: "  "))
}
let P = kCVImageBufferColorPrimariesKey, T = kCVImageBufferTransferFunctionKey, M = kCVImageBufferYCbCrMatrixKey
dump("1-1-1", [P: kCVImageBufferColorPrimaries_ITU_R_709_2, T: kCVImageBufferTransferFunction_ITU_R_709_2, M: kCVImageBufferYCbCrMatrix_ITU_R_709_2])
dump("1-2-1_noTF", [P: kCVImageBufferColorPrimaries_ITU_R_709_2, M: kCVImageBufferYCbCrMatrix_ITU_R_709_2])
dump("1-13-1_sRGB", [P: kCVImageBufferColorPrimaries_ITU_R_709_2, T: kCVImageBufferTransferFunction_sRGB, M: kCVImageBufferYCbCrMatrix_ITU_R_709_2])
dump("1-gamma2.4", [P: kCVImageBufferColorPrimaries_ITU_R_709_2, T: kCVImageBufferTransferFunction_UseGamma, kCVImageBufferGammaLevelKey: 2.4, M: kCVImageBufferYCbCrMatrix_ITU_R_709_2])
dump("1-gamma2.2", [P: kCVImageBufferColorPrimaries_ITU_R_709_2, T: kCVImageBufferTransferFunction_UseGamma, kCVImageBufferGammaLevelKey: 2.2, M: kCVImageBufferYCbCrMatrix_ITU_R_709_2])
