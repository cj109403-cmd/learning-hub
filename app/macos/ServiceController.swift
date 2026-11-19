import Foundation
import CryptoKit
import Darwin

enum DesktopError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }
}

func projectIdentity(_ root: URL) -> String {
    // Foundation may shorten /private/var to /var on macOS. Python.resolve()
    // uses realpath; hash that identical spelling for the launch handshake.
    let canonical: String
    if let path = realpath(root.path, nil) { canonical = String(cString: path); free(path) }
    else { canonical = root.path }
    let digest = SHA256.hash(data: Data(canonical.utf8))
    return digest.map { String(format: "%02x", $0) }.joined().prefix(16).description
}

final class ServiceController {
    let root: URL
    let projectID: String
    private(set) var token = UUID().uuidString + UUID().uuidString
    private(set) var process: Process?
    private(set) var ownsProcess = false
    private(set) var baseURL: URL?
    private(set) var logURL: URL?
    private var logHandle: FileHandle?
    private var readyURL: URL?
    private var readyTimer: Timer?
    private var requestBusy = false
    private var receipt: [String: Any]?
    private var expectedStop = false
    var onUnexpectedExit: (() -> Void)?
    private var initialCompletion: ((Result<URL, Error>) -> Void)?
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config)
    }()

    init(root: URL) { self.root = root; self.projectID = projectIdentity(root) }

    /// One line in this launch's desktop log, so every stop and recovery has a written reason.
    func note(_ text: String) {
        guard let logHandle else { return }
        let stamp = ISO8601DateFormatter().string(from: Date())
        logHandle.seekToEndOfFile()
        logHandle.write(Data("\(stamp) shell: \(text)\n".utf8))
    }

    static func python() throws -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        var candidates = [URL(fileURLWithPath: "/usr/bin/python3")] +
            ["miniforge3", "miniconda3", "anaconda3", "mambaforge"].map {
                home.appendingPathComponent($0 + "/envs/learn/bin/python3")
            }
        if let environments = try? String(contentsOf: home.appendingPathComponent(".conda/environments.txt"), encoding: .utf8) {
            for line in environments.split(whereSeparator: \.isNewline) {
                let env = URL(fileURLWithPath: String(line).trimmingCharacters(in: .whitespaces))
                if env.lastPathComponent == "learn" { candidates.append(env.appendingPathComponent("bin/python3")) }
            }
        }
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate.path) {
            let probe = Process()
            probe.executableURL = candidate
            probe.arguments = ["-I", "-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"]
            probe.standardOutput = FileHandle.nullDevice
            probe.standardError = FileHandle.nullDevice
            do { try probe.run(); probe.waitUntilExit(); if probe.terminationStatus == 0 { return candidate } }
            catch { continue }
        }
        throw DesktopError.message("没有找到 Python 3.9 或更高版本。请安装 Apple 命令行工具（xcode-select --install），或创建 conda learn 环境。安装后重新打开学习台。")
    }

    private func prepareLog() throws {
        let fm = FileManager.default
        let folder = root.appendingPathComponent("logs", isDirectory: true)
        for candidate in [folder, folder.appendingPathComponent("runtime")] {
            if (try? candidate.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
                throw DesktopError.message("日志或运行登记文件夹是链接，请改用普通文件夹后重试。")
            }
            try fm.createDirectory(at: candidate, withIntermediateDirectories: true)
        }
        let formatter = DateFormatter(); formatter.dateFormat = "yyyyMMdd-HHmmss"
        let log = folder.appendingPathComponent("desktop-\(formatter.string(from: Date()))-\(UUID().uuidString.prefix(8)).log")
        fm.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600])
        logURL = log
        logHandle = try FileHandle(forWritingTo: log)
        let logs = try fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey, .isSymbolicLinkKey])
            .filter { $0.pathExtension == "log" && ($0.lastPathComponent.hasPrefix("desktop-") || $0.lastPathComponent.hasPrefix("learning-hub-")) && (try? $0.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true }
            .sorted { ((try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) >
                      ((try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) }
        for old in logs.dropFirst(10) { try? fm.removeItem(at: old) }
        readyURL = folder.appendingPathComponent("runtime/launch-\(UUID().uuidString).json")
    }

    func start(reusePort: Bool = false, completion: @escaping (Result<URL, Error>) -> Void) {
        do {
            let priorPort = reusePort ? baseURL?.port : nil
            baseURL = nil; ownsProcess = false; expectedStop = false
            token = UUID().uuidString + UUID().uuidString
            try? logHandle?.close()
            try prepareLog()
            let python = try Self.python()
            let child = Process()
            child.executableURL = python
            // The service follows this window process instead of page heartbeats: macOS pauses
            // hidden pages, and a paused page must not shut the service down.
            child.arguments = ["-B", "-m", "app.server.main", "--root", root.path, "--desktop", "--ready-file", readyURL!.path, "--token", token,
                               "--parent-pid", String(getpid())]
            if let priorPort { child.arguments?.append(contentsOf: ["--port", String(priorPort)]) }
            child.currentDirectoryURL = root
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = logHandle
            child.standardError = logHandle
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment.removeValue(forKey: "PYTHONPATH")
            environment.removeValue(forKey: "PYTHONHOME")
            child.environment = environment
            child.terminationHandler = { [weak self] ended in
                DispatchQueue.main.async {
                    guard let self, self.baseURL != nil, self.ownsProcess, !self.expectedStop else { return }
                    self.note("service exited unexpectedly (status \(ended.terminationStatus), reason \(ended.terminationReason == .uncaughtSignal ? "signal" : "exit"))")
                    self.onUnexpectedExit?()
                }
            }
            try child.run()
            process = child
            initialCompletion = completion
            let deadline = Date().addingTimeInterval(20)
            readyTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                self?.pollReady(deadline: deadline)
            }
            pollReady(deadline: deadline)
        } catch { completion(.failure(error)) }
    }

    private func finishStart(_ result: Result<URL, Error>) {
        readyTimer?.invalidate(); readyTimer = nil
        if let readyURL { try? FileManager.default.removeItem(at: readyURL) }
        let callback = initialCompletion; initialCompletion = nil
        callback?(result)
    }

    private func pollReady(deadline: Date) {
        guard initialCompletion != nil, !requestBusy else { return }
        if Date() > deadline {
            finishStart(.failure(DesktopError.message("后台启动超过 20 秒仍未就绪，可能正在恢复上次中断的内容更新。为保护文件，本次没有强制结束它；请打开日志查看原因，稍后再试。")))
            return
        }
        if let readyURL, (try? readyURL.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
            finishStart(.failure(DesktopError.message("后台运行登记不能使用链接。"))); return
        }
        guard let readyURL, let bytes = try? Data(contentsOf: readyURL),
              let ready = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else {
            if let process, !process.isRunning, process.terminationStatus != 0 {
                finishStart(.failure(DesktopError.message("后台没有成功启动，可能本机端口都被占用。请打开日志查看原因。")))
            }
            return
        }
        guard let port = ready["port"] as? Int, (8765...8775).contains(port),
              ready["project_id"] as? String == projectID,
              let reportedURL = ready["url"] as? String,
              reportedURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == "http://127.0.0.1:\(port)",
              let url = URL(string: "http://127.0.0.1:\(port)"),
              let pid = ready["pid"] as? Int, pid > 0,
              let instance = ready["instance_id"] as? String, !instance.isEmpty else {
            finishStart(.failure(DesktopError.message("后台身份信息不匹配，已停止连接。请打开日志查看原因。")))
            return
        }
        requestBusy = true
        request(url.appendingPathComponent("api/health")) { [weak self] result in
            guard let self else { return }; self.requestBusy = false
            guard self.initialCompletion != nil else { return }
            guard case let .success((status, health)) = result, status == 200,
                  health["ok"] as? Bool == true, health["stopping"] as? Bool != true,
                  health["project_id"] as? String == self.projectID,
                  health["instance_id"] as? String == instance,
                  health["pid"] as? Int == pid else { return }
            self.receipt = ready; self.baseURL = url
            self.ownsProcess = ready["existing"] as? Bool != true && ready["token"] as? String == self.token &&
                ready["pid"] as? Int == Int(self.process?.processIdentifier ?? -1)
            self.finishStart(.success(url))
        }
    }

    private func request(_ url: URL, post: Bool = false, completion: @escaping (Result<(Int, [String: Any]), Error>) -> Void) {
        var request = URLRequest(url: url)
        if post {
            request.httpMethod = "POST"; request.httpBody = Data("{}".utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(token, forHTTPHeaderField: "X-Learning-Control")
        }
        session.dataTask(with: request) { data, response, error in
            DispatchQueue.main.async {
                if let error { completion(.failure(error)); return }
                guard let data, let response = response as? HTTPURLResponse,
                      let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                    completion(.failure(DesktopError.message("后台没有返回有效的状态。"))); return
                }
                completion(.success((response.statusCode, object)))
            }
        }.resume()
    }

    func stop(completion: @escaping (Result<Void, Error>) -> Void) {
        if let child = process, child.isRunning, baseURL == nil {
            if initialCompletion != nil {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.stop(completion: completion) }
            } else {
                completion(.failure(DesktopError.message("后台仍在启动或恢复内容，暂时不能确认安全退出。请稍后再试；本次没有强制结束后台。")))
            }
            return
        }
        guard ownsProcess, let child = process, child.isRunning, let baseURL else { completion(.success(())); return }
        expectedStop = true
        request(baseURL.appendingPathComponent("api/shutdown"), post: true) { [weak self] result in
            guard let self else { return }
            switch result {
            case let .success((status, reply)) where status == 409 && reply["updating"] as? Bool == true:
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.stop(completion: completion) }
            case let .success((status, _)) where status == 200:
                let deadline = Date().addingTimeInterval(2)
                self.waitForExit(child, deadline: deadline, completion: completion)
            case .failure where !child.isRunning:
                completion(.success(()))
            default:
                // An unacknowledged shutdown could still be an active update;
                // do not kill it merely because the HTTP request timed out.
                self.expectedStop = false
                completion(.failure(DesktopError.message("暂时无法确认后台已安全收尾，窗口会保留。请稍后再退出；必要时打开日志查看原因。")))
            }
        }
    }

    private func waitForExit(_ child: Process, deadline: Date, completion: @escaping (Result<Void, Error>) -> Void) {
        if !child.isRunning { completion(.success(())); return }
        if Date() >= deadline {
            // This is our Process object, after an authenticated shutdown ACK.
            // Never select processes by port, executable name or stale pid file.
            kill(child.processIdentifier, SIGKILL)
            child.waitUntilExit()
            completion(.success(())); return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            self.waitForExit(child, deadline: deadline, completion: completion)
        }
    }
}
