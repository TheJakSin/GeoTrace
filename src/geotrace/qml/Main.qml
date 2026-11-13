import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    width: 1380; height: 880
    minimumWidth: 1100; minimumHeight: 720
    visible: true
    title: "GeoTrace · Геологические карты · 0.2 alpha"
    color: "#edf1f2"
    Material.theme: Material.Light
    Material.accent: "#087e78"
    font.family: Qt.platform.os === "osx" ? "Helvetica Neue" : "Segoe UI"
    font.pixelSize: 13
    property var appState: backend.state
    property bool closingConfirmed: false
    property string pendingAction: ""
    component ActionButton: Button {
        id: control
        implicitHeight: 38
        leftPadding: 12; rightPadding: 12
        contentItem: Label {
            text: control.text; font: control.font
            color: !control.enabled ? "#849797" : control.highlighted ? "#ffffff" : "#234b50"
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 7
            color: !control.enabled ? "#e7eded" : control.highlighted ? (control.down ? "#086a64" : "#087e78") : (control.hovered ? "#dde9e8" : "#edf3f2")
        }
    }
    function request(action) {
        if (root.appState.busy) return
        if (root.appState.dirty) { pendingAction = action; discard.open() }
        else perform(action)
    }
    function perform(action) {
        if (action === "open") openDialog.open()
        if (action === "run") backend.run(groups.currentValue, minimum.value)
        if (action === "close") { closingConfirmed = true; root.close() }
    }
    onClosing: function(event) {
        if (root.appState.busy) { event.accepted = false; busyDialog.open() }
        else if (root.appState.dirty && !closingConfirmed) { event.accepted = false; request("close") }
    }
    FileDialog {
        id: openDialog
        title: "Открыть привязанную карту"
        nameFilters: ["GeoTIFF (*.tif *.tiff)"]
        onAccepted: backend.openFile(selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: "Сохранить новую версию полигонов"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "gpkg"
        nameFilters: ["GeoPackage (*.gpkg)"]
        onAccepted: backend.exportFile(selectedFile)
    }
    Dialog {
        id: discard
        anchors.centerIn: parent
        title: "Есть несохранённый результат"
        modal: true
        standardButtons: Dialog.Discard | Dialog.Cancel
        Label { text: "Продолжить без сохранения текущих полигонов и атрибутов?" }
        onDiscarded: root.perform(root.pendingAction)
    }
    Dialog {
        id: busyDialog
        anchors.centerIn: parent; title: "Идёт обработка"; modal: true
        standardButtons: Dialog.Ok
        Label { text: "Дождитесь окончания операции перед закрытием программы." }
    }
    Dialog {
        id: edgeDialog; anchors.centerIn: parent; title: "Область достигает рамки"; modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label { width: 370; wrapMode: Text.WordWrap; text: "Выделение может быть обрезано рамкой анализа. Принять его в таком виде? Для полного объекта можно сбросить подсказки и увеличить рамку." }
        onAccepted: backend.acceptPrompt()
    }
    Shortcut { sequences: [StandardKey.Open]; onActivated: root.request("open") }
    Shortcut { sequences: [StandardKey.Undo]; onActivated: backend.undo() }
    Shortcut { sequences: [StandardKey.Save]; enabled: root.appState.count > 0 && !root.appState.busy; onActivated: saveDialog.open() }

    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 78; color: "#122f36"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 24; anchors.rightMargin: 24; spacing: 18
                Rectangle {
                    width: 38; height: 38; radius: 11; color: "#72d5ba"
                    Label { anchors.centerIn: parent; text: "G"; color: "#122f36"; font.pixelSize: 26; font.bold: true }
                }
                ColumnLayout {
                    spacing: 2
                    Label { text: "GeoTrace"; color: "white"; font.pixelSize: 23; font.bold: true }
                    Label { text: "ОЦИФРОВКА ГЕОЛОГИЧЕСКИХ КАРТ"; color: "#a9c5c8"; font.pixelSize: 10; font.letterSpacing: 1.2 }
                }
                Item { Layout.fillWidth: true }
                Label { text: "0.2  /  ПРОТОТИП"; color: "#acd1cc"; font.pixelSize: 11 }
                ActionButton { text: "Открыть карту"; enabled: !root.appState.busy; onClicked: root.request("open") }
                ActionButton {
                    text: "Экспорт .gpkg"; highlighted: true
                    enabled: root.appState.count > 0 && !root.appState.busy
                    onClicked: saveDialog.open()
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 1
            Rectangle {
                Layout.preferredWidth: 256; Layout.fillHeight: true; color: "#fcfdfd"
                ScrollView {
                    id: sidebarScroll
                    anchors.fill: parent; anchors.margins: 16; clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: sidebarScroll.availableWidth; spacing: 10
                        Label { text: "01  Распознавание"; font.bold: true; font.pixelSize: 17; color: "#183b40" }
                        TabBar {
                            id: modeTabs; objectName: "modeTabs"; Layout.fillWidth: true
                            TabButton { text: "Авто" }
                            TabButton { text: "Подсказки" }
                        }
                        ColumnLayout {
                            visible: modeTabs.currentIndex === 0; Layout.fillWidth: true; spacing: 10
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#62787d"; font.pixelSize: 12
                                text: "Пробное выделение цветовых групп по всей карте." }
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "Группы"; Layout.fillWidth: true }
                                ComboBox { id: groups; model: [2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]; currentIndex: 5; enabled: !root.appState.busy; Layout.preferredWidth: 96 }
                            }
                            Label { text: "Подавление мелких пятен, px"; font.pixelSize: 11; color: "#62787d" }
                            SpinBox { id: minimum; from: 1; to: 1000; stepSize: 20; value: 80; editable: true; enabled: !root.appState.busy; Layout.fillWidth: true }
                            ActionButton { text: "Выделить всю карту"; highlighted: true; Layout.fillWidth: true
                                enabled: root.appState.rasterUrl !== "" && !root.appState.busy; onClicked: root.request("run") }
                        }
                        ColumnLayout {
                            visible: modeTabs.currentIndex === 1; Layout.fillWidth: true; spacing: 9
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#62787d"; font.pixelSize: 12
                                text: "Зелёные точки — нужная область, красные — лишнее. Рамка появится после первой зелёной точки." }
                            ComboBox {
                                id: hintMode; objectName: "hintMode"; Layout.fillWidth: true
                                model: ["Выбрать полигон", "Включить (+)", "Исключить (−)"]
                                currentIndex: 1; enabled: !root.appState.busy
                            }
                            Label { text: "Метод"; font.pixelSize: 11; color: "#62787d" }
                            ComboBox {
                                id: hintMethod; Layout.fillWidth: true
                                model: root.appState.neuralAvailable ? ["Цвет + подсказки", "MobileSAM · CPU"] : ["Цвет + подсказки"]
                                enabled: !root.appState.busy
                                onActivated: backend.invalidateProposal()
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "Рамка, px"; Layout.fillWidth: true; font.pixelSize: 11 }
                                ComboBox { id: roiSize; model: [256,512,768]; currentIndex: 1; Layout.preferredWidth: 96
                                    enabled: !root.appState.busy && root.appState.hintCount === 0 }
                            }
                            Label { text: "Допуск цвета: " + Math.round(tolerance.value); visible: hintMethod.currentIndex === 0; font.pixelSize: 11; color: "#62787d" }
                            Slider { id: tolerance; Layout.fillWidth: true; from: 5; to: 45; stepSize: 1; value: 22
                                visible: hintMethod.currentIndex === 0; enabled: !root.appState.busy
                                onMoved: backend.invalidateProposal() }
                            ActionButton { text: "Уточнить область"; Layout.fillWidth: true; highlighted: true
                                enabled: root.appState.hintCount > 0 && !root.appState.busy
                                onClicked: backend.runPrompt(hintMethod.currentIndex, Math.round(tolerance.value)) }
                            RowLayout {
                                Layout.fillWidth: true
                                ActionButton { text: "− точка"; Layout.fillWidth: true; enabled: root.appState.hintCount > 0 && !root.appState.busy; onClicked: backend.removeHint() }
                                ActionButton { text: "Сбросить"; Layout.fillWidth: true; enabled: root.appState.hintCount > 0 && !root.appState.busy; onClicked: backend.clearHints() }
                            }
                            ActionButton { text: "Принять область"; Layout.fillWidth: true
                                enabled: root.appState.proposalReady && !root.appState.busy
                                onClicked: { if (root.appState.touchesEdge) edgeDialog.open(); else backend.acceptPrompt() } }
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#9a6426"; font.pixelSize: 11
                                visible: root.appState.touchesEdge; text: "Выделение достигает рамки. Возможно, объект обрезан." }
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#62787d"; font.pixelSize: 11
                                text: "Принятые области защищены от перезаписи. Отменить принятие: Ctrl/Cmd+Z." }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#e4ebeb" }
                        Label { text: "СЛОИ"; color: "#62787d"; font.pixelSize: 10; font.letterSpacing: 1.5 }
                        CheckBox { id: rasterVisible; text: "Исходная карта"; checked: true }
                        CheckBox { id: overlayVisible; text: "Принятые / авто-полигоны"; checked: true; enabled: root.appState.count > 0 }
                        Label { text: "Непрозрачность полигонов"; font.pixelSize: 11; color: "#62787d" }
                        Slider { id: opacitySlider; from: 0; to: 1; value: 0.55; Layout.fillWidth: true }
                        Label { text: "ЦВЕТОВЫЕ ГРУППЫ"; color: "#62787d"; font.pixelSize: 10; font.letterSpacing: 1.1 }
                        Repeater {
                            model: root.appState.colors
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true; height: 22
                                Rectangle { width: 14; height: 14; radius: 4; color: modelData.color; border.color: "#aebfbd" }
                                Label { text: modelData.label; Layout.fillWidth: true; font.pixelSize: 12 }
                                Label { text: modelData.count; color: "#62787d"; font.pixelSize: 11 }
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                Rectangle {
                    Layout.fillWidth: true; height: 64; color: "#ffffff"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 12
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 4
                            Label { text: root.appState.filename || "Рабочее поле"; elide: Text.ElideMiddle; Layout.fillWidth: true; font.bold: true }
                            Label { text: root.appState.count ? root.appState.count + " полигонов · требуется проверка" : "GeoTIFF → области → полигоны"; color: "#62787d"; font.pixelSize: 11 }
                        }
                        ActionButton { text: "−"; Layout.preferredWidth: 38; onClicked: viewport.zoom = Math.max(0.1, viewport.zoom / 1.3) }
                        ActionButton { text: "Вписать"; onClicked: viewport.fit() }
                        ActionButton { text: "+"; Layout.preferredWidth: 38; onClicked: viewport.zoom = Math.min(12, viewport.zoom * 1.3) }
                    }
                }
                Flickable {
                    id: viewport
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    property real zoom: 1
                    contentWidth: Math.max(width, root.appState.width * zoom)
                    contentHeight: Math.max(height, root.appState.height * zoom)
                    function fit() {
                        zoom = Math.min((width - 40) / root.appState.width, (height - 40) / root.appState.height)
                        contentX = 0; contentY = 0
                    }
                    Connections { target: backend; function onMapLoaded() { viewport.fit() } }
                    Item {
                        objectName: "rasterCanvas"
                        width: root.appState.width * viewport.zoom; height: root.appState.height * viewport.zoom
                        x: Math.max(0, (viewport.width - width) / 2)
                        y: Math.max(0, (viewport.height - height) / 2)
                        visible: root.appState.rasterUrl !== ""
                        Image { anchors.fill: parent; source: root.appState.rasterUrl; visible: rasterVisible.checked; cache: false; smooth: false }
                        Image { anchors.fill: parent; source: root.appState.overlayUrl; visible: overlayVisible.checked; opacity: opacitySlider.value; cache: false; smooth: false }
                        Image { anchors.fill: parent; source: root.appState.selectionUrl; cache: false; smooth: false }
                        Image { anchors.fill: parent; source: root.appState.hintUrl; cache: false; smooth: false }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: modeTabs.currentIndex === 1 && hintMode.currentIndex > 0 ? Qt.CrossCursor : Qt.ArrowCursor
                            onClicked: function(mouse) {
                                var x = mouse.x / viewport.zoom; var y = mouse.y / viewport.zoom
                                if (modeTabs.currentIndex === 1 && hintMode.currentIndex > 0)
                                    backend.addHint(x,y,hintMode.currentIndex === 1 ? 1 : 0,roiSize.currentValue)
                                else backend.selectAt(x,y)
                            }
                            onWheel: function(wheel) {
                                var old = viewport.zoom
                                var next = Math.max(0.1, Math.min(12, old * (wheel.angleDelta.y > 0 ? 1.15 : 1 / 1.15)))
                                var px = wheel.x / old; var py = wheel.y / old
                                var screenX = parent.x + wheel.x - viewport.contentX
                                var screenY = parent.y + wheel.y - viewport.contentY
                                viewport.zoom = next
                                viewport.contentX = Math.max(0, Math.min(viewport.contentWidth - viewport.width, parent.x + px * next - screenX))
                                viewport.contentY = Math.max(0, Math.min(viewport.contentHeight - viewport.height, parent.y + py * next - screenY))
                                wheel.accepted = true
                            }
                        }
                    }
                    Column {
                        anchors.centerIn: parent; width: Math.min(parent.width - 60, 380); spacing: 16
                        visible: root.appState.rasterUrl === ""
                        Label { text: "От карты — к данным"; font.pixelSize: 28; font.bold: true; color: "#24484b"; width: parent.width; horizontalAlignment: Text.AlignHCenter }
                        Label { text: "Откройте привязанную геологическую карту. Исходник останется основой для проверки каждой границы."; wrapMode: Text.WordWrap; width: parent.width; horizontalAlignment: Text.AlignHCenter; color: "#6b8285"; font.pixelSize: 15 }
                    }
                    ScrollBar.horizontal: ScrollBar { }
                    ScrollBar.vertical: ScrollBar { }
                }
                Rectangle {
                    Layout.fillWidth: true; height: 34; color: "#e3eaea"
                    Label { anchors.verticalCenter: parent.verticalCenter; x: 12; width: parent.width - 24; elide: Text.ElideRight; text: root.appState.metadata || "Колесо — масштаб · перетаскивание — перемещение · щелчок — выбор полигона"; font.pixelSize: 10; color: "#4c676c" }
                }
            }
            Rectangle {
                Layout.preferredWidth: 270; Layout.fillHeight: true; color: "#fcfdfd"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 12
                    Label { text: "02  Проверка"; font.bold: true; font.pixelSize: 17; color: "#183b40" }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#62787d"
                        text: root.appState.selection.object_id ? "Полигон № " + root.appState.selection.object_id + " · группа " + root.appState.selection.class_id : "Выберите полигон на карте, чтобы заполнить его атрибуты."
                    }
                    Label { text: "Геологический индекс"; topPadding: 8 }
                    TextField { id: indexField; objectName: "indexField"; Layout.fillWidth: true; enabled: !!root.appState.selection.object_id && !root.appState.busy; text: root.appState.selection.unit_index || ""; placeholderText: "Например, O₂cr" }
                    Label { text: "Название подразделения" }
                    TextField { id: nameField; objectName: "nameField"; Layout.fillWidth: true; enabled: !!root.appState.selection.object_id && !root.appState.busy; text: root.appState.selection.unit_name || ""; placeholderText: "Введите название" }
                    Label { text: "Примечание" }
                    ScrollView {
                        Layout.fillWidth: true; Layout.preferredHeight: 110
                        TextArea { id: noteField; objectName: "noteField"; enabled: !!root.appState.selection.object_id && !root.appState.busy; text: root.appState.selection.note || ""; placeholderText: "Что нужно уточнить…"; wrapMode: Text.WordWrap }
                    }
                    ActionButton {
                        text: "Применить атрибуты"; Layout.fillWidth: true
                        enabled: !!root.appState.selection.object_id && !root.appState.busy
                        onClicked: backend.updateAttributes(indexField.text, nameField.text, noteField.text)
                    }
                    ActionButton { text: "Отменить последнее действие"; Layout.fillWidth: true; enabled: !root.appState.busy && root.appState.canUndo; onClicked: backend.undo() }
                    Item { Layout.fillHeight: true }
                    Rectangle {
                        Layout.fillWidth: true; implicitHeight: caveat.implicitHeight + 28; radius: 9; color: "#f5efdf"
                        Label {
                            id: caveat; anchors.fill: parent; anchors.margins: 14; wrapMode: Text.WordWrap; font.pixelSize: 11; color: "#78623b"
                            text: "Экспериментальный результат\n\nПроверьте выделение по исходнику. Подсказки уточняют форму, но не определяют геологический смысл. Сохраните принятые области в GeoPackage."
                        }
                    }
                }
            }
        }
        ProgressBar { Layout.fillWidth: true; Layout.preferredHeight: root.appState.busy ? 3 : 0; visible: root.appState.busy; indeterminate: true }
        Rectangle {
            Layout.fillWidth: true; height: 46; color: root.appState.error ? "#fbe4de" : "#ffffff"
            Label { anchors.fill: parent; anchors.margins: 12; verticalAlignment: Text.AlignVCenter; text: (root.appState.dirty ? "●  " : "") + root.appState.status; elide: Text.ElideMiddle; color: root.appState.error ? "#a43725" : "#49666c"; font.pixelSize: 12 }
        }
    }
}
