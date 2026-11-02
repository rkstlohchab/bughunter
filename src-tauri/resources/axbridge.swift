// BugHunter AXBridge — native, foreground-app-only Accessibility adapter.
// Compile on macOS with scripts/build_mac_axbridge.sh. Never opens sockets or a shell.
// Usage: axbridge <bundle-id> <read_ui|click_button|click_tab|select_item|focus_field|click_menu> [exact visible label]
import Foundation
import AppKit
import ApplicationServices

func fail(_ message: String) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: ["error": message], options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data([10]))
    exit(1)
}
func emit(_ payload: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data([10]))
}
func attribute(_ element: AXUIElement, _ key: CFString) -> AnyObject? {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, key, &raw) == .success else { return nil }
    return raw as AnyObject?
}
// CoreFoundation references do not support meaningful conditional downcasts.
// Verify the actual CFTypeID before treating an attribute as an AXUIElement.
func elementAttribute(_ element: AXUIElement, _ key: CFString) -> AXUIElement? {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, key, &raw) == .success,
          let object = raw,
          CFGetTypeID(object) == AXUIElementGetTypeID() else { return nil }
    // Safe after checking that the value really is an AXUIElement reference.
    return unsafeBitCast(object, to: AXUIElement.self)
}
func label(_ element: AXUIElement) -> String {
    for key in [kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute] {
        if let string = attribute(element, key as CFString) as? String, !string.isEmpty { return string }
    }
    return ""
}
func role(_ element: AXUIElement) -> String {
    attribute(element, kAXRoleAttribute as CFString) as? String ?? ""
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    // AXChildren is safe to request. Most custom-rendered apps have no AX children.
    return attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
}
func enabled(_ element: AXUIElement) -> Bool {
    attribute(element, kAXEnabledAttribute as CFString) as? Bool ?? true
}
func protected(_ element: AXUIElement) -> Bool {
    let r = role(element).lowercased(), l = label(element).lowercased()
    if r.contains("secure") || r.contains("password") { return true }
    return ["password", "secret", "token", "api key", "one-time", "otp", "credit card", "recovery code"].contains(where: l.contains)
}
func classify(_ element: AXUIElement) -> String? {
    let r = role(element)
    switch r {
    case "AXButton", "AXCheckBox", "AXPopUpButton": return "Button"
    case "AXRadioButton", "AXTab": return "Tab"
    case "AXRow", "AXCell", "AXOutlineRow": return "Item"
    case "AXTextField", "AXTextArea", "AXComboBox": return protected(element) ? nil : "Text field label"
    default: return nil
    }
}
struct Entry { let element: AXUIElement; let kind: String; let name: String }
func walk(_ node: AXUIElement, maxNodes: Int = 500) -> [Entry] {
    var queue: [(AXUIElement, Int)] = [(node, 0)]
    var cursor = 0
    var found: [Entry] = []
    while cursor < queue.count && cursor < maxNodes {
        let (element, depth) = queue[cursor]; cursor += 1
        if let kind = classify(element) {
            let name = label(element)
            if !name.isEmpty && name.count < 180 { found.append(Entry(element: element, kind: kind, name: name)) }
        }
        if depth < 12 && queue.count < maxNodes {
            for item in children(element).prefix(90) { queue.append((item, depth + 1)) }
        }
    }
    return found
}
let args = CommandLine.arguments
if args.count < 3 { fail("Expected bundle id and action.") }
let bid = args[1], action = args[2], requested = args.count > 3 ? args[3] : ""
if bid.isEmpty || bid.count > 220 || requested.count > 180 { fail("Invalid app identity or control label.") }
if !AXIsProcessTrusted() { fail("Accessibility permission is missing; enable it in System Settings.") }
let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bid)
guard let target = apps.first(where: { $0.isActive }) else { fail("Selected app is not frontmost. No action executed.") }
let app = AXUIElementCreateApplication(target.processIdentifier)
AXUIElementSetMessagingTimeout(app, 1.5)
guard let window = elementAttribute(app, kAXFocusedWindowAttribute as CFString) else { fail("No accessible focused window.") }
AXUIElementSetMessagingTimeout(window, 1.5)
let entries = walk(window)
if action == "read_ui" {
    var lines: [String] = ["Window: " + String(label(window).prefix(150))]
    for item in entries.prefix(120) { lines.append("\(item.kind): \(item.name)") }
    // Include exact menu labels when exposed, but never execute menu items in a read.
    if let menu = elementAttribute(app, kAXMenuBarAttribute as CFString) {
        let bars = children(menu)
        for bar in bars.prefix(15) {
            let head = label(bar)
            for submenu in children(bar) {
                for item in children(submenu).prefix(30) {
                    let text = label(item)
                    if !head.isEmpty && !text.isEmpty { lines.append("Menu item: \(head) > \(text)") }
                }
            }
        }
    }
    emit(["observation": lines.joined(separator: "\n"), "adapter": "AXUIElement", "controls": entries.count])
    exit(0)
}
let desired: String
switch action {
case "click_button": desired = "Button"
case "click_tab": desired = "Tab"
case "select_item": desired = "Item"
case "focus_field": desired = "Text field label"
default: fail("Unsupported native action; use the existing Mac agent for this action.")
}
let matches = entries.filter { $0.kind == desired && $0.name == requested }
if matches.isEmpty { fail("The requested \(desired.lowercased()) is not visible. No action executed.") }
if matches.count > 1 { fail("There are multiple controls with that label. No action executed.") }
let item = matches[0]
if !enabled(item.element) || protected(item.element) { fail("This control is disabled or protected.") }
let axAction: CFString = kAXPressAction as CFString
let result = AXUIElementPerformAction(item.element, axAction)
if result != .success { fail("macOS rejected the action (AX error \(result.rawValue)).") }
emit(["receipt": "Accessibility action sent to selected foreground app", "adapter": "AXUIElement", "outcome_verified": false])
