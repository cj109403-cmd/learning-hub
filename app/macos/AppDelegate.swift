import AppKit
import Darwin

final class AppDelegate: NSObject, NSApplicationDelegate {
    var windowController: LearningWindowController?
    var service: ServiceController?
    private var root: URL?
    private var instanceFD: Int32 = -1
    private var activationName: Notification.Name?
    private var quitPending = false
    private var finished = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let root = try locateProject()
            self.root = root
            if !claimInstance(root) { finished = true; NSApp.terminate(nil); return }
            let ephemeral = CommandLine.arguments.contains("--ephemeral") || CommandLine.arguments.contains("--ephemeral-profile")
            let controller = LearningWindowController(root: root, ephemeral: ephemeral)
            windowController = controller
            controller.requestQuit = { NSApp.terminate(nil) }
            buildMenus(delegate: self, controller: controller)
            controller.showWindow(nil)
            NSApp.activate(ignoringOtherApps: true)
            let service = ServiceController(root: root)
            self.service = service
            service.onUnexpectedExit = { [weak self] in self?.serviceStoppedUnexpectedly() }
            controller.onContentProcessTerminated = { [weak self] in self?.service?.note("page content process was terminated by macOS; reloading the page") }
            service.start { [weak self] result in
                switch result {
                case let .success(url): self?.windowController?.load(url)
                case let .failure(error): self?.showError(error)
                }
            }
        } catch { showError(error) }
    }

    private func isProject(_ url: URL) -> Bool {
        ["app/server/main.py", "app/static/index.html", "content/course.yaml"].allSatisfy {
            var candidate = url
            for part in $0.split(separator: "/") {
                candidate.appendPathComponent(String(part))
                if (try? candidate.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true { return false }
            }
            return FileManager.default.fileExists(atPath: candidate.path)
        }
    }

    private func locateProject() throws -> URL {
        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "--project-root"), index + 1 < args.count {
            let supplied = URL(fileURLWithPath: args[index + 1]).standardizedFileURL.resolvingSymlinksInPath()
            guard isProject(supplied) else { throw DesktopError.message("指定的项目副本不完整。") }
            return supplied
        }
        let adjacent = Bundle.main.bundleURL.deletingLastPathComponent().resolvingSymlinksInPath()
        if isProject(adjacent) {
            UserDefaults.standard.set(adjacent.path, forKey: "projectRoot")
            return adjacent
        }
        if let path = UserDefaults.standard.string(forKey: "projectRoot") {
            let saved = URL(fileURLWithPath: path).resolvingSymlinksInPath()
            if isProject(saved) { return saved }
        }
        let panel = NSOpenPanel()
        panel.message = "请选择 learning-hub 项目文件夹（里面应有 app 和 content 文件夹）。"
        panel.prompt = "使用这个项目"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let choice = panel.url?.resolvingSymlinksInPath() else {
            throw DesktopError.message("还没有选择项目文件夹。下次打开时可以重新选择。")
        }
        guard isProject(choice) else { throw DesktopError.message("选中的文件夹不是完整的学习台项目，请选择解压后的 learning-hub 文件夹。") }
        UserDefaults.standard.set(choice.path, forKey: "projectRoot")
        return choice
    }

    private func claimInstance(_ root: URL) -> Bool {
        let runtime = root.appendingPathComponent("logs/runtime", isDirectory: true)
        for path in [root.appendingPathComponent("logs"), runtime] {
            if (try? path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true { return false }
        }
        do { try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true) }
        catch { showError(error); return false }
        let lock = runtime.appendingPathComponent("macos.lock")
        instanceFD = open(lock.path, O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard instanceFD >= 0 else { showError(DesktopError.message("无法创建应用运行锁，请检查项目文件夹是否可写。")); return false }
        let name = Notification.Name("LearningHub.activate." + projectIdentity(root))
        if flock(instanceFD, LOCK_EX | LOCK_NB) != 0 {
            close(instanceFD); instanceFD = -1
            DistributedNotificationCenter.default().postNotificationName(name, object: nil, userInfo: nil, deliverImmediately: true)
            return false
        }
        activationName = name
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(activateWindow), name: name, object: nil)
        return true
    }

    @objc func activateWindow() {
        windowController?.window?.deminiaturize(nil)
        windowController?.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { activateWindow(); return true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if finished { return .terminateNow }
        if quitPending { return .terminateCancel }
        quitPending = true
        // AppKit must receive terminateLater before any (possibly synchronous)
        // no-page/no-service completion calls reply(toApplicationShouldTerminate:).
        DispatchQueue.main.async { [weak self] in self?.flushAndStop() }
        return .terminateLater
    }

    private func flushAndStop() {
        guard let controller = windowController else { finished = true; NSApp.reply(toApplicationShouldTerminate: true); return }
        controller.flush { [weak self] state in
            guard let self else { return }
            if state["updating"] as? Bool == true {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.flushAndStop() }
                return
            }
            guard state["saved"] as? Bool == true || state["protected"] as? Bool == true else {
                self.quitPending = false
                NSApp.reply(toApplicationShouldTerminate: false)
                controller.showSaveWarning()
                return
            }
            guard let service = self.service else { self.finished = true; NSApp.reply(toApplicationShouldTerminate: true); return }
            service.stop { result in
                switch result {
                case .success: self.finished = true; NSApp.reply(toApplicationShouldTerminate: true)
                case let .failure(error):
                    self.quitPending = false
                    NSApp.reply(toApplicationShouldTerminate: false)
                    self.showError(error)
                }
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if instanceFD >= 0 { flock(instanceFD, LOCK_UN); close(instanceFD) }
        if let activationName { DistributedNotificationCenter.default().removeObserver(self, name: activationName, object: nil) }
    }

    func showError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "学习台暂时无法继续"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "知道了"); alert.addButton(withTitle: "打开日志"); alert.addButton(withTitle: "打开说明")
        let respond: (NSApplication.ModalResponse) -> Void = { [weak self] reply in
            if reply == .alertSecondButtonReturn { self?.showLogs() }
            if reply == .alertThirdButtonReturn { self?.showHelp() }
        }
        if let window = windowController?.window { alert.beginSheetModal(for: window, completionHandler: respond) }
        else { respond(alert.runModal()) }
    }

    private var restartTimes: [Date] = []

    /// Restart the service quietly; only ask after 3 restarts within 10 minutes.
    private func serviceStoppedUnexpectedly() {
        guard let controller = windowController else { return }
        restartTimes = restartTimes.filter { $0.timeIntervalSinceNow > -600 }
        guard restartTimes.count < 3 else { askAfterRepeatedStops(); return }
        restartTimes.append(Date())
        controller.setReconnecting(true)
        service?.start(reusePort: true) { [weak self] result in
            switch result {
            case .success:
                self?.service?.note("service restarted automatically")
                controller.reconnect { protected in
                    controller.setReconnecting(false)
                    if !protected { controller.showSaveWarning() }
                }
            case let .failure(error):
                controller.setReconnecting(false)
                self?.showError(error)
            }
        }
    }

    private func askAfterRepeatedStops() {
        guard let controller = windowController, let window = controller.window else { return }
        let alert = NSAlert()
        alert.messageText = "学习台后台意外停止了"
        alert.informativeText = "10 分钟内已经自动重启了 3 次，这次先停下来问你。窗口里的输入仍保留；可以再试一次，或者打开日志看看原因。"
        alert.addButton(withTitle: "重新启动后台"); alert.addButton(withTitle: "留在页面"); alert.addButton(withTitle: "打开日志")
        alert.beginSheetModal(for: window) { [weak self] reply in
            guard let self else { return }
            if reply == .alertThirdButtonReturn { self.showLogs(); return }
            guard reply == .alertFirstButtonReturn else { return }
            self.service?.start(reusePort: true) { result in
                switch result {
                case .success:
                    controller.reconnect { protected in
                        if !protected { controller.showSaveWarning() }
                    }
                case let .failure(error): self.showError(error)
                }
            }
        }
    }

    @objc func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "学习台", .applicationVersion: "阶段三", .version: "1.0"])
    }
    @objc func showHelp() { if let root { NSWorkspace.shared.open(root.appendingPathComponent("app/README.md")) } }
    @objc func showProject() { if let root { NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: root.path) } }
    @objc func showLogs() { if let root { NSWorkspace.shared.open(root.appendingPathComponent("logs", isDirectory: true)) } }
}
