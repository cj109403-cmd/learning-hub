import AppKit

func buildMenus(delegate: AppDelegate, controller: LearningWindowController) {
    let bar = NSMenu()
    func menu(_ title: String) -> NSMenu {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let submenu = NSMenu(title: title); item.submenu = submenu; bar.addItem(item); return submenu
    }
    @discardableResult
    func item(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String = "", _ target: AnyObject? = nil,
              modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
        entry.target = target; entry.keyEquivalentModifierMask = modifiers; menu.addItem(entry); return entry
    }
    let app = menu("学习台")
    item(app, "关于学习台", #selector(AppDelegate.showAbout), "", delegate)
    app.addItem(.separator())
    item(app, "设置…", #selector(LearningWindowController.showSettings), ",", controller)
    app.addItem(.separator())
    let services = NSMenu(); let serviceItem = NSMenuItem(title: "服务", action: nil, keyEquivalent: "")
    serviceItem.submenu = services; app.addItem(serviceItem); NSApp.servicesMenu = services
    item(app, "隐藏学习台", #selector(NSApplication.hide(_:)), "h")
    item(app, "隐藏其他", #selector(NSApplication.hideOtherApplications(_:)), "h", modifiers: [.command, .option])
    item(app, "全部显示", #selector(NSApplication.unhideAllApplications(_:)))
    app.addItem(.separator())
    item(app, "退出学习台", #selector(NSApplication.terminate(_:)), "q")

    let edit = menu("编辑")
    item(edit, "撤销", Selector(("undo:")), "z")
    item(edit, "重做", Selector(("redo:")), "z", modifiers: [.command, .shift])
    edit.addItem(.separator())
    item(edit, "剪切", #selector(NSText.cut(_:)), "x")
    item(edit, "拷贝", #selector(NSText.copy(_:)), "c")
    item(edit, "粘贴", #selector(NSText.paste(_:)), "v")
    item(edit, "全选", #selector(NSText.selectAll(_:)), "a")

    let view = menu("显示")
    item(view, "重新载入", #selector(LearningWindowController.reloadPage), "r", controller)
    view.addItem(.separator())
    item(view, "放大", #selector(LearningWindowController.zoomIn), "+", controller)
    let equal = item(view, "放大", #selector(LearningWindowController.zoomIn), "=", controller)
    equal.isAlternate = true
    item(view, "缩小", #selector(LearningWindowController.zoomOut), "-", controller)
    item(view, "实际大小", #selector(LearningWindowController.actualSize), "0", controller)

    let window = menu("窗口")
    item(window, "最小化", #selector(NSWindow.performMiniaturize(_:)), "m")
    item(window, "缩放", #selector(NSWindow.performZoom(_:)))
    item(window, "关闭", #selector(NSWindow.performClose(_:)), "w")
    window.addItem(.separator())
    item(window, "前置全部窗口", #selector(NSApplication.arrangeInFront(_:)))
    NSApp.windowsMenu = window

    let help = menu("帮助")
    item(help, "学习台使用说明", #selector(AppDelegate.showHelp), "", delegate)
    item(help, "在访达中显示项目文件夹", #selector(AppDelegate.showProject), "", delegate)
    item(help, "打开日志文件夹", #selector(AppDelegate.showLogs), "", delegate)
    NSApp.helpMenu = help
    NSApp.mainMenu = bar
}
