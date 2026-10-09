import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import PDFKit

func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        print("{\"status\":\"error\",\"message\":\"JSON oluşturulamadı.\"}")
    }
}

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name, &value) == .success ? value : nil
}

func stringAttribute(_ element: AXUIElement, _ name: CFString) -> String {
    guard let value = attribute(element, name) else { return "" }
    return (value as? String) ?? ""
}

func elementAttribute(_ element: AXUIElement, _ name: CFString) -> AXUIElement? {
    guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID() else {
        return nil
    }
    return (value as! AXUIElement)
}

func parentDocument(_ element: AXUIElement?) -> String {
    var current = element
    for _ in 0..<8 {
        guard let item = current else { break }
        let doc = stringAttribute(item, kAXDocumentAttribute as CFString)
        if !doc.isEmpty { return doc }
        current = elementAttribute(item, kAXParentAttribute as CFString)
    }
    return ""
}

func normal(_ value: String) -> String {
    value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "tr_TR"))
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

func visibleWindowInfo() -> [[String: Any]] {
    guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] else { return [] }
    return windows.filter { window in
        let layer = (window[kCGWindowLayer as String] as? NSNumber)?.intValue ?? -1
        let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 0
        let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
        let width = (bounds["Width"] as? NSNumber)?.doubleValue ?? 0
        let height = (bounds["Height"] as? NSNumber)?.doubleValue ?? 0
        return layer == 0 && alpha > 0.01 && width >= 80 && height >= 60
    }
}

func ownerPID(_ window: [String: Any]) -> pid_t {
    pid_t((window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value ?? 0)
}

func windowName(_ window: [String: Any]) -> String {
    (window[kCGWindowName as String] as? String) ?? ""
}

func isJarvisWindow(_ title: String) -> Bool {
    let compact = title.uppercased().filter { $0.isLetter }
    return compact == "JARVIS"
}

func boolAttribute(_ element: AXUIElement, _ name: String) -> Bool? {
    attribute(element, name as CFString) as? Bool
}

func elementFrame(_ element: AXUIElement) -> CGRect? {
    guard let rawPosition = attribute(element, kAXPositionAttribute as CFString),
          let rawSize = attribute(element, kAXSizeAttribute as CFString),
          CFGetTypeID(rawPosition) == AXValueGetTypeID(), CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
    let position = rawPosition as! AXValue
    let size = rawSize as! AXValue
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position, .cgPoint, &point), AXValueGetValue(size, .cgSize, &dimensions),
          point.x.isFinite, point.y.isFinite, dimensions.width.isFinite, dimensions.height.isFinite,
          dimensions.width > 0, dimensions.height > 0 else { return nil }
    return CGRect(origin: point, size: dimensions)
}

func fieldLabels(_ element: AXUIElement) -> [String] {
    var labels = [stringAttribute(element, kAXTitleAttribute as CFString),
                  stringAttribute(element, kAXDescriptionAttribute as CFString),
                  stringAttribute(element, kAXPlaceholderValueAttribute as CFString)]
    if let label = elementAttribute(element, kAXTitleUIElementAttribute as CFString) {
        labels += [stringAttribute(label, kAXTitleAttribute as CFString),
                   stringAttribute(label, kAXDescriptionAttribute as CFString),
                   stringAttribute(label, kAXValueAttribute as CFString)]
    }
    // A field's current value is content, not a name identifying that field.
    return labels.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}

func secureField(_ element: AXUIElement) -> Bool {
    let role = stringAttribute(element, kAXRoleAttribute as CFString)
    let subrole = stringAttribute(element, kAXSubroleAttribute as CFString)
    if role == "AXSecureTextField" || subrole == "AXSecureTextField"
        || boolAttribute(element, "AXProtectedContent") == true { return true }
    let sensitive = ["password", "passcode", "parola", "sifre", "pin"]
    return fieldLabels(element).contains { label in
        let words = normal(label).components(separatedBy: CharacterSet.alphanumerics.inverted)
        return words.contains { sensitive.contains($0) }
    }
}

