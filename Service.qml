import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var shell: null

  readonly property string helper: {
    var url = Qt.resolvedUrl("collect.py").toString()
    return url.indexOf("file://") === 0 ? decodeURIComponent(url.substring(7)) : url
  }

  property bool announced: false

  function refresh() {
    if (collectProcess.running)
      return
    collectProcess.command = ["python3", helper]
    collectProcess.running = true
  }

  Timer {
    interval: 300000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: collectProcess
    running: false
    onExited: function(exitCode) {
      if (exitCode !== 0 || root.announced)
        return
      root.announced = true
      if (!notifyProcess.running)
        notifyProcess.running = true
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("cursor.usage", text.trim())
    }
  }

  Process {
    id: notifyProcess
    running: false
    command: ["omarchy-shell", "-q", "omarchy.agents", "refresh"]
  }
}
