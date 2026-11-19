import AppKit
import WebKit

final class Bridge: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    weak var controller: LearningWindowController?
    var baseURL: URL?

    private func local(_ url: URL?) -> Bool {
        guard let url, let baseURL else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == baseURL.port
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, local(message.frameInfo.request.url),
              let value = message.body as? [String: Any],
              let type = value["type"] as? String, ["theme", "appearance"].contains(type),
              let mode = value["mode"] as? String, ["light", "dark"].contains(mode),
              let background = value["background"] as? String, background.count <= 128 else { return }
        controller?.setAppearance(mode: mode, background: background)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        // Iframe video navigation is intentionally kept in WebKit. It cannot
        // invoke the native bridge because every message also checks frameInfo.
        if navigationAction.targetFrame?.isMainFrame == false {
            decisionHandler(["https", "about"].contains(url.scheme ?? "") ? .allow : .cancel); return
        }
        if local(url) { decisionHandler(.allow); return }
        if navigationAction.navigationType == .linkActivated, ["http", "https"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["https", "http"].contains(url.scheme ?? ""), !local(url) {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { controller?.loaded() }
    /// macOS may end a background page's content process under memory pressure. The service is
    /// fine; reload the page (unsent input is protected by drafts) instead of restarting Python.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { controller?.contentProcessTerminated() }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard frame.isMainFrame, local(frame.request.url) else { completionHandler(false); return }
        let alert = NSAlert(); alert.messageText = message
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        if let window = controller?.window {
            alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
        } else { completionHandler(false) }
    }
}
