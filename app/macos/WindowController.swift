import AppKit
import WebKit

final class LearningWindowController: NSWindowController, NSWindowDelegate {
    let webView: WKWebView
    let bridge: Bridge
    private let root: URL
    private let loading = NSTextField(labelWithString: "正在打开学习台…")
    private var didLoad = false
    private var systemAppearanceObservation: NSKeyValueObservation?
    var requestQuit: (() -> Void)?

    init(root: URL, ephemeral: Bool) {
        self.root = root
        bridge = Bridge()
        let config = WKWebViewConfiguration()
        if ephemeral { config.websiteDataStore = .nonPersistent() }
        else if #available(macOS 14.0, *) {
            let hash = projectIdentity(root)
            let uuid = UUID(uuidString: "\(hash.prefix(8))-\(hash.dropFirst(8).prefix(4))-4000-8000-000000000001")!
            config.websiteDataStore = WKWebsiteDataStore(forIdentifier: uuid)
        } else { config.websiteDataStore = .nonPersistent() }
        config.userContentController.add(bridge, name: "learningHub")
        let systemDark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let source = "window.__LEARNING_DESKTOP__={platform:'macos'};window.__LEARNING_SYSTEM_DARK__=\(systemDark);if(document.documentElement)document.documentElement.dataset.desktop='macos';"
        config.userContentController.addUserScript(WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        webView = WKWebView(frame: .zero, configuration: config)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 840),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                              backing: .buffered, defer: false)
        window.title = "学习台"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        super.init(window: window)
        window.delegate = self
        let frameName = "LearningHub-" + projectIdentity(root)
        window.setFrameAutosaveName(frameName)
        window.setFrameUsingName(frameName)
        webView.navigationDelegate = bridge
        webView.uiDelegate = bridge
        bridge.controller = self
        webView.translatesAutoresizingMaskIntoConstraints = false
        loading.translatesAutoresizingMaskIntoConstraints = false
        loading.font = .systemFont(ofSize: 15)
        loading.textColor = .secondaryLabelColor
        loading.isHidden = true
        guard let content = window.contentView else { return }
        content.addSubview(webView); content.addSubview(loading)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            loading.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            loading.centerYAnchor.constraint(equalTo: content.centerYAnchor)
        ])
        initialAppearance()
        systemAppearanceObservation = NSApp.observe(\.effectiveAppearance, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.reportSystemAppearance() }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self, !self.didLoad else { return }; self.loading.isHidden = false
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    private func initialAppearance() {
        // This runtime read is limited to UI preferences; the shell never reads
        // learning records or any workspace files.
        let url = root.appendingPathComponent("progress/settings.json")
        let linked = [root.appendingPathComponent("progress"), url].contains {
            (try? $0.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true
        }
        let data = linked ? nil : try? Data(contentsOf: url)
        let settings = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any] ?? [:]
        let systemDark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let dark = settings["mode"] as? String == "dark" || (settings["mode"] as? String != "light" && systemDark)
        let paper = settings["theme"] as? String == "paper"
        let background = paper ? (dark ? "#1A1917" : "#FAF8F3") : (dark ? "#111113" : "#FFFFFF")
        setAppearance(mode: dark ? "dark" : "light", background: background)
    }

    func setAppearance(mode: String, background: String) {
        guard let color = Self.color(background) else { return }
        window?.appearance = NSAppearance(named: mode == "dark" ? .darkAqua : .aqua)
        window?.backgroundColor = color
        webView.underPageBackgroundColor = color
    }

    static func color(_ text: String) -> NSColor? {
        if text.hasPrefix("#"), text.count == 7, let value = UInt32(text.dropFirst(), radix: 16) {
            return NSColor(srgbRed: CGFloat((value >> 16) & 255) / 255,
                           green: CGFloat((value >> 8) & 255) / 255,
                           blue: CGFloat(value & 255) / 255, alpha: 1)
        }
        if text.hasPrefix("rgb("), text.hasSuffix(")") {
            let values = text.dropFirst(4).dropLast().split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
            if values.count == 3, values.allSatisfy({ (0...255).contains($0) }) {
                return NSColor(srgbRed: values[0] / 255, green: values[1] / 255, blue: values[2] / 255, alpha: 1)
            }
        }
        return nil
    }

    func load(_ url: URL) { bridge.baseURL = url; webView.load(URLRequest(url: url)) }
    func loaded() { didLoad = true; loading.isHidden = true; reportSystemAppearance() }
    private func reportSystemAppearance() {
        let dark = NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        webView.evaluateJavaScript("window.__LEARNING_SYSTEM_DARK__=\(dark);window.dispatchEvent(new CustomEvent('learning-system-appearance',{detail:{dark:\(dark)}}));", completionHandler: nil)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { requestQuit?(); return false }

    func flush(completion: @escaping ([String: Any]) -> Void) {
        guard didLoad else { completion(["saved": true, "protected": true, "updating": false]); return }
        var delivered = false
        func deliver(_ result: [String: Any]) { if !delivered { delivered = true; completion(result) } }
        webView.callAsyncJavaScript("return await window.learningHub.flushPending();", arguments: [:], in: nil, in: .page) { result in
            if case let .success(value) = result, let state = value as? [String: Any] { deliver(state) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard !delivered, let self else { return }
            self.webView.evaluateJavaScript("window.learningHub?.protectionState?.()") { value, _ in
                deliver(value as? [String: Any] ?? ["saved": false, "protected": false, "updating": false])
            }
            // A wedged WebContent process must not leave the quit request hung.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                deliver(["saved": false, "protected": false, "updating": false])
            }
        }
    }

    @objc func showSettings() { webView.evaluateJavaScript("window.learningHub?.openSettings?.()", completionHandler: nil) }
    @objc func reloadPage() {
        flush { [weak self] state in
            if state["updating"] as? Bool == true { NSSound.beep(); return }
            if state["saved"] as? Bool == true || state["protected"] as? Bool == true { self?.webView.reload() }
            else { self?.showSaveWarning() }
        }
    }
    @objc func zoomIn() { webView.pageZoom = min(2, webView.pageZoom + 0.1) }
    @objc func zoomOut() { webView.pageZoom = max(0.6, webView.pageZoom - 0.1) }
    @objc func actualSize() { webView.pageZoom = 1 }
    var onContentProcessTerminated: (() -> Void)?
    func contentProcessTerminated() {
        onContentProcessTerminated?()
        webView.reload()
    }
    /// Quiet "正在重新连接……" line in the page while the shell restarts the service.
    func setReconnecting(_ value: Bool) {
        webView.evaluateJavaScript("window.learningHub?.setReconnecting?.(\(value))", completionHandler: nil)
    }
    func reconnect(completion: @escaping (Bool) -> Void) {
        webView.callAsyncJavaScript("return await window.learningHub.reconnect();", arguments: [:], in: nil, in: .page) { result in
            guard case let .success(value) = result, let state = value as? [String: Any] else { completion(false); return }
            completion(state["saved"] as? Bool == true || state["protected"] as? Bool == true)
        }
    }
    func showSaveWarning() {
        let alert = NSAlert()
        alert.messageText = "还有文字没有安全保存"
        alert.informativeText = "正式记录和磁盘草稿都还未确认保存。这次先保留窗口，请稍后再试；也可以先复制文字留存。"
        alert.addButton(withTitle: "留在学习台")
        if let window { alert.beginSheetModal(for: window) }
    }
}
