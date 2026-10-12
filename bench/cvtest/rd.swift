import Foundation
import AVFoundation
import CoreVideo
let sem = DispatchSemaphore(value: 0)
Task {
  for path in CommandLine.arguments.dropFirst() {
    for (outLabel, tf) in [("linear", AVVideoTransferFunction_Linear), ("sRGB", AVVideoTransferFunction_IEC_sRGB)] {
      let asset = AVURLAsset(url: URL(fileURLWithPath: path))
      let track = try! await asset.loadTracks(withMediaType: .video).first!
      let reader = try! AVAssetReader(asset: asset)
      let settings: [String: Any] = [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_64RGBAHalf,
        AVVideoColorPropertiesKey: [AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
                                    AVVideoTransferFunctionKey: tf,
                                    AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2]]
      let out = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
      reader.add(out)
      guard reader.startReading() else { print(path, outLabel, "startReading failed", reader.error as Any); continue }
      guard let sb = out.copyNextSampleBuffer(), let pb = CMSampleBufferGetImageBuffer(sb) else { print(path, outLabel, "no frame", reader.error as Any); continue }
      CVPixelBufferLockBaseAddress(pb, .readOnly)
      let base = CVPixelBufferGetBaseAddress(pb)!.assumingMemoryBound(to: Float16.self)
      let stride = CVPixelBufferGetBytesPerRow(pb) / 2
      var s: [String] = []
      for c in [32, 96, 160, 224] { s.append(String(format: "%.5f", Float(base[32 * stride + c * 4 + 1]))) }
      CVPixelBufferUnlockBaseAddress(pb, .readOnly)
      print((path as NSString).lastPathComponent, "->", outLabel, "G at code 0.10,0.18,0.50,0.80:", s.joined(separator: " "))
      reader.cancelReading()
    }
  }
  sem.signal()
}
sem.wait()
