import Foundation
import AppKit
import CoreGraphics

let front = NSWorkspace.shared.frontmostApplication
var value: [String: Any] = [
    "app": front?.localizedName ?? "",
    "bundle_id": front?.bundleIdentifier ?? "",
    "title": "",
    "screen_recording_allowed": CGPreflightScreenCaptureAccess(),
]
if let pid = front?.processIdentifier,
   let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
    for window in windows {
        guard (window[kCGWindowOwnerPID as String] as? Int32) == pid,
              (window[kCGWindowLayer as String] as? Int) == 0 else { continue }
        if let title = window[kCGWindowName as String] as? String, !title.isEmpty {
            value["title"] = title
            break
        }
    }
}
if let data = try? JSONSerialization.data(withJSONObject: value),
   let output = String(data: data, encoding: .utf8) {
    print(output)
}