func visibleField(_ element: AXUIElement, in root: AXUIElement) -> Bool {
    guard var clipped = elementFrame(element) else { return false }
    var current: AXUIElement? = element
    for _ in 0..<32 {
        guard let item = current else { return false }
        if boolAttribute(item, "AXHidden") == true || boolAttribute(item, "AXVisible") == false
            || boolAttribute(item, "AXMinimized") == true { return false }
        if let frame = elementFrame(item) {
            clipped = clipped.intersection(frame)
            if clipped.isNull || clipped.isEmpty { return false }
        }
        if CFEqual(item, root) { return true }
        current = elementAttribute(item, kAXParentAttribute as CFString)
    }
    return false
}

let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "snapshot"

if mode == "pdf_page" {
    guard args.count > 3, let pageNumber = Int(args[3]), pageNumber > 0 else {
        emit(["status": "error", "message": "Geçerli PDF sayfa numarası gerekli."])
        exit(0)
    }
    let path = args[2]
    guard URL(fileURLWithPath: path).pathExtension.lowercased() == "pdf",
          let pdf = PDFDocument(url: URL(fileURLWithPath: path)) else {
        emit(["status": "error", "message": "Açık PDF okunamadı."])
        exit(0)
    }
    guard pageNumber <= pdf.pageCount, let page = pdf.page(at: pageNumber - 1) else {
        emit(["status": "error", "message": "Sayfa numarası PDF aralığının dışında.", "page_count": pdf.pageCount])
        exit(0)
    }
    let text = page.string ?? ""
    emit(["status": "ok", "page": pageNumber, "page_count": pdf.pageCount,
          "text": String(text.prefix(18000)), "truncated": text.count > 18000])
    exit(0)
}

guard let frontApp = NSWorkspace.shared.frontmostApplication else {
    emit(["status": "unavailable", "message": "Aktif uygulama bulunamadı."])
    exit(0)
}

let callerPID = mode == "snapshot" && args.count > 2 ? pid_t(Int32(args[2]) ?? -1) : -1
let windows = visibleWindowInfo()
let frontWindows = windows.filter { ownerPID($0) == frontApp.processIdentifier }
let ownFrontWindow = frontWindows.contains { isJarvisWindow(windowName($0)) }
let isJarvisFront = (callerPID > 0 && frontApp.processIdentifier == callerPID)
    || ((frontApp.bundleIdentifier == "org.python.python" || frontApp.localizedName == "Python") && ownFrontWindow)
var app = frontApp
var targetSource = "frontmost"
var selectedWindow: [String: Any]? = frontWindows.first
if mode == "snapshot" && isJarvisFront {
    let ignored = Set(["com.apple.dock", "com.apple.controlcenter", "com.apple.notificationcenterui"])
    for window in windows where ownerPID(window) != frontApp.processIdentifier {
        guard let candidate = NSRunningApplication(processIdentifier: ownerPID(window)),
              !ignored.contains(candidate.bundleIdentifier ?? "") else { continue }
        app = candidate
        selectedWindow = window
        targetSource = "behind_jarvis"
        break
    }
    if targetSource == "frontmost" {
        emit(["status": "unavailable", "message": "JARVIS arkasında okunabilecek bir pencere bulunamadı.",
              "app": frontApp.localizedName ?? "", "bundle_id": frontApp.bundleIdentifier ?? "",
              "target_source": "jarvis_only"])
        exit(0)
    }
}

let base: [String: Any] = ["app": app.localizedName ?? "", "bundle_id": app.bundleIdentifier ?? "",
                            "pid": Int(app.processIdentifier), "target_source": targetSource,
                            "frontmost_bundle_id": frontApp.bundleIdentifier ?? "",
                            "window_id": (selectedWindow?[kCGWindowNumber as String] as? NSNumber)?.intValue ?? 0]
guard AXIsProcessTrusted() else {
    if mode == "click_button" || mode == "fill_field" {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }
    emit(base.merging(["status": "permission_required",
                       "message": "Erişilebilirlik izni gerekli: Sistem Ayarları > Gizlilik ve Güvenlik > Erişilebilirlik bölümünde jarvis-context-helper uygulamasına izin verin."]) { _, new in new })
    exit(0)
}

