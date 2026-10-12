import Foundation
import AVFoundation
import CoreVideo
// Playback path: AVPlayerItemVideoOutput, no color conversion requested -> inspect attachments the player assigns
let paths = Array(CommandLine.arguments.dropFirst())
for path in paths {
  let item = AVPlayerItem(url: URL(fileURLWithPath: path))
  let out = AVPlayerItemVideoOutput(pixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_64RGBAHalf])
  item.add(out)
  let player = AVPlayer(playerItem: item)
  player.isMuted = true
  player.play()
  var got = false
  for _ in 0..<200 {
    RunLoop.current.run(until: Date().addingTimeInterval(0.02))
    let t = item.currentTime()
    if out.hasNewPixelBuffer(forItemTime: t), let pb = out.copyPixelBuffer(forItemTime: t, itemTimeForDisplay: nil) {
      let tf = CVBufferCopyAttachment(pb, kCVImageBufferTransferFunctionKey, nil) as? String ?? "nil"
      let pr = CVBufferCopyAttachment(pb, kCVImageBufferColorPrimariesKey, nil) as? String ?? "nil"
      var csName = "nil"
      if let cs = CVImageBufferCreateColorSpaceFromAttachments(CVBufferCopyAttachments(pb, .shouldPropagate)!)?.takeRetainedValue() { csName = (cs.name as String?) ?? "(unnamed)" }
      CVPixelBufferLockBaseAddress(pb, .readOnly)
      let base = CVPixelBufferGetBaseAddress(pb)!.assumingMemoryBound(to: Float16.self)
      let stride = CVPixelBufferGetBytesPerRow(pb) / 2
      var s: [String] = []
      for c in [32, 96, 160, 224] { s.append(String(format: "%.4f", Float(base[32 * stride + c * 4 + 1]))) }
      CVPixelBufferUnlockBaseAddress(pb, .readOnly)
      print((path as NSString).lastPathComponent, "player buffer TF=\(tf) primaries=\(pr) cs=\(csName) G:", s.joined(separator: " "))
      got = true; break
    }
  }
  if !got { print(path, "no buffer") }
  player.pause()
}
