import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15

Rectangle {
    id: root
    color: "#040814"
    clip: true

    property string statusLabel: displayModel ? displayModel.statusText : "状态: 未连接"
    property string loweredStatus: statusLabel.toLowerCase()
    property bool listeningState: statusLabel.indexOf("聆听") !== -1 || loweredStatus.indexOf("listening") !== -1
    property bool speakingState: statusLabel.indexOf("说话") !== -1 || loweredStatus.indexOf("speaking") !== -1
    property bool idleState: statusLabel.indexOf("待命") !== -1 || loweredStatus.indexOf("idle") !== -1
    property bool disconnectedState: statusLabel.indexOf("未连接") !== -1 || loweredStatus.indexOf("offline") !== -1
    property bool audioReactive: listeningState || speakingState
    property color accentColor: speakingState ? "#ff8a5b" : (listeningState ? "#26d8c7" : (disconnectedState ? "#b785ff" : "#68a8ff"))
    property color accentSoftColor: speakingState ? "#ffd0b3" : (listeningState ? "#9bf3e8" : (disconnectedState ? "#dfcbff" : "#c4dcff"))
    property color deepAccentColor: speakingState ? "#341922" : (listeningState ? "#102b35" : (disconnectedState ? "#25153a" : "#112544"))
    property string statusCapsuleLabel: speakingState ? "VOICE OUTPUT" : (listeningState ? "VOICE INPUT" : (disconnectedState ? "OFFLINE" : "STANDBY"))

    signal manualButtonPressed()
    signal manualButtonReleased()
    signal autoButtonClicked()
    signal abortButtonClicked()
    signal modeButtonClicked()
    signal sendButtonClicked(string text)
    signal settingsButtonClicked()
    signal titleMinimize()
    signal titleClose()
    signal titleDragStart(real mouseX, real mouseY)
    signal titleDragMoveTo(real mouseX, real mouseY)
    signal titleDragEnd()

    Connections {
        target: displayModel
        ignoreUnknownSignals: true

        onTtsTextChanged: {
            transcriptFlash.stop()
            transcriptFlash.start()
        }

        onEmotionPathChanged: {
            emotionPop.stop()
            emotionPop.start()
        }

        onStatusTextChanged: {
            statusFlash.stop()
            statusFlash.start()
        }
    }

    SequentialAnimation {
        id: transcriptFlash
        NumberAnimation { target: transcriptCard; property: "scale"; to: 0.985; duration: 90; easing.type: Easing.OutCubic }
        NumberAnimation { target: transcriptCard; property: "scale"; to: 1.0; duration: 180; easing.type: Easing.OutBack }
    }

    SequentialAnimation {
        id: emotionPop
        NumberAnimation { target: coreShell; property: "scale"; to: 0.92; duration: 100; easing.type: Easing.OutCubic }
        NumberAnimation { target: coreShell; property: "scale"; to: 1.03; duration: 220; easing.type: Easing.OutBack }
        NumberAnimation { target: coreShell; property: "scale"; to: 1.0; duration: 140; easing.type: Easing.InOutCubic }
    }

    SequentialAnimation {
        id: statusFlash
        NumberAnimation { target: statusGlow; property: "opacity"; to: 0.55; duration: 160; easing.type: Easing.OutCubic }
        NumberAnimation { target: statusGlow; property: "opacity"; to: 0.18; duration: 360; easing.type: Easing.InOutQuad }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#020611" }
            GradientStop { position: 0.28; color: "#071226" }
            GradientStop { position: 0.72; color: "#091937" }
            GradientStop { position: 1.0; color: "#030611" }
        }
    }

    Item {
        anchors.fill: parent

        Rectangle {
            id: nebulaLeft
            width: parent.width * 0.68
            height: width
            radius: width / 2
            x: -width * 0.22
            y: parent.height * 0.08
            color: speakingState ? "#7a2237" : "#0d3f60"
            opacity: speakingState ? 0.22 : 0.17
            scale: 1.0
            SequentialAnimation on x {
                loops: Animation.Infinite
                NumberAnimation { to: -nebulaLeft.width * 0.10; duration: 12000; easing.type: Easing.InOutSine }
                NumberAnimation { to: -nebulaLeft.width * 0.24; duration: 12000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on y {
                loops: Animation.Infinite
                NumberAnimation { to: root.height * 0.02; duration: 9000; easing.type: Easing.InOutSine }
                NumberAnimation { to: root.height * 0.12; duration: 9000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on scale {
                loops: Animation.Infinite
                NumberAnimation { to: 1.12; duration: 10000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.96; duration: 10000; easing.type: Easing.InOutSine }
            }
        }

        Rectangle {
            id: nebulaRight
            width: parent.width * 0.52
            height: width
            radius: width / 2
            x: parent.width - width * 0.72
            y: parent.height * 0.46
            color: listeningState ? "#0c7d7f" : "#2f2e78"
            opacity: listeningState ? 0.16 : 0.12
            scale: 1.0
            SequentialAnimation on x {
                loops: Animation.Infinite
                NumberAnimation { to: root.width - nebulaRight.width * 0.58; duration: 15000; easing.type: Easing.InOutSine }
                NumberAnimation { to: root.width - nebulaRight.width * 0.80; duration: 15000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on y {
                loops: Animation.Infinite
                NumberAnimation { to: root.height * 0.38; duration: 11000; easing.type: Easing.InOutSine }
                NumberAnimation { to: root.height * 0.54; duration: 11000; easing.type: Easing.InOutSine }
            }
        }

        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: "#0d1830"
            border.width: 1
            opacity: 0.65
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: 0

        Rectangle {
            id: titleBar
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            color: "#050a14"
            border.color: "#13233f"
            border.width: 1

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: root.accentColor
                opacity: 0.25
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                z: 0
                onPressed: root.titleDragStart(mouse.x, mouse.y)
                onPositionChanged: {
                    if (pressed) {
                        root.titleDragMoveTo(mouse.x, mouse.y)
                    }
                }
                onReleased: root.titleDragEnd()
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 10
                spacing: 10
                z: 1

                Rectangle {
                    width: 10
                    height: 10
                    radius: 5
                    color: root.accentColor
                    opacity: 0.95
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        running: root.audioReactive
                        NumberAnimation { to: 0.35; duration: 420; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 0.95; duration: 420; easing.type: Easing.InOutSine }
                    }
                }

                Column {
                    spacing: 0
                    Layout.fillWidth: true

                    Text {
                        text: "AI Agent Console"
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: "#d8ebff"
                    }

                    Text {
                        text: root.statusCapsuleLabel
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 10
                        color: root.accentSoftColor
                        opacity: 0.72
                    }
                }

                Rectangle {
                    id: btnMin
                    width: 28
                    height: 28
                    radius: 8
                    color: btnMinMouse.pressed ? "#18325e" : (btnMinMouse.containsMouse ? "#102342" : "transparent")
                    border.color: btnMinMouse.containsMouse ? "#325f9d" : "#1a2a45"
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "–"
                        font.pixelSize: 15
                        color: "#8fb7df"
                    }

                    MouseArea {
                        id: btnMinMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: root.titleMinimize()
                    }
                }

                Rectangle {
                    id: btnClose
                    width: 28
                    height: 28
                    radius: 8
                    color: btnCloseMouse.pressed ? "#a62c41" : (btnCloseMouse.containsMouse ? "#ff5c7c" : "transparent")
                    border.color: btnCloseMouse.containsMouse ? "#ff92a6" : "#3b2231"
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "×"
                        font.pixelSize: 15
                        color: btnCloseMouse.containsMouse ? "#ffffff" : "#8fb7df"
                    }

                    MouseArea {
                        id: btnCloseMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: root.titleClose()
                    }
                }
            }
        }

        Rectangle {
            id: mainPanel
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "transparent"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 14

                Rectangle {
                    id: statusStrip
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    radius: 18
                    color: "#071121"
                    border.color: root.accentColor
                    border.width: 1

                    Rectangle {
                        id: statusGlow
                        anchors.fill: parent
                        radius: parent.radius
                        color: root.accentColor
                        opacity: 0.18
                    }

                    Rectangle {
                        width: parent.width * 0.34
                        height: parent.height * 1.5
                        radius: width / 2
                        color: root.accentSoftColor
                        opacity: 0.10
                        rotation: -14
                        y: -height * 0.24
                        x: -width
                        SequentialAnimation on x {
                            loops: Animation.Infinite
                            NumberAnimation { to: statusStrip.width; duration: 4200; easing.type: Easing.InOutQuad }
                            PauseAnimation { duration: 600 }
                            NumberAnimation { to: -width; duration: 10 }
                            PauseAnimation { duration: 1800 }
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 18
                        anchors.rightMargin: 18
                        spacing: 14

                        Rectangle {
                            width: 12
                            height: 12
                            radius: 6
                            color: root.accentColor
                            opacity: 0.95
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                text: root.statusCapsuleLabel
                                font.family: "PingFang SC, Microsoft YaHei UI"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: root.accentSoftColor
                            }

                            Text {
                                text: root.statusLabel
                                font.family: "PingFang SC, Microsoft YaHei UI"
                                font.pixelSize: 16
                                font.weight: Font.Bold
                                color: "#eff6ff"
                            }
                        }

                        Row {
                            spacing: 6
                            Layout.alignment: Qt.AlignVCenter

                            Repeater {
                                model: 5
                                delegate: Rectangle {
                                    width: 5
                                    height: root.audioReactive ? 12 + (index % 3) * 6 : 8
                                    radius: 2.5
                                    color: root.accentColor
                                    opacity: root.audioReactive ? 0.9 : 0.35
                                    SequentialAnimation on height {
                                        loops: Animation.Infinite
                                        running: root.audioReactive
                                        PauseAnimation { duration: index * 80 }
                                        NumberAnimation { to: root.speakingState ? 26 : 22; duration: 260; easing.type: Easing.InOutSine }
                                        NumberAnimation { to: 10 + ((index + 1) % 3) * 4; duration: 320; easing.type: Easing.InOutSine }
                                    }
                                    Behavior on opacity { NumberAnimation { duration: 180 } }
                                }
                            }
                        }
                    }
                }

                Item {
                    id: stage
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 240

                    Item {
                        id: centerStage
                        anchors.centerIn: parent
                        width: Math.min(parent.width, parent.height) * 0.92
                        height: width

                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width
                            height: width
                            radius: width / 2
                            color: root.accentColor
                            opacity: root.audioReactive ? 0.12 : 0.07
                            scale: 0.86
                            SequentialAnimation on scale {
                                loops: Animation.Infinite
                                NumberAnimation { to: root.audioReactive ? 1.02 : 0.92; duration: 2200; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 0.84; duration: 2200; easing.type: Easing.InOutSine }
                            }
                            SequentialAnimation on opacity {
                                loops: Animation.Infinite
                                NumberAnimation { to: root.audioReactive ? 0.18 : 0.11; duration: 2200; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 0.05; duration: 2200; easing.type: Easing.InOutSine }
                            }
                        }

                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width * 0.82
                            height: width
                            radius: width / 2
                            color: "transparent"
                            border.color: root.accentColor
                            border.width: 1
                            opacity: 0.42
                        }

                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width * 0.58
                            height: width
                            radius: width / 2
                            color: "transparent"
                            border.color: root.accentSoftColor
                            border.width: 1
                            opacity: 0.24
                        }

                        Item {
                            width: parent.width * 0.86
                            height: width
                            anchors.centerIn: parent
                            rotation: 0

                            NumberAnimation on rotation {
                                from: 0
                                to: 360
                                duration: root.speakingState ? 5200 : (root.listeningState ? 6800 : 11000)
                                loops: Animation.Infinite
                                running: true
                            }

                            Rectangle {
                                width: 12
                                height: 12
                                radius: 6
                                color: root.accentColor
                                anchors.top: parent.top
                                anchors.horizontalCenter: parent.horizontalCenter
                            }

                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: "#ffffff"
                                opacity: 0.75
                                anchors.bottom: parent.bottom
                                anchors.horizontalCenter: parent.horizontalCenter
                            }
                        }

                        Item {
                            width: parent.width * 0.70
                            height: width
                            anchors.centerIn: parent
                            rotation: 360

                            NumberAnimation on rotation {
                                from: 360
                                to: 0
                                duration: root.speakingState ? 4400 : (root.listeningState ? 7800 : 13000)
                                loops: Animation.Infinite
                                running: true
                            }

                            Rectangle {
                                width: 10
                                height: 10
                                radius: 5
                                color: root.accentSoftColor
                                opacity: 0.85
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Rectangle {
                                width: 10
                                height: 10
                                radius: 5
                                color: root.accentSoftColor
                                opacity: 0.55
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        Rectangle {
                            id: coreShell
                            anchors.centerIn: parent
                            width: parent.width * 0.52
                            height: width
                            radius: width / 2
                            border.color: root.accentColor
                            border.width: 1
                            gradient: Gradient {
                                GradientStop { position: 0.0; color: Qt.lighter(root.deepAccentColor, 1.25) }
                                GradientStop { position: 0.55; color: root.deepAccentColor }
                                GradientStop { position: 1.0; color: "#040916" }
                            }

                            Rectangle {
                                anchors.centerIn: parent
                                width: parent.width * 0.82
                                height: width
                                radius: width / 2
                                color: "#06111f"
                                border.color: root.accentSoftColor
                                border.width: 1
                                opacity: 0.92
                            }

                            Loader {
                                id: emotionLoader
                                anchors.centerIn: parent
                                property real maxSize: Math.max(parent.width * 0.72, 88)
                                width: maxSize
                                height: maxSize
                                scale: 1.0

                                sourceComponent: {
                                    var path = displayModel ? displayModel.emotionPath : ""
                                    if (!path || path.length === 0) {
                                        return emojiComponent
                                    }
                                    if (path.indexOf(".gif") !== -1) {
                                        return gifComponent
                                    }
                                    if (path.indexOf(".") !== -1) {
                                        return imageComponent
                                    }
                                    return emojiComponent
                                }

                                Component {
                                    id: gifComponent
                                    AnimatedImage {
                                        fillMode: Image.PreserveAspectCrop
                                        source: displayModel ? displayModel.emotionPath : ""
                                        playing: true
                                        speed: root.speakingState ? 1.12 : 1.0
                                        cache: true
                                        clip: true
                                    }
                                }

                                Component {
                                    id: imageComponent
                                    Image {
                                        fillMode: Image.PreserveAspectCrop
                                        source: displayModel ? displayModel.emotionPath : ""
                                        cache: true
                                        clip: true
                                    }
                                }

                                Component {
                                    id: emojiComponent
                                    Text {
                                        text: displayModel ? displayModel.emotionPath : "😊"
                                        font.pixelSize: 104
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                }
                            }
                        }

                        Column {
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 8
                            spacing: 8

                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: root.speakingState ? "RESPONDING" : (root.listeningState ? "LISTENING" : "READY")
                                font.family: "PingFang SC, Microsoft YaHei UI"
                                font.pixelSize: 11
                                color: root.accentSoftColor
                                opacity: 0.9
                            }

                            Row {
                                anchors.horizontalCenter: parent.horizontalCenter
                                spacing: 8

                                Repeater {
                                    model: 7
                                    delegate: Rectangle {
                                        width: 7
                                        height: 10 + (index % 3) * 5
                                        radius: 3.5
                                        color: root.accentColor
                                        opacity: root.audioReactive ? 0.9 : 0.30
                                        SequentialAnimation on height {
                                            loops: Animation.Infinite
                                            running: root.audioReactive
                                            PauseAnimation { duration: index * 70 }
                                            NumberAnimation { to: root.speakingState ? 44 : 32; duration: 260; easing.type: Easing.InOutSine }
                                            NumberAnimation { to: 12 + ((index + 1) % 3) * 6; duration: 340; easing.type: Easing.InOutSine }
                                        }
                                        Behavior on opacity { NumberAnimation { duration: 180 } }
                                    }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    id: transcriptCard
                    Layout.fillWidth: true
                    Layout.preferredHeight: 184
                    radius: 20
                    color: "#07101e"
                    border.color: root.accentColor
                    border.width: 1
                    scale: 1.0

                    Rectangle {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: 4
                        radius: 2
                        color: root.accentColor
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 18
                        anchors.rightMargin: 18
                        anchors.topMargin: 14
                        anchors.bottomMargin: 14
                        spacing: 8

                        Text {
                            text: "LIVE TRANSCRIPT"
                            font.family: "PingFang SC, Microsoft YaHei UI"
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                            color: root.accentSoftColor
                        }

                        ScrollView {
                            id: transcriptScroll
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true

                            ScrollBar.vertical.policy: ScrollBar.AsNeeded
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                            TextArea {
                                id: transcriptText
                                width: transcriptScroll.availableWidth
                                text: displayModel ? displayModel.ttsText : "待命"
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                font.family: "PingFang SC, Microsoft YaHei UI"
                                font.pixelSize: 14
                                color: "#edf4ff"
                                textFormat: TextEdit.PlainText
                                background: null
                                padding: 0
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 98
            color: "#040a14"
            border.color: "#13233f"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.bottomMargin: 14
                anchors.topMargin: 12
                spacing: 8

                Button {
                    id: manualBtn
                    Layout.preferredWidth: 116
                    Layout.fillWidth: true
                    Layout.maximumWidth: 156
                    Layout.preferredHeight: 44
                    text: "按住后说话"
                    visible: displayModel ? !displayModel.autoMode : true
                    scale: manualBtn.pressed ? 0.97 : (manualBtn.hovered ? 1.02 : 1.0)

                    background: Rectangle {
                        radius: 12
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: manualBtn.pressed ? "#0d43ce" : "#2f82ff" }
                            GradientStop { position: 1.0; color: manualBtn.pressed ? "#083288" : "#1554d8" }
                        }
                        border.color: "#83bcff"
                        border.width: manualBtn.hovered ? 1 : 0
                    }

                    contentItem: Text {
                        text: manualBtn.text
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: "white"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    onPressed: {
                        manualBtn.text = "松开以停止"
                        root.manualButtonPressed()
                    }
                    onReleased: {
                        manualBtn.text = "按住后说话"
                        root.manualButtonReleased()
                    }
                }

                Button {
                    id: autoBtn
                    Layout.preferredWidth: 116
                    Layout.fillWidth: true
                    Layout.maximumWidth: 156
                    Layout.preferredHeight: 44
                    text: displayModel ? displayModel.buttonText : "开始对话"
                    visible: displayModel ? displayModel.autoMode : false
                    scale: autoBtn.pressed ? 0.97 : (autoBtn.hovered ? 1.02 : 1.0)

                    background: Rectangle {
                        radius: 12
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: autoBtn.pressed ? "#0d43ce" : "#2f82ff" }
                            GradientStop { position: 1.0; color: autoBtn.pressed ? "#083288" : "#1554d8" }
                        }
                        border.color: "#83bcff"
                        border.width: autoBtn.hovered ? 1 : 0
                    }

                    contentItem: Text {
                        text: autoBtn.text
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: "white"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    onClicked: root.autoButtonClicked()
                }

                Button {
                    id: abortBtn
                    Layout.preferredWidth: 94
                    Layout.fillWidth: true
                    Layout.maximumWidth: 132
                    Layout.preferredHeight: 44
                    text: "打断对话"
                    scale: abortBtn.pressed ? 0.98 : (abortBtn.hovered ? 1.01 : 1.0)

                    background: Rectangle {
                        radius: 12
                        color: abortBtn.pressed ? "#122746" : "#0b1830"
                        border.color: abortBtn.hovered ? root.accentColor : "#21406f"
                        border.width: 1
                    }

                    contentItem: Text {
                        text: abortBtn.text
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 12
                        color: "#88b7e1"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    onClicked: root.abortButtonClicked()
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 140
                    Layout.preferredHeight: 44
                    spacing: 8

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        radius: 12
                        color: "#071423"
                        border.color: textInput.activeFocus ? root.accentColor : "#1c3357"
                        border.width: 1

                        TextInput {
                            id: textInput
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            verticalAlignment: TextInput.AlignVCenter
                            font.family: "PingFang SC, Microsoft YaHei UI"
                            font.pixelSize: 13
                            color: "#d6e7fb"
                            selectByMouse: true
                            clip: true

                            Text {
                                anchors.fill: parent
                                text: "输入文字..."
                                font: textInput.font
                                color: "#496682"
                                verticalAlignment: Text.AlignVCenter
                                visible: !textInput.text && !textInput.activeFocus
                            }

                            Keys.onReturnPressed: {
                                if (textInput.text.trim().length > 0) {
                                    root.sendButtonClicked(textInput.text)
                                    textInput.text = ""
                                }
                            }
                        }
                    }

                    Button {
                        id: sendBtn
                        Layout.preferredWidth: 72
                        Layout.maximumWidth: 92
                        Layout.preferredHeight: 44
                        text: "发送"
                        scale: sendBtn.pressed ? 0.97 : (sendBtn.hovered ? 1.02 : 1.0)

                        background: Rectangle {
                            radius: 12
                            color: sendBtn.pressed ? "#12335f" : "#0f2341"
                            border.color: sendBtn.hovered ? root.accentColor : "#28518a"
                            border.width: 1
                        }

                        contentItem: Text {
                            text: sendBtn.text
                            font.family: "PingFang SC, Microsoft YaHei UI"
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            color: "#e9f4ff"
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                        onClicked: {
                            if (textInput.text.trim().length > 0) {
                                root.sendButtonClicked(textInput.text)
                                textInput.text = ""
                            }
                        }
                    }
                }

                Button {
                    id: modeBtn
                    Layout.preferredWidth: 92
                    Layout.fillWidth: true
                    Layout.maximumWidth: 124
                    Layout.preferredHeight: 44
                    text: displayModel ? displayModel.modeText : "手动对话"
                    scale: modeBtn.pressed ? 0.98 : (modeBtn.hovered ? 1.01 : 1.0)

                    background: Rectangle {
                        radius: 12
                        color: modeBtn.pressed ? "#11253f" : "#0a1830"
                        border.color: modeBtn.hovered ? root.accentColor : "#21406f"
                        border.width: 1
                    }

                    contentItem: Text {
                        text: modeBtn.text
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 12
                        color: "#88b7e1"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    onClicked: root.modeButtonClicked()
                }

                Button {
                    id: settingsBtn
                    Layout.preferredWidth: 92
                    Layout.fillWidth: true
                    Layout.maximumWidth: 124
                    Layout.preferredHeight: 44
                    text: "参数配置"
                    scale: settingsBtn.pressed ? 0.98 : (settingsBtn.hovered ? 1.01 : 1.0)

                    background: Rectangle {
                        radius: 12
                        color: settingsBtn.pressed ? "#11253f" : "#0a1830"
                        border.color: settingsBtn.hovered ? root.accentColor : "#21406f"
                        border.width: 1
                    }

                    contentItem: Text {
                        text: settingsBtn.text
                        font.family: "PingFang SC, Microsoft YaHei UI"
                        font.pixelSize: 12
                        color: "#88b7e1"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    onClicked: root.settingsButtonClicked()
                }
            }
        }
    }
}