let appElement = AXUIElementCreateApplication(app.processIdentifier)
let focused = elementAttribute(appElement, kAXFocusedUIElementAttribute as CFString)
let window = elementAttribute(appElement, kAXFocusedWindowAttribute as CFString)
let axTitle = window.map { stringAttribute($0, kAXTitleAttribute as CFString) } ?? ""
let title = axTitle.isEmpty ? (selectedWindow.map { windowName($0) } ?? "") : axTitle

if mode == "snapshot" {
    let selected = focused.map { stringAttribute($0, kAXSelectedTextAttribute as CFString) } ?? ""
    let role = focused.map { stringAttribute($0, kAXRoleAttribute as CFString) } ?? ""
    let document = parentDocument(focused).isEmpty ? parentDocument(window) : parentDocument(focused)
    emit(base.merging(["status": "ok", "window_title": title,
                       "focused_role": role, "selected_text": String(selected.prefix(3000)),
                       "document": document]) { _, new in new })
    exit(0)
}

if mode == "fill_field" {
    guard args.count == 8, let root = window,
          let expectedPID = Int32(args[5]), let expectedWindowID = Int(args[6]),
          !args[2].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          args[2].count <= 100, args[3].count <= 2000 else {
        emit(["status": "error", "message": "Alan adı, metin ve doğrulanmış pencere bilgisi gerekli."])
        exit(0)
    }
    let target = normal(args[2])
    let replacement = args[3]
    func sameWindow() -> Bool {
        guard !isJarvisFront, let front = NSWorkspace.shared.frontmostApplication,
              front.processIdentifier == expectedPID, front.bundleIdentifier == args[4],
              let latestWindow = elementAttribute(AXUIElementCreateApplication(expectedPID), kAXFocusedWindowAttribute as CFString),
              CFEqual(root, latestWindow),
              let visible = visibleWindowInfo().first(where: { ownerPID($0) == expectedPID }),
              (visible[kCGWindowNumber as String] as? NSNumber)?.intValue == expectedWindowID else { return false }
        let currentTitle = stringAttribute(latestWindow, kAXTitleAttribute as CFString)
        return (currentTitle.isEmpty ? windowName(visible) : currentTitle) == args[7]
    }
    guard sameWindow() else {
        emit(["status": "error", "message": "Aktif uygulama veya pencere değişti; metin yazılmadı."])
        exit(0)
    }
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var matches: [AXUIElement] = []
    var secureMatches = 0
    var notEditable = 0
    var visited = 0
    var truncated = false
    while !queue.isEmpty && visited < 1500 {
        let (element, depth) = queue.removeFirst()
        visited += 1
        if boolAttribute(element, "AXHidden") == true || boolAttribute(element, "AXVisible") == false { continue }
        let role = stringAttribute(element, kAXRoleAttribute as CFString)
        if ["AXTextField", "AXTextArea", "AXSecureTextField"].contains(role),
           fieldLabels(element).contains(where: { normal($0) == target }), visibleField(element, in: root) {
            if secureField(element) {
                secureMatches += 1
            } else if boolAttribute(element, "AXEnabled") == false {
                notEditable += 1
            } else {
                var settable = DarwinBoolean(false)
                if AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success,
                   settable.boolValue {
                    if !matches.contains(where: { CFEqual($0, element) }) { matches.append(element) }
                } else {
                    notEditable += 1
                }
            }
        }
        if let children = attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement], !children.isEmpty {
            if depth < 24 { for child in children { queue.append((child, depth + 1)) } }
            else { truncated = true }
        }
    }
    if !queue.isEmpty { truncated = true }
    guard !truncated else {
        emit(["status": "error", "message": "Pencere alanları tam taranamadı; tek hedef doğrulanamadığı için yazılmadı."])
        exit(0)
    }
    guard secureMatches == 0 else {
        emit(["status": "error", "message": "Şifre veya güvenli alana bu araçla yazılmaz."])
        exit(0)
    }
    guard matches.count == 1, notEditable == 0 else {
        let message = matches.isEmpty ? (notEditable > 0 ? "Alan erişilebilirlik üzerinden düzenlenemiyor." : "Bu adla görünür, düzenlenebilir alan bulunamadı.")
                                     : "Birden fazla aynı adlı alan var; hedef belirsiz olduğu için yazılmadı."
        emit(["status": "error", "message": message, "matches": matches.count])
        exit(0)
    }
    let field = matches[0]
    var stillSettable = DarwinBoolean(false)
    guard sameWindow(), visibleField(field, in: root), !secureField(field),
          ["AXTextField", "AXTextArea"].contains(stringAttribute(field, kAXRoleAttribute as CFString)),
          boolAttribute(field, "AXEnabled") != false,
          AXUIElementIsAttributeSettable(field, kAXValueAttribute as CFString, &stillSettable) == .success,
          stillSettable.boolValue,
          fieldLabels(field).contains(where: { normal($0) == target }) else {
        emit(["status": "error", "message": "Pencere veya alan değişti; metin yazılmadı."])
        exit(0)
    }
    // AXValue does not synthesize a key press, Return, click, or form submission.
    let code = AXUIElementSetAttributeValue(field, kAXValueAttribute as CFString, replacement as CFString)
    guard code == .success else {
        emit(["status": "needs_review", "message": "Metin yazma işlemi doğrulanamadı; alanı kontrol etmeden tekrar etme.",
              "ax_error": code.rawValue, "submitted": false])
        exit(0)
    }
    var verified = false
    for _ in 0..<5 {
        guard sameWindow(), visibleField(field, in: root), !secureField(field) else { break }
        if let value = attribute(field, kAXValueAttribute as CFString) as? String, value == replacement {
            verified = true
            break
        }
        usleep(40_000)
    }
    emit(base.merging(["status": verified ? "ok" : "needs_review", "verified": verified,
                       "message": verified ? "Alan dolduruldu ve metin doğrulandı; form gönderilmedi."
                                           : "Yazma isteği uygulandı ancak sonuç doğrulanamadı; alanı kontrol et.",
                       "window_title": title, "field": args[2], "submitted": false,
                       "value": verified ? replacement : ""]) { _, new in new })
    exit(0)
}

if mode == "click_button" {
    guard args.count > 2, let root = window else {
        emit(["status": "error", "message": "Aktif pencere veya düğme adı bulunamadı."])
        exit(0)
    }
    let target = normal(args[2])
    if args.count > 3 && !args[3].isEmpty && title != args[3] {
        emit(["status": "error", "message": "Aktif pencere değişti; düğmeye basılmadı."])
        exit(0)
    }
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var matches: [AXUIElement] = []
    var visited = 0
    while !queue.isEmpty && visited < 600 {
        let (element, depth) = queue.removeFirst()
        visited += 1
        let role = stringAttribute(element, kAXRoleAttribute as CFString)
        if role == "AXButton" {
            let names = [stringAttribute(element, kAXTitleAttribute as CFString),
                         stringAttribute(element, kAXDescriptionAttribute as CFString),
                         stringAttribute(element, kAXValueAttribute as CFString)]
            if names.contains(where: { normal($0) == target }) { matches.append(element) }
        }
        if depth < 9, let children = attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] {
            for child in children { queue.append((child, depth + 1)) }
        }
    }
    guard matches.count == 1 else {
        emit(base.merging(["status": "error", "message": matches.isEmpty ? "Düğme bulunamadı." : "Birden fazla aynı adlı düğme var.",
                           "window_title": title, "matches": matches.count]) { _, new in new })
        exit(0)
    }
    let code = AXUIElementPerformAction(matches[0], kAXPressAction as CFString)
    emit(base.merging(["status": code == .success ? "ok" : "error",
                       "message": code == .success ? "Düğmeye basıldı." : "Düğmeye basılamadı.",
                       "window_title": title, "button": args[2]]) { _, new in new })
    exit(0)
}

emit(["status": "error", "message": "Bilinmeyen yardımcı mod."])
