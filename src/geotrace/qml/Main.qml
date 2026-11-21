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
    title: (root.appState.dirty ? "● " : "") + (root.appState.projectName || "Новый проект") + " — GeoTrace"
    color: "#e8ebef"
    Material.theme: Material.Light
    Material.accent: "#356ac3"
    font.family: Qt.platform.os === "osx" ? "Helvetica Neue" : "Segoe UI"
    font.pixelSize: 13
    property var appState: backend.state
    property bool closingConfirmed: false
    property string pendingAction: ""
    property bool continueAfterSave: false
    property bool webMapOpened: false
    property bool sidebarOpen: false
    property bool layersMode: false
    property bool attributesPinned: false
    property bool attributesDismissed: false
    property int lastSelectedId: -1
    readonly property bool attributesShown: attributesPinned
    property bool originalOnly: false
    property bool viewing: true
    readonly property string commandKey: Qt.platform.os === "osx" ? "⌘" : "Ctrl"
    readonly property bool mapKeysEnabled: workspaceTabs.currentIndex===0 && !!appState.rasterUrl && !appState.busy && !autosavePaused && !polygonDialog.visible && !advancedTools.visible && !projectMenu.visible && !backend.isTextInput(activeFocusItem)
    property bool reviewOpen: false
    property int shownReviewCount: 0
    readonly property bool comparisonEnabled: workspaceTabs.currentIndex===0 && !!appState.rasterUrl && !autosavePaused && !polygonDialog.visible
    onComparisonEnabledChanged: backend.setCompareShortcutEnabled(comparisonEnabled)
    Component.onDestruction: backend.setCompareShortcutEnabled(false)
    function chooseTool(index, lasso) {
        viewing=false; modeTabs.currentIndex=2; editTool.currentIndex=index; lassoToggle.checked=lasso
        viewport.forceActiveFocus()
    }
    function chooseView() { modeTabs.currentIndex=0; viewing=true; viewport.forceActiveFocus() }
    function escapeMap() {
        if(appState.editPoints.length) backend.clearEditLine()
        else { backend.clearEditSelection(); chooseView() }
    }
    function finishEdit() {
        if(appState.geometryReady) backend.acceptGeometry()
        else if(appState.autoReady) backend.acceptAuto()
        else if(linePreviewButton.visible && linePreviewButton.enabled) linePreviewButton.clicked()
        else if(quickMergeButton.visible && quickMergeButton.enabled) quickMergeButton.clicked()
    }
    property var paletteModel: []
    property var groupNames: []
    property string groupsJson: ""
    property string paletteJson: "[]"
    property var customFieldsModel: []
    property string fieldsJson: "[]"
    property string bulkFieldsJson: ""
    property var bulkFieldsModel: []
    property var fieldTypeCodes: ["text", "integer", "number", "boolean", "date", "choice"]
    property var fieldTypeNames: ["Текст", "Целое число", "Дробное число", "Да / Нет", "Дата", "Список"]
    function refreshFieldModel() {
        var names=root.appState.colors.map(function(c){return c.label})
        var encodedNames=JSON.stringify(names)
        if(encodedNames!==groupsJson){groupsJson=encodedNames;groupNames=names}
        var encodedPalette = JSON.stringify(root.appState.qualityPalette)
        if (encodedPalette !== paletteJson) { paletteJson=encodedPalette; paletteModel=root.appState.qualityPalette }
        var bulkEncoded = JSON.stringify(root.appState.bulkFields)
        if (bulkEncoded !== bulkFieldsJson) { bulkFieldsJson=bulkEncoded; bulkFieldsModel=root.appState.bulkFields }
        var encoded = JSON.stringify(root.appState.fields)
        if (encoded !== fieldsJson) { fieldsJson = encoded; customFieldsModel = root.appState.fields }
    }
    function customValues() {
        var values = {}
        for (var i=0; i<attributeRepeater.count; i++) {
            var editor = attributeRepeater.itemAt(i)
            values[editor.field.key] = editor.readValue()
        }
        return JSON.stringify(values)
    }
    function editField(field) {
        fieldDialog.originalKey = field ? field.key : ""
        fieldDialog.fieldError = ""
        preset.currentIndex=0
        fieldKey.text = field ? field.key : ""
        fieldLabel.text = field ? field.label : ""
        fieldType.currentIndex = field ? fieldTypeCodes.indexOf(field.type) : 0
        fieldRequired.checked = field ? field.required : false
        fieldDefault.text = field && field.default !== null ? String(field.default) : ""
        fieldChoices.text = field ? field.choices.join("\n") : ""
        fieldDialog.open()
    }
    component AttributeInput: ColumnLayout {
        id: input
        required property var field
        property var currentValue
        property bool active: false
        property bool mixed: false
        property int resetToken: 0
        property bool edited: false
        property string initialText: ""
        property int initialIndex: 0
        function resetEditor() {
            if(!valueText || !valueChoice)return
            valueText.text=currentValue===undefined || currentValue===null ? "" : String(currentValue)
            valueChoice.currentIndex=currentValue===undefined || currentValue===null ? 0 : field.type==="boolean" ? (currentValue ? 1 : 2) : field.choices.indexOf(currentValue)+1
            initialText=valueText.text;initialIndex=valueChoice.currentIndex;edited=false
        }
        function isChanged() { if(mixed && (field.type==="boolean" || field.type==="choice") && valueChoice.currentIndex===0)return false; return edited || ((field.type==="boolean" || field.type==="choice") ? valueChoice.currentIndex!==initialIndex : valueText.text!==initialText) }
        onResetTokenChanged: resetEditor()
        onCurrentValueChanged: resetEditor()
        Component.onCompleted: resetEditor()
        function clearInput() { valueText.text=""; valueChoice.currentIndex=0 }
        function readValue() {
            var offset=mixed ? 1 : 0
            if (field.type === "boolean") return valueChoice.currentIndex<=offset ? null : valueChoice.currentIndex===offset+1
            if (field.type === "choice") return valueChoice.currentIndex<=offset ? null : field.choices[valueChoice.currentIndex-offset-1]
            return valueText.text
        }
        spacing: 4
        Label { text: input.field.label + (input.field.required ? " *" : ""); Layout.fillWidth: true; wrapMode: Text.WordWrap }
        TextField { implicitHeight: 34;
            id: valueText; objectName: "attribute_" + input.field.key
            Layout.fillWidth: true; enabled: input.active
            visible: input.field.type !== "boolean" && input.field.type !== "choice"
            text: input.currentValue === undefined || input.currentValue === null ? "" : String(input.currentValue)
            placeholderText: input.mixed ? "Разные значения" : input.field.type === "date" ? "ГГГГ-ММ-ДД" : "Не задано"
            onTextEdited: input.edited=true
        }
        ComboBox { implicitHeight: 34;
            id: valueChoice; objectName: "choice_" + input.field.key
            Layout.fillWidth: true; enabled: input.active
            visible: input.field.type === "boolean" || input.field.type === "choice"
            model: (input.mixed ? ["Разные значения", "Не задано"] : ["Не задано"]).concat(input.field.type==="boolean" ? ["Да","Нет"] : input.field.choices)
            onActivated: input.edited=true
            currentIndex: input.currentValue === undefined || input.currentValue === null ? 0 : input.field.type === "boolean" ? (input.currentValue ? 1 : 2) : input.field.choices.indexOf(input.currentValue)+1
        }
    }
    property bool autosavePaused: helpDialog.visible || autoOptions.visible || promptOptions.visible || mapSettings.visible || webKeyDialog.visible || yandexKeyDialog.visible || basemapFileDialog.visible || areaFileDialog.visible || areaDialog.visible || openDialog.visible || saveDialog.visible || projectSaveDialog.visible || discard.visible || recoveryDialog.visible || busyDialog.visible || edgeDialog.visible || mapMouse.vertexDragging || mapMouse.edgeDragging || mapMouse.lassoDragging || schemaManager.visible || fieldDialog.visible || deleteFieldDialog.visible || bulkConfirm.visible || paletteDialog.visible
    onAutosavePausedChanged: backend.pauseAutosave(autosavePaused)
    function saveProject() {
        if (root.appState.busy) return
        if (root.appState.projectPath) backend.saveProject("")
        else projectSaveDialog.open()
    }
    Connections {
        target: backend
        function onChanged() {
            var oid=root.appState.selection.object_id || -1
            if (oid!==root.lastSelectedId) { root.lastSelectedId=oid;root.attributesDismissed=false }
            if(root.shownReviewCount!==root.appState.reviewCount){root.shownReviewCount=root.appState.reviewCount;root.reviewOpen=false}
            root.refreshFieldModel(); if (root.appState.error) root.continueAfterSave = false }
        function onCompareShortcut() { root.originalOnly=!root.originalOnly }
        function onMapLoaded() { root.chooseView();root.originalOnly=false;root.reviewOpen=false;root.attributesPinned=false;attributeTarget.currentIndex=0 }
        function onFocusPolygon(x0,y0,x1,y1) {
            viewport.fitMode=false
            viewport.zoom=Math.max(.1,Math.min(12,(viewport.usableWidth-80)/Math.max(1,x1-x0),(viewport.height-80)/Math.max(1,y1-y0)))
            viewport.contentX=Math.max(0,Math.min(viewport.contentWidth-viewport.width,(x0+x1)*viewport.zoom/2+rasterCanvas.x-viewport.usableWidth/2))
            viewport.contentY=Math.max(0,Math.min(viewport.contentHeight-viewport.height,(y0+y1)*viewport.zoom/2+rasterCanvas.y-viewport.height/2))
        }
        function onProjectSaved() {
            if (root.continueAfterSave) {
                root.continueAfterSave = false
                root.perform(root.pendingAction)
            }
        }
    }
    Component.onCompleted: { backend.setCompareShortcutEnabled(root.comparisonEnabled); refreshFieldModel(); if (root.appState.recoveries.length) recoveryDialog.open() }
    component ActionButton: Button {
        id: control
        hoverEnabled: true
        property string description: Accessible.name || text
        property string shortcutLabel: ""
        ToolTip.visible: hovered && !!description
        ToolTip.delay: 550
        ToolTip.text: description + (shortcutLabel ? " · " + shortcutLabel : "")
        implicitHeight: 30
        topInset: 0; bottomInset: 0; leftInset: 0; rightInset: 0
        leftPadding: 9; rightPadding: 9; topPadding: 5; bottomPadding: 5
        contentItem: Label {
            text: control.text; font: control.font
            color: !control.enabled ? "#939ba6" : control.highlighted ? "#ffffff" : "#303944"
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 3
            color: !control.enabled ? "#eef0f3" : control.highlighted ? (control.down ? "#28549c" : "#356ac3") : (control.hovered ? "#e2e8f1" : "#f0f2f5")
        }
    }
    component MapTool: ActionButton {
        id: tool
        property string iconName: "cursor"
        leftPadding: 5; rightPadding: 5; topPadding: 5; bottomPadding: 5
        contentItem: Image { source: Qt.resolvedUrl("icons/"+tool.iconName+".svg"); sourceSize.width: 22; sourceSize.height: 22; fillMode: Image.PreserveAspectFit; opacity: tool.enabled ? 1 : .4 }
        background: Rectangle { radius: 2; color: tool.highlighted ? "#e1eafa" : tool.hovered ? "#e8ebf0" : "transparent"; border.width: tool.highlighted ? 1 : 0; border.color: "#356ac3" }
    }
    function request(action) {
        if (root.appState.busy) return
        if (action === "run") { perform(action); return }
        if (root.appState.dirty) { pendingAction = action; discard.open() }
        else perform(action)
    }
    function perform(action) {
        if (action === "open") openDialog.open()
        if (action === "run") {
            if (autoProfile.currentIndex === 2) backend.previewQuality()
            else backend.previewAuto(groups.currentValue, minimum.value, autoProfile.currentIndex === 1 ? "scan" : "baseline")
        }
        if (action === "close") { backend.discardRecovery(); closingConfirmed = true; root.close() }
    }
    onClosing: function(event) {
        if (root.appState.busy) { event.accepted = false; busyDialog.open() }
        else if (root.appState.dirty && !closingConfirmed) { event.accepted = false; request("close") }
    }
    FileDialog {
        id: openDialog
        title: "Открыть проект или привязанную карту"
        nameFilters: ["Карты и проекты (*.geotrace *.tif *.tiff)", "Проекты GeoTrace (*.geotrace)", "GeoTIFF (*.tif *.tiff)"]
        onAccepted: backend.openFile(selectedFile)
    }
    Dialog {
        id: yandexKeyDialog; objectName: "yandexKeyDialog"
        anchors.centerIn: parent; modal: true; width: 460; title: "Яндекс · ключ Tiles API"
        onClosed: yandexKey.text = ""
        contentItem: ColumnLayout {
            spacing: 12
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "В приложении задан стандартный ключ. Здесь можно указать свой ключ пакета Tiles API. Свой ключ сохраняется на этом компьютере."
            }
            TextField { implicitHeight: 34;
                id: yandexKey; objectName: "yandexKey"; Layout.fillWidth: true
                placeholderText: root.appState.yandexKeyConfigured ? "Введите новый ключ для замены" : "Ключ Tiles API"
                echoMode: TextInput.Password; selectByMouse: true; maximumLength: 256
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#a43725"
                text: root.appState.yandexKeyError; visible: !!text
            }
            ActionButton {
                text: "Получить ключ"; onClicked: Qt.openUrlExternally("https://developer.tech.yandex.ru/")
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ActionButton {
                    text: "Стандартный"; objectName: "resetYandexKey"
                    onClicked: { if (backend.resetYandexKey()) yandexKeyDialog.close() }
                }
                ActionButton { text: "Отмена"; onClicked: yandexKeyDialog.close() }
                ActionButton {
                    text: "Сохранить"; objectName: "saveYandexKey"; highlighted: true; enabled: yandexKey.text.trim().length > 0
                    onClicked: { if (backend.setYandexKey(yandexKey.text)) yandexKeyDialog.close() }
                }
            }
        }
    }
    FileDialog {
        id: basemapFileDialog
        title: "Открыть снимок GeoTIFF"
        nameFilters: ["Цветной GeoTIFF (*.tif *.tiff)"]
        onAccepted: backend.openBasemapFile(selectedFile)
    }
    FileDialog {
        id: areaFileDialog
        title: "Обрезать карту по KML"
        nameFilters: ["Контуры KML (*.kml)"]
        onAccepted: backend.loadArea(selectedFile)
    }
    Dialog {
        id: areaDialog; objectName: "areaDialog"
        anchors.centerIn: parent; modal: true; width: 460
        title: "Обрезка по KML"
        onAboutToShow: { areaChoice.currentIndex = 0; backend.previewArea(0) }
        onClosed: backend.dismissArea()
        contentItem: ColumnLayout {
            spacing: 12
            ComboBox { implicitHeight: 34;
                id: areaChoice; objectName: "areaChoice"; Layout.fillWidth: true
                model: root.appState.areaOptions
                onActivated: backend.previewArea(currentIndex)
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "Останется карта внутри выбранного контура, включая вырезы. Лишние части изображения и полигонов будут удалены из рабочей карты. Исходный файл сохранится. Доступна отмена."
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                visible: root.appState.error; text: root.appState.error ? root.appState.status : ""; color: "#a43725"
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ActionButton { text: "Отмена"; onClicked: areaDialog.close() }
                ActionButton {
                    text: "Обрезать"; objectName: "applyAreaButton"; highlighted: true
                    enabled: !root.appState.busy && !!root.appState.areaDraftUrl
                    onClicked: { backend.applyArea(areaChoice.currentIndex); areaDialog.close() }
                }
            }
        }
    }
    Connections { target: backend; function onAreaPrepared() { areaDialog.open() } }
    FileDialog {
        id: saveDialog
        title: "Сохранить новую версию полигонов"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "gpkg"
        nameFilters: ["GeoPackage (*.gpkg)"]
        onAccepted: backend.exportFile(selectedFile)
    }
    FileDialog {
        id: reviewSaveDialog
        title: "Сохранить журнал проверки"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: ["Журнал (*.csv)"]
        onAccepted: backend.exportReviewLog(selectedFile)
    }
    FileDialog {
        id: projectSaveDialog
        title: "Сохранить проект GeoTrace"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "geotrace"
        nameFilters: ["Проект GeoTrace (*.geotrace)"]
        onAccepted: backend.saveProject(selectedFile)
        onRejected: root.continueAfterSave = false
    }
    Dialog {
        id: recoveryDialog
        anchors.centerIn: parent; title: "Найдены рабочие копии"; modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            width: 420; spacing: 16
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "Можно восстановить работу из автоматической копии. Отмена оставит копии на диске." }
            ComboBox { implicitHeight: 34; id: recoveryChoice; Layout.fillWidth: true; model: root.appState.recoveries; textRole: "label" }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11; color: "#687383"
                text: "После восстановления сохраните проект. История отмены начинается заново." }
        }
        onAccepted: {
            if (recoveryChoice.currentIndex >= 0)
                backend.restoreRecovery(root.appState.recoveries[recoveryChoice.currentIndex].path)
        }
    }
    Dialog {
        id: discard; objectName: "unsavedDialog"
        anchors.centerIn: parent
        title: "Есть несохранённый результат"
        modal: true
        standardButtons: Dialog.Save | Dialog.Discard | Dialog.Cancel
        Label { text: "Сохранить карту, полигоны, атрибуты и подсказки в проект?" }
        onAccepted: { root.continueAfterSave = true; root.saveProject() }
        onDiscarded: { root.continueAfterSave = false; root.perform(root.pendingAction) }
        onRejected: root.continueAfterSave = false
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
    Dialog {
        id: schemaManager; objectName: "schemaManager"; anchors.centerIn: parent
        title: "Поля проекта"; modal: true; width: 600; height: 490
        background: Rectangle { color: "#ffffff"; radius: 4; border.color: "#dce0e6" }
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "Номер, группа, примечание, состояние проверки и происхождение есть у каждого полигона. Здесь можно добавить поля для своей задачи." }
            ActionButton { text: "Добавить поле"; objectName: "addFieldButton"; onClicked: root.editField(null) }
            ScrollView {
                id: fieldListScroll; Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                ColumnLayout {
                    width: fieldListScroll.availableWidth
                    Repeater {
                        model: root.customFieldsModel
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.label + (modelData.required ? " *" : ""); font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                                Label { text: modelData.key + " · " + root.fieldTypeNames[root.fieldTypeCodes.indexOf(modelData.type)]; font.pixelSize: 11; color: "#687383" }
                            }
                            ActionButton { text: "Изменить"; onClicked: root.editField(modelData) }
                            ActionButton { text: "Удалить"; onClicked: { deleteFieldDialog.fieldKey = modelData.key; deleteFieldDialog.fieldLabel = modelData.label; deleteFieldDialog.open() } }
                        }
                    }
                    Label { visible: !root.customFieldsModel.length; text: "Пользовательских полей пока нет."; color: "#687383" }
                }
            }
        }
    }
    Dialog {
        id: fieldDialog; objectName: "fieldDialog"; anchors.centerIn: parent; modal: true; width: 500
        title: originalKey ? "Изменить поле" : "Новое поле"
        background: Rectangle { color: "#ffffff"; radius: 4; border.color: "#dce0e6" }
        property string originalKey: ""
        property string fieldError: ""
        ColumnLayout {
            width: 460; spacing: 9
            ComboBox { implicitHeight: 34;
                id: preset; objectName: "fieldPreset"; Layout.fillWidth: true; visible: !fieldDialog.originalKey
                model: ["Своё поле"].concat(root.appState.suggestions.map(function(f){return f.label}))
                onActivated: {
                    var f = currentIndex ? root.appState.suggestions[currentIndex-1] : null
                    fieldKey.text = f ? f.key : ""; fieldLabel.text = f ? f.label : ""
                    fieldType.currentIndex = f ? root.fieldTypeCodes.indexOf(f.type) : 0
                    fieldChoices.text = f && f.choices ? f.choices.join("\n") : ""
                    fieldDefault.text = ""; fieldRequired.checked = false; fieldDialog.fieldError=""
                }
            }
            Label { text: "Название" }
            TextField { implicitHeight: 34; id: fieldLabel; objectName: "fieldLabel"; Layout.fillWidth: true; placeholderText: "Например, мощность пласта, м" }
            Label { text: "Код поля для ГИС" }
            TextField { implicitHeight: 34; id: fieldKey; objectName: "fieldKey"; Layout.fillWidth: true; readOnly: !!fieldDialog.originalKey; placeholderText: "thickness_m · латинские строчные буквы, цифры, _" }
            ComboBox { implicitHeight: 34; id: fieldType; objectName: "fieldType"; Layout.fillWidth: true; model: root.fieldTypeNames }
            CheckBox { implicitHeight: 30; id: fieldRequired; text: "Обязательно перед экспортом" }
            Label { text: "Значение по умолчанию · можно оставить пустым"; font.pixelSize: 12 }
            TextField { implicitHeight: 34; id: fieldDefault; objectName: "fieldDefault"; Layout.fillWidth: true; placeholderText: fieldType.currentIndex === 4 ? "ГГГГ-ММ-ДД" : fieldType.currentIndex === 3 ? "Да или Нет" : "Не задано" }
            Label { text: "Варианты списка · один на строку"; visible: fieldType.currentIndex === 5 }
            ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 90; visible: fieldType.currentIndex === 5
                TextArea { id: fieldChoices; objectName: "fieldChoices"; wrapMode: Text.WordWrap }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: !!fieldDialog.fieldError; text: fieldDialog.fieldError; color: "#a43725" }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ActionButton { text: "Отмена"; onClicked: fieldDialog.close() }
                ActionButton { text: "Сохранить поле"; objectName: "saveFieldButton"; highlighted: true
                    onClicked: {
                        var definition = {key:fieldKey.text.trim(),label:fieldLabel.text.trim(),type:root.fieldTypeCodes[fieldType.currentIndex],
                            required:fieldRequired.checked,default:fieldDefault.text,
                            choices:fieldChoices.text.split(/\r?\n/).map(function(v) {return v.trim()}).filter(function(v) {return !!v})}
                        if (backend.saveField(JSON.stringify(definition),fieldDialog.originalKey)) fieldDialog.close()
                        else fieldDialog.fieldError = backend.state.schemaError
                    }
                }
            }
        }
    }
    Dialog {
        id: deleteFieldDialog; anchors.centerIn: parent; modal: true; title: "Удалить поле?"
        background: Rectangle { color: "#ffffff"; radius: 4; border.color: "#dce0e6" }
        property string fieldKey: ""
        property string fieldLabel: ""
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label { width: 400; wrapMode: Text.WordWrap; text: "Поле «" + deleteFieldDialog.fieldLabel + "» и его значения у всех полигонов будут удалены. Действие можно отменить." }
        onAccepted: backend.removeField(fieldKey)
    }
    Dialog {
        id: polygonDialog; objectName: "polygonDialog"; anchors.centerIn: parent; modal: true
        title: "Таблица полигонов"; width: Math.min(root.width-48,1220); height: Math.min(root.height-48,760)
        background: Rectangle { color: "#ffffff"; radius: 4; border.color: "#dce0e6" }
        standardButtons: Dialog.Close
        function filterNow() { backend.filterTable(tableSearch.text,["all","unreviewed","accepted","edited"][reviewFilter.currentIndex],missingFilter.checked,tableGroup.currentIndex) }
        onOpened: filterNow()
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                TextField { implicitHeight: 34; id: tableSearch; objectName: "tableSearch"; Layout.fillWidth: true; placeholderText: "Поиск по номеру и всем атрибутам"; onTextEdited: searchDelay.restart() }
                Timer { id: searchDelay; interval: 250; onTriggered: polygonDialog.filterNow() }
                ComboBox { implicitHeight: 34; id: tableGroup; objectName: "tableGroup"; Layout.preferredWidth: 140; model: ["Все группы"].concat(root.groupNames); onActivated: polygonDialog.filterNow() }
                ComboBox { implicitHeight: 34; id: reviewFilter; objectName: "reviewFilter"; Layout.preferredWidth: 180; model: ["Все состояния","Не проверен","Принят","Изменён вручную"]; onActivated: polygonDialog.filterNow() }
                CheckBox { implicitHeight: 30; id: missingFilter; objectName: "missingFilter"; text: "Не заполнено обязательное"; onClicked: polygonDialog.filterNow() }
                ActionButton { text: "Сброс"; onClicked: { tableSearch.text="";tableGroup.currentIndex=0;reviewFilter.currentIndex=0;missingFilter.checked=false;polygonDialog.filterNow() } }
            }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "Найдено: " + root.appState.tableMatched + " / " + root.appState.tableTotal + " · Отмечено: " + root.appState.tableSelected; Layout.fillWidth: true; font.bold: true }
                ActionButton { text: "Отметить найденные"; objectName: "selectFilteredButton"; enabled: !root.appState.busy && root.appState.tableMatched>0; onClicked: backend.chooseFiltered(true) }
                ActionButton { text: "Снять отметки"; enabled: !root.appState.busy && root.appState.tableSelected>0; onClicked: backend.chooseFiltered(false) }
                ActionButton { text: "Отменить действие"; objectName: "tableUndoButton"; enabled: !root.appState.busy && root.appState.canUndo; onClicked: backend.undo() }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                Rectangle {
                    Layout.fillWidth: true; height: 38; color: "#e8edf4"; clip: true
                    Row {
                        x: -polygonView.contentX
                        Repeater {
                            model: root.appState.tableColumns
                            delegate: Rectangle {
                                required property var modelData
                                width: modelData.width; height: 38; color: "#e8edf4"; border.color: "#d8dee8"
                                Label { anchors.fill: parent; anchors.margins: 6; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                    text: modelData.label + (root.appState.tableSort===modelData.key ? (root.appState.tableDescending ? " ▼" : " ▲") : ""); font.bold: true; font.pixelSize: 12 }
                                TapHandler { onTapped: backend.sortTable(modelData.key) }
                            }
                        }
                    }
                }
                TableView {
                    id: polygonView; objectName: "polygonView"; Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; model: backend.polygonTable; reuseItems: true; rowSpacing: 0; columnSpacing: 0
                    columnWidthProvider: function(column) { var definition=root.appState.tableColumns[column]; return definition ? definition.width : 160 }
                    rowHeightProvider: function(row) { return 36 }
                    delegate: Rectangle {
                        required property int row
                        required property int column
                        required property string cellText
                        required property int objectId
                        required property bool rowChecked
                        required property bool missingRequired
                        objectName: "tableCell_" + objectId + "_" + column
                        implicitWidth: 150; implicitHeight: 36
                        color: rowChecked ? "#e1eafa" : missingRequired ? "#fff3de" : row%2 ? "#f6f7f9" : "#ffffff"
                        border.color: "#e4eae8"
                        CheckBox { implicitHeight: 30; anchors.centerIn: parent; visible: column===0; checked: rowChecked; enabled: !root.appState.busy; onClicked: backend.toggleTableObject(objectId) }
                        Label { anchors.fill: parent; anchors.margins: 6; visible: column>0; text: cellText; textFormat: Text.PlainText; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; font.pixelSize: 12 }
                        HoverHandler { id: cellHover }
                        ToolTip.visible: column>0 && cellHover.hovered
                        ToolTip.delay: 700
                        ToolTip.text: cellText
                        TapHandler { enabled: column>0 && !root.appState.busy; onDoubleTapped: { backend.focusTableObject(objectId); polygonDialog.close() } }
                    }
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AlwaysOn }
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
                }
                Label { visible: root.appState.tableMatched===0; text: "Нет объектов, подходящих под фильтр."; padding: 8; color: "#687383" }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11; color: "#687383"
                text: "Двойной щелчок — карта. Заголовок — сортировка. Другие поля — прокрутка вправо. Новый фильтр сбрасывает отметки. Жёлтый фон — обязательные поля не заполнены." }
            Rectangle { Layout.fillWidth: true; height: 1; color: "#dce7e4" }
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                ColumnLayout {
                    Layout.preferredWidth: 240
                    Label { text: "Массовое заполнение"; font.bold: true }
                    ComboBox { implicitHeight: 34; id: bulkField; objectName: "bulkField"; Layout.fillWidth: true; model: root.bulkFieldsModel; textRole: "label"; enabled: !root.appState.busy
                        onCurrentIndexChanged: Qt.callLater(function() { bulkValue.clearInput() }) }
                }
                AttributeInput { id: bulkValue; objectName: "bulkValue"; Layout.fillWidth: true
                    field: root.bulkFieldsModel[Math.max(0,bulkField.currentIndex)] || {key:"",label:"Значение",type:"text",choices:[]}
                    currentValue: null; active: root.appState.tableSelected>0 && !root.appState.busy }
                CheckBox { implicitHeight: 30; id: onlyEmpty; objectName: "onlyEmpty"; text: "Только пустые"; checked: true; enabled: !root.appState.busy }
                ActionButton { text: "Подготовить"; objectName: "prepareBulkButton"; highlighted: true; enabled: root.appState.tableSelected>0 && !root.appState.busy
                    onClicked: { if (backend.prepareBulk(bulkValue.field.key,JSON.stringify(bulkValue.readValue()),onlyEmpty.checked)) bulkConfirm.open() } }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText; font.pixelSize: 12
                text: root.appState.status; color: root.appState.error ? "#a43725" : "#566273" }
        }
    }
    Dialog {
        id: bulkConfirm; objectName: "bulkConfirm"; anchors.centerIn: parent; modal: true; width: 490
        title: "Применить массовое заполнение?"
        background: Rectangle { color: "#ffffff"; radius: 4; border.color: "#dce0e6" }
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label { width: 440; wrapMode: Text.WordWrap; textFormat: Text.PlainText; text: root.appState.bulkSummary }
        onAccepted: backend.applyBulk()
        onRejected: backend.cancelBulk()
    }
    Dialog {
        id: paletteDialog; objectName: "paletteDialog"
        title: "Подготовка карты · целевые заливки"; modal: false; padding: 20
        margins: 0; focus: true; closePolicy: Popup.CloseOnEscape
        enter: Transition {}
        exit: Transition {}
        anchors.centerIn: parent
        width: root.width; height: root.height
        background: Rectangle { color: "#ffffff"; radius: 0; border.color: "#dce0e6" }
        property bool picking: false
        property int focused: 0
        property bool colourPreview: true
        function includeColour(ident,enabled) {
            backend.includePalette(ident,enabled)
            backend.focusPalette(colourPreview ? focused : 0)
        }
        function toggleColour(ident) {
            if(focused===ident && colourPreview){colourPreview=false;backend.focusPalette(0)}
            else {focused=ident;colourPreview=true;backend.focusPalette(ident)}
        }
        property string rasterKey: ""
        onAboutToShow: {
            picking=false
            if(rasterKey!==root.appState.rasterUrl){rasterKey=root.appState.rasterUrl;focused=0;paletteViewport.fitMap();backend.focusPalette(0)}
        }
        contentItem: ColumnLayout {
            spacing: 12
            Label { text: "Проверьте заливки и создайте полигоны"; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label { text: root.appState.qualityNote; color: "#687383"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
                ColumnLayout {
                    Layout.preferredWidth: 270; Layout.minimumWidth: 270; Layout.maximumWidth: 270; Layout.fillHeight: true
                    Label { text: "Целевые заливки"; font.bold: true }
                    Label { text: "Нажмите цвет, чтобы увидеть его на карте. Снимайте отметку только с ненужной заливки — она не станет полигоном."; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: "#687383"; font.pixelSize: 12 }
                    ScrollView {
                        id: paletteScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                        ColumnLayout {
                            width: paletteScroll.availableWidth; spacing: 6
                            Repeater {
                                model: root.paletteModel
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    CheckBox { implicitHeight: 30; objectName: "paletteInclude_"+modelData.id; checked: modelData.enabled
                                        onClicked: { paletteDialog.includeColour(modelData.id,checked) } }
                                    Rectangle {
                                        width: 32; height: 32; radius: 6; color: modelData.color
                                        border.width: paletteDialog.focused===modelData.id ? 3 : 1
                                        border.color: paletteDialog.focused===modelData.id ? "#ec307d" : "#b8c9c6"
                                        MouseArea { anchors.fill: parent; onClicked: { paletteDialog.toggleColour(modelData.id) } }
                                    }
                                    ActionButton { objectName: "paletteFocus_"+modelData.id; text: modelData.label; Layout.fillWidth: true
                                        onClicked: { paletteDialog.toggleColour(modelData.id) } }
                                    Label { text: modelData.share+"%"; color: "#687383"; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                    Label { text: "Если две заливки — одно подразделение:"; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
                    RowLayout {
                        ComboBox { implicitHeight: 34; id: mergeTarget; objectName: "paletteMergeTarget"; Layout.fillWidth: true; model: root.paletteModel; textRole: "label" }
                        ActionButton { objectName: "mergePaletteButton"; text: "Объединить"
                            enabled: paletteDialog.focused>0 && mergeTarget.currentIndex>=0 && paletteDialog.focused!==mergeTarget.currentIndex+1
                            onClicked: { var dest=mergeTarget.currentIndex+1; backend.mergePalette(paletteDialog.focused,dest); paletteDialog.focused=dest } }
                    }
                    ActionButton { objectName: "sampleGroupButton"; text: paletteDialog.picking ? "Укажите цвет на карте…" : "Новая группа · пипетка"
                        Layout.fillWidth: true; highlighted: paletteDialog.picking; onClicked: paletteDialog.picking=!paletteDialog.picking }
                    Label { visible: !!root.appState.paletteMessage; text: root.appState.paletteMessage; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
                    ActionButton { objectName: "resetPaletteButton"; text: "Вернуть автоматический выбор"; Layout.fillWidth: true
                        onClicked: { backend.resetPalette(); paletteDialog.focused=0 } }
                }
                PaletteMap {
                    id: paletteViewport; Layout.fillWidth: true; Layout.fillHeight: true
                    columns: root.appState.width; rows: root.appState.height
                    rasterSource: root.appState.rasterUrl; paletteSource: root.appState.paletteUrl
                    evidenceSource: root.appState.qualityEvidenceUrl; showEvidence: evidenceVisible.checked
                    picking: paletteDialog.picking
                    onSampled: function(x,y) {
                        var ident=backend.samplePalette(x,y)
                        if (ident) { paletteDialog.focused=ident; paletteDialog.colourPreview=true; paletteDialog.picking=false }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                CheckBox { implicitHeight: 30; objectName: "palettePreviewToggle"; text: "Подсветка цвета"; checked: paletteDialog.colourPreview
                    onClicked: {paletteDialog.colourPreview=checked;backend.focusPalette(checked ? paletteDialog.focused : 0)} }
                CheckBox { implicitHeight: 30; id: evidenceVisible; text: "Показать признаки границ"; checked: false }
                Item { Layout.fillWidth: true }
                Label { text: "Перетащите карту для перемещения"; color: "#687383"; font.pixelSize: 12 }
                ActionButton { objectName: "paletteZoomOut"; text: "−"; enabled: paletteViewport.zoom>1
                    Accessible.name: "Уменьшить карту"; onClicked: paletteViewport.zoomAt(1/1.5,paletteViewport.width/2,paletteViewport.height/2) }
                ActionButton { objectName: "paletteZoomIn"; text: "+"; enabled: paletteViewport.zoom<32
                    Accessible.name: "Увеличить карту"; onClicked: paletteViewport.zoomAt(1.5,paletteViewport.width/2,paletteViewport.height/2) }
                ActionButton { objectName: "paletteFit"; text: "Вся карта"; onClicked: paletteViewport.fitMap() }
            }
            Label { text: "Цвета — кандидаты, а не распознанные геологические подразделения. Светлая заливка не исключается автоматически. Ручные правки сохраняются."; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11; color: "#687383" }
            RowLayout {
                Layout.fillWidth: true
                ActionButton { text: "← К карте"; onClicked: paletteDialog.close() }
                Item { Layout.fillWidth: true }
                ActionButton { objectName: "paletteRunButton"; text: "Создать полигоны"; highlighted: true; enabled: !root.appState.busy && root.paletteModel.some(function(item){return item.enabled})
                    onClicked: { paletteDialog.close(); backend.previewQuality() } }
            }
        }
    }
    Connections { target: backend; function onPalettePrepared() { paletteDialog.open() } }
    Dialog {
        id: mapSettings; objectName: "mapSettings"; title: "Настройки карт"; modal: true; anchors.centerIn: parent; width: 460
        standardButtons: Dialog.Close
        contentItem: ColumnLayout { spacing: 12
            Label { text: "Подложка в редакторе · Tiles API"; font.bold: true }
                        ActionButton {
                            text: root.appState.yandexKeyConfigured ? "Изменить ключ Tiles API…" : "Ввести ключ Tiles API…"
                            objectName: "configureYandexKey"; Layout.fillWidth: true
                            visible: true
                            enabled: !root.appState.busy; onClicked: yandexKeyDialog.open()
                        }
            Label { text: "Вкладка Яндекс Карт · JavaScript API"; font.bold: true }
            ActionButton { text: "Изменить ключ JavaScript API…"; Layout.fillWidth: true; onClicked: webKeyDialog.open() }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Стандартные ключи уже заданы. Личные ключи сохраняются на этом компьютере."; color: "#687383" }
        }
    }
    Dialog {
        id: webKeyDialog; title: "Ключ JavaScript API"; modal: true; anchors.centerIn: parent; width: 460
        onClosed: webKeyInput.text=""
        contentItem: ColumnLayout {
            TextField { implicitHeight: 34; id: webKeyInput; Layout.fillWidth: true; echoMode: TextInput.Password; maximumLength: 256; placeholderText: "Новый ключ JavaScript API" }
            Label { text: root.appState.webMapKeyError; visible: !!text; color: "#a43725"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                ActionButton { text: "Стандартный"; onClicked: {if(backend.resetWebMapKey())webKeyDialog.close()} }
                ActionButton { text: "Отмена"; onClicked: webKeyDialog.close() }
                ActionButton { text: "Сохранить"; enabled: !!webKeyInput.text.trim(); onClicked: {if(backend.setWebMapKey(webKeyInput.text))webKeyDialog.close()} }
            }
        }
    }
    TabBar {
        id: modeTabs; objectName: "modeTabs"; visible: false; currentIndex: 0
        onCurrentIndexChanged: root.viewing=currentIndex===0
        TabButton {}
        TabButton {}
        TabButton {}
    }
    ComboBox { implicitHeight: 34; id: editTool; objectName: "editTool"; visible: false; currentIndex: 5
        model: ["Выбор", "Разделение", "Общая граница", "Полигон", "Вершины", "Граница"]
        onCurrentIndexChanged: backend.setEditTool(currentIndex) }
    CheckBox { implicitHeight: 30; id: lassoToggle; objectName: "lassoToggle"; visible: false }
    Dialog { id: autoOptions; objectName: "autoOptions"; title: "Параметры оцифровки"; modal: true; anchors.centerIn: parent; width: 380
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
                            visible: true; Layout.fillWidth: true; spacing: 10
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#687383"; font.pixelSize: 12
                                text: "Пересчёт непроверенных областей. Принятые полигоны, ручные правки и применённые атрибуты сохраняются." }
                            Label { visible: autoProfile.currentIndex === 2; text: "Цвет + исходные границы"; font.bold: true; Layout.fillWidth: true }
                            Label { visible: autoProfile.currentIndex === 2; text: root.appState.qualityReady ? root.appState.qualityPalette.length+" групп заливок · параметры подобраны" : "Палитра и признаки границ будут подобраны автоматически."; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#687383"; font.pixelSize: 12 }
                            ActionButton { objectName: "advancedPaletteButton"; visible: root.appState.qualityReady && autoProfile.currentIndex === 2; text: "Посмотреть палитру…"; Layout.fillWidth: true; enabled: !root.appState.busy; onClicked: paletteDialog.open() }
                            CheckBox { implicitHeight: 30; id: otherMethods; text: "Другие методы"; checked: false }
                            ComboBox { implicitHeight: 34; id: autoProfile; objectName: "autoProfile"; Layout.fillWidth: true; visible: otherMethods.checked
                                model: ["Обычный", "Скан: подавление печати", "Цвет + границы"]; currentIndex: 2; enabled: !root.appState.busy
                                onActivated: backend.discardAuto() }
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: autoProfile.currentIndex === 1
                                font.pixelSize: 11; color: "#8b6724"
                                text: "Уменьшает помехи от надписей и штриховки. Может стирать узкие тёмные области — проверьте их на исходнике." }
                            RowLayout {
                                visible: otherMethods.checked && autoProfile.currentIndex !== 2; Layout.fillWidth: true
                                Label { text: "Группы"; Layout.fillWidth: true }
                                ComboBox { implicitHeight: 34; id: groups; model: [2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]; currentIndex: 5; enabled: !root.appState.busy; Layout.preferredWidth: 96; onActivated: backend.discardAuto() }
                            }
                            Label { visible: otherMethods.checked && autoProfile.currentIndex !== 2; text: "Подавление мелких пятен, px"; font.pixelSize: 11; color: "#687383" }
                            SpinBox { id: minimum; visible: otherMethods.checked && autoProfile.currentIndex !== 2; from: 1; to: 1000; stepSize: 20; value: 80; editable: true; enabled: !root.appState.busy; Layout.fillWidth: true; onValueModified: backend.discardAuto() }
                            ActionButton { objectName: "advancedAutoRun"; text: autoProfile.currentIndex === 2 ? "Оцифровать карту" : "Рассчитать предпросмотр"; highlighted: true; Layout.fillWidth: true
                                enabled: root.appState.rasterUrl !== "" && !root.appState.busy; onClicked: { autoOptions.close(); modeTabs.currentIndex=0; root.request("run") } }
                            ProgressBar { visible: root.appState.qualityBusy; from: 0; to: 100; value: root.appState.qualityProgress; Layout.fillWidth: true }
                            Label { visible: root.appState.qualityBusy; text: root.appState.qualityStage + (root.appState.qualityCancelPending ? "" : " · " + root.appState.qualityETA); Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
                            ActionButton { objectName: "cancelQualityButton"; visible: root.appState.qualityBusy; text: "Остановить расчёт"; Layout.fillWidth: true; enabled: !root.appState.qualityCancelPending; onClicked: backend.cancelQuality() }
                        }
    }
    Dialog { id: promptOptions; objectName: "promptOptions"; title: "Уточнить область"; modal: false; x: 52; y: 62; width: 300
        standardButtons: Dialog.Close
        onOpened: modeTabs.currentIndex=1
        onClosed: if(modeTabs.currentIndex===1)modeTabs.currentIndex=0
        contentItem: ColumnLayout {
                            visible: true; Layout.fillWidth: true; spacing: 9
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#687383"; font.pixelSize: 12
                                text: "Зелёные точки — нужная область, красные — лишнее. Рамка появится после первой зелёной точки." }
                            ComboBox { implicitHeight: 34;
                                id: hintMode; objectName: "hintMode"; Layout.fillWidth: true
                                model: ["Выбрать полигон", "Включить (+)", "Исключить (−)"]
                                currentIndex: 1; enabled: !root.appState.busy
                            }
                            Label { text: "Метод"; font.pixelSize: 11; color: "#687383" }
                            ComboBox { implicitHeight: 34;
                                id: hintMethod; Layout.fillWidth: true
                                model: root.appState.neuralAvailable ? ["Цвет + подсказки", "MobileSAM · CPU"] : ["Цвет + подсказки"]
                                enabled: !root.appState.busy
                                onActivated: backend.invalidateProposal()
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "Рамка, px"; Layout.fillWidth: true; font.pixelSize: 11 }
                                ComboBox { implicitHeight: 34; id: roiSize; model: [256,512,768]; currentIndex: 1; Layout.preferredWidth: 96
                                    enabled: !root.appState.busy && root.appState.hintCount === 0 }
                            }
                            Label { text: "Допуск цвета: " + Math.round(tolerance.value); visible: hintMethod.currentIndex === 0; font.pixelSize: 11; color: "#687383" }
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
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#687383"; font.pixelSize: 11
                                text: "Принятые области защищены от автообработки. Изменить вручную: вкладка «Правка»." }
                        }
    }
    Dialog {
        id: helpDialog; objectName: "helpDialog"; title: "GeoTrace · краткая справка"
        anchors.centerIn: parent; width: 620; modal: true; focus: true; standardButtons: Dialog.Close
        background: Rectangle { color: "#ffffff"; border.color: "#dce0e6"; radius: 4 }
        header: Label { text: helpDialog.title; padding: 18; bottomPadding: 12; font.pixelSize: 18; font.bold: true; color: "#252f3d" }
        footer: Item { implicitHeight: 50
            ActionButton { anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter
                text: "Закрыть"; description: "Закрыть справку"; shortcutLabel: "Esc"; onClicked: helpDialog.close() }
        }
        contentItem: ColumnLayout { spacing: 14
            Label { text: "Работа с картой"; font.bold: true }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "Начните с просмотра. Для оцифровки нажмите «Оцифровать», проверьте палитру и примените результат. Спорные участки собраны в «Проверке». Правки можно отменять." }
            GridLayout { columns: 2; columnSpacing: 28; rowSpacing: 7
                Label { text: "Q / V / L"; font.bold: true } Label { text: "Просмотр / выбор / лассо" }
                Label { text: "B / C / P / N"; font.bold: true } Label { text: "Граница / разделение / полигон / вершины" }
                Label { text: "D / K / R"; font.bold: true } Label { text: "Оцифровка / палитра / проверка" }
                Label { text: "G / A / T"; font.bold: true } Label { text: "Слои / атрибуты / таблица" }
                Label { text: "[ / ]"; font.bold: true } Label { text: "Предыдущий / следующий участок проверки" }
                Label { text: "Shift"; font.bold: true } Label { text: "Короткое нажатие: исходник / результат" }
                Label { text: "F / + / −"; font.bold: true } Label { text: "Вся карта / приблизить / отдалить" }
                Label { text: "Enter"; font.bold: true } Label { text: "Предпросмотр, применение или объединение" }
                Label { text: "Esc / Backspace"; font.bold: true } Label { text: "Сбросить линию или выбор / убрать точку" }
                Label { text: root.commandKey+"+O / S / Shift+S"; font.bold: true } Label { text: "Открыть / сохранить / сохранить как" }
                Label { text: root.commandKey+"+Z / Shift+Z"; font.bold: true } Label { text: "Отменить / повторить" }
                Label { text: root.commandKey+"+Enter"; font.bold: true } Label { text: "Применить поля в открытых атрибутах" }
                Label { text: "F1"; font.bold: true } Label { text: "Открыть эту справку" }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#566273"
                text: "Колесо меняет масштаб. Средняя кнопка перемещает карту в любом инструменте; в просмотре можно тянуть левой. Лассо с Shift добавляет полигоны к выбору. Клавиши инструментов работают и в русской раскладке; при вводе текста они отключены." }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#566273"
                text: "«Граница» позволяет тянуть общую границу сразу у двух соседей. «Перерисовать общую границу» в меню ⋯: выберите двух соседей и нарисуйте новую линию; внешний контур сохраняется." }
        }
    }
    Shortcut { autoRepeat: false; sequences: ["F1"]; onActivated: helpDialog.open() }
    Shortcut { autoRepeat: false; sequences: ["Q", "Й"]; enabled: root.mapKeysEnabled; onActivated: root.chooseView() }
    Shortcut { autoRepeat: false; sequences: ["V", "М"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(0,false) }
    Shortcut { autoRepeat: false; sequences: ["L", "Д"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(0,true) }
    Shortcut { autoRepeat: false; sequences: ["B", "И"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(5,false) }
    Shortcut { autoRepeat: false; sequences: ["C", "С"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(1,false) }
    Shortcut { autoRepeat: false; sequences: ["P", "З"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(3,false) }
    Shortcut { autoRepeat: false; sequences: ["N", "Т"]; enabled: root.mapKeysEnabled; onActivated: root.chooseTool(4,false) }
    Shortcut { autoRepeat: false; sequences: ["D", "В"]; enabled: root.mapKeysEnabled; onActivated: { modeTabs.currentIndex=0;root.request("run") } }
    Shortcut { autoRepeat: false; sequences: ["K", "Л"]; enabled: root.mapKeysEnabled && root.appState.qualityReady; onActivated: paletteDialog.open() }
    Shortcut { autoRepeat: false; sequences: ["R", "К"]; enabled: root.mapKeysEnabled && root.appState.reviewCount>0; onActivated: root.reviewOpen=!root.reviewOpen }
    Shortcut { autoRepeat: false; sequences: ["[", "Х"]; enabled: root.mapKeysEnabled && root.reviewOpen && reviewPanel.navigationReady && root.appState.reviewPosition>1; onActivated: {backend.previousReviewSite();reviewPanel.selectTool()} }
    Shortcut { autoRepeat: false; sequences: ["]", "Ъ"]; enabled: root.mapKeysEnabled && root.reviewOpen && reviewPanel.navigationReady && root.appState.reviewPosition<root.appState.reviewCount; onActivated: {backend.nextReviewSite();reviewPanel.selectTool()} }
    Shortcut { autoRepeat: false; sequences: ["G", "П"]; enabled: root.mapKeysEnabled; onActivated: root.sidebarOpen=!root.sidebarOpen }
    Shortcut { autoRepeat: false; sequences: ["A", "Ф"]; enabled: root.mapKeysEnabled; onActivated: root.attributesPinned=!root.attributesPinned }
    Shortcut { autoRepeat: false; sequences: ["T", "Е"]; enabled: root.mapKeysEnabled && root.appState.count>0; onActivated: polygonDialog.open() }
    Shortcut { autoRepeat: false; sequences: ["F", "А"]; enabled: root.mapKeysEnabled; onActivated: viewport.fit() }
    Shortcut { autoRepeat: true; sequences: ["+", "="]; enabled: root.mapKeysEnabled; onActivated: viewport.zoomAtCenter(1.3) }
    Shortcut { autoRepeat: true; sequences: ["-"]; enabled: root.mapKeysEnabled; onActivated: viewport.zoomAtCenter(1/1.3) }
    Shortcut { autoRepeat: false; sequences: ["Escape"]; enabled: root.mapKeysEnabled; onActivated: root.escapeMap() }
    Shortcut { autoRepeat: false; sequences: ["Backspace"]; enabled: root.mapKeysEnabled && root.appState.editPoints.length>0; onActivated: backend.removeEditPoint() }
    Shortcut { autoRepeat: false; sequences: ["Return","Enter"]; enabled: root.mapKeysEnabled && (root.appState.autoReady || !root.viewing) && !root.originalOnly; onActivated: root.finishEdit() }
    Shortcut { autoRepeat: false; sequences: ["Ctrl+Return", "Ctrl+Enter"]; enabled: root.attributesShown && attributesPanel.scopeData.count>0 && !root.appState.busy && !root.autosavePaused && !polygonDialog.visible && workspaceTabs.currentIndex===0; onActivated: applyAttributesButton.clicked() }
    Shortcut { autoRepeat: false; sequences: [StandardKey.Open]; enabled: !root.autosavePaused && !polygonDialog.visible; onActivated: root.request("open") }
    Shortcut { autoRepeat: false; sequences: [StandardKey.Undo]; enabled: !root.autosavePaused && !backend.isTextInput(root.activeFocusItem) && !root.appState.busy; onActivated: backend.undo() }
    Shortcut { autoRepeat: false; sequences: [StandardKey.Redo]; enabled: !root.autosavePaused && !backend.isTextInput(root.activeFocusItem) && !root.appState.busy; onActivated: backend.redo() }
    Shortcut { autoRepeat: false; sequences: [StandardKey.Save]; enabled: root.appState.rasterUrl !== "" && !root.appState.busy && !root.autosavePaused && !polygonDialog.visible; onActivated: root.saveProject() }
    Shortcut { autoRepeat: false; sequences: [StandardKey.SaveAs]; enabled: root.appState.rasterUrl !== "" && !root.appState.busy && !root.autosavePaused && !polygonDialog.visible; onActivated: projectSaveDialog.open() }

    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 48; color: "#ffffff"
            RowLayout {
                id: headerLeft; anchors.left: parent.left; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 16
                Label { text: "GeoTrace"; color: "#252b34"; font.pixelSize: 20; font.bold: true }
                TabBar {
                    id: workspaceTabs; objectName: "workspaceTabs"; Layout.preferredWidth: 205; Layout.preferredHeight: 32; clip: true
                    background: Item {}
                    component WorkspaceTab: TabButton {
                        id: tab; height: 32; padding: 7; hoverEnabled: true
                        contentItem: Label { text: tab.text; font.pixelSize: 12; color: tab.checked ? "#28549c" : "#687383"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: tab.hovered ? "#f0f3f8" : "transparent"
                            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 2; color: "#356ac3"; visible: tab.checked }
                        }
                        ToolTip.visible: hovered; ToolTip.delay: 550; ToolTip.text: text==="Карта" ? "Оцифровка и правка карты" : "Обзор проекта на Яндекс Картах"
                    }
                    WorkspaceTab { objectName: "digitizerTab"; text: "Карта" }
                    WorkspaceTab { objectName: "yandexWebTab"; text: "Яндекс Карты" }
                    onCurrentIndexChanged: {
                        if (currentIndex===1 && !root.webMapOpened) { root.webMapOpened=true;backend.openWebMap() }
                    }
                }
            }
            Label {
                objectName: "projectTitle"; anchors.centerIn: parent
                width: Math.max(80,parent.width-2*Math.max(headerLeft.width+28,headerRight.width+28))
                text: (root.appState.dirty ? "●  " : "")+(root.appState.projectName || root.appState.filename || "Новый проект")
                color: "#252f3d"; font.bold: true; font.pixelSize: 14; elide: Text.ElideMiddle; horizontalAlignment: Text.AlignHCenter
                ToolTip.visible: titleHover.hovered; ToolTip.delay: 550; ToolTip.text: text
                HoverHandler { id: titleHover }
            }
            RowLayout {
                id: headerRight; anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                ActionButton { text: "Открыть…"; description: "Открыть карту или проект"; shortcutLabel: root.commandKey+"+O"; enabled: !root.appState.busy; onClicked: root.request("open") }
                ActionButton { text: "Сохранить"; objectName: "saveProjectButton"; shortcutLabel: root.commandKey+"+S"; highlighted: true
                    enabled: !!root.appState.rasterUrl && !root.appState.busy; onClicked: root.saveProject() }
                ActionButton { text: "↶"; objectName: "undoButton"; description: "Отменить действие"; shortcutLabel: root.commandKey+"+Z"
                    enabled: root.appState.canUndo && !root.appState.busy; onClicked: backend.undo() }
                ActionButton { text: "↷"; objectName: "redoButton"; description: "Повторить действие"; shortcutLabel: root.commandKey+"+Shift+Z"
                    enabled: root.appState.canRedo && !root.appState.busy; onClicked: backend.redo() }
                ActionButton { text: "Проект ▾"; objectName: "projectMenuButton"; onClicked: projectMenu.popup()
                    Menu { id: projectMenu
                        MenuItem { text: "Сохранить как…"; ToolTip.visible: hovered; ToolTip.delay: 550; ToolTip.text: "Сохранить отдельную копию · "+root.commandKey+"+Shift+S"; enabled: !!root.appState.rasterUrl && !root.appState.busy; onTriggered: projectSaveDialog.open() }
                        MenuItem { text: "Экспорт GeoPackage…"; enabled: root.appState.count>0 && !root.appState.busy; onTriggered: saveDialog.open() }
                        MenuSeparator {}
                        MenuItem { text: "Поля атрибутов…"; enabled: !!root.appState.rasterUrl && !root.appState.busy; onTriggered: schemaManager.open() }
                        MenuItem { objectName: "openMapSettings"; text: "Настройки карт…"; onTriggered: mapSettings.open() }
                    }
                }
                ActionButton { text: "?"; objectName: "helpButton"; description: "Краткая справка"; shortcutLabel: "F1"; onClicked: helpDialog.open() }
            }
        }

        RowLayout {
            visible: workspaceTabs.currentIndex===0
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 1
            Rectangle { Layout.preferredWidth: 42; Layout.fillHeight: true; color: "#f4f5f7"
                Column { anchors.top: parent.top; anchors.topMargin: 6; anchors.horizontalCenter: parent.horizontalCenter; spacing: 4
                    MapTool { objectName: "viewToolButton"; iconName: "view"; width: 34; height: 34
                        Accessible.name: "Просмотр"; description: "Перемещение карты без выбора полигонов"; shortcutLabel: "Q"
                        highlighted: root.viewing; onClicked: root.chooseView() }
                    Repeater { model: ["↖", "⌁", "↔", "╱", "⬡", "⋮"]
                        delegate: MapTool { required property int index; required property string modelData
                            iconName: ["cursor","lasso","boundary","split","polygon","vertices"][index]
                            objectName: "toolButton_"+index; width: 34; height: 34; implicitHeight: 34; leftPadding: 0; rightPadding: 0; text: modelData
                            Accessible.name: ["Выбрать полигоны", "Лассо", "Изменить границу", "Разделить полигон", "Создать полигон", "Изменить вершины"][index]
                            description: ["Щёлкайте по полигонам для выбора","Выберите полигоны обводкой","Перетяните общую границу","Нарисуйте линию разделения","Обведите новый полигон","Переместите вершину"][index]; shortcutLabel: ["V","L","B","C","P","N"][index]
                            highlighted: !root.viewing && modeTabs.currentIndex===2 && editTool.currentIndex===[0,0,5,1,3,4][index] && (index>1 || lassoToggle.checked===(index===1))
                            enabled: !root.appState.busy && !!root.appState.rasterUrl; onClicked: root.chooseTool([0,0,5,1,3,4][index],index===1)
                        }
                    }
                    ActionButton { text: "⋯"; description: "Дополнительные инструменты"; objectName: "moreEditTools"; width: 34; leftPadding: 0; rightPadding: 0; onClicked: advancedTools.popup()
                        Menu { id: advancedTools
                            MenuItem { text: "Перерисовать общую границу"; ToolTip.visible: hovered; ToolTip.delay: 550; ToolTip.text: "Заменить границу двух соседей нарисованной линией"; onTriggered: root.chooseTool(2,false) }
                            MenuItem { text: "Уточнить область…"; onTriggered: promptOptions.open() }
                            MenuItem { text: "Параметры оцифровки…"; onTriggered: autoOptions.open() }
                        }
                    }
                    Rectangle { width: 26; height: 1; color: "#d9dde3"; anchors.horizontalCenter: parent.horizontalCenter }
                    MapTool { iconName: "layers"; objectName: "layersButton"; width: 34; leftPadding: 0; rightPadding: 0; highlighted: root.sidebarOpen
                        Accessible.name: "Слои"; description: "Показать / скрыть слои"; shortcutLabel: "G"
                        onClicked: {root.layersMode=true;root.sidebarOpen=!root.sidebarOpen} }
                }
            }
            Rectangle {
                id: toolsSidebar; objectName: "toolsSidebar"; visible: root.sidebarOpen
                parent: viewport; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; z: 120
                width: 230; color: "#ffffff"
                MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: function(wheel){wheel.accepted=true} }
                ScrollView {
                    id: sidebarScroll; objectName: "sidebarScroll"
                    anchors.fill: parent; anchors.margins: 16; clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AlwaysOn; width: 6
                        background: Rectangle { color: "#f0f2f5"; radius: 3 }
                        contentItem: Rectangle { implicitWidth: 6; radius: 3; color: parent.pressed ? "#356ac3" : "#b4bfce" }
                    }
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: sidebarScroll.availableWidth - 12; spacing: 10
                        RowLayout { Layout.fillWidth: true
                            Label { text: "Слои"; font.bold: true; font.pixelSize: 15; Layout.fillWidth: true }
                            ActionButton { text: "×"; description: "Закрыть слои"; objectName: "closeLayers"; onClicked: root.sidebarOpen=false }
                        }

                        ColumnLayout { visible: true; Layout.fillWidth: true; spacing: 8
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#dce0e6" }
                        Label { text: "ВИДИМОСТЬ И ПРОЗРАЧНОСТЬ"; color: "#687383"; font.pixelSize: 10; font.letterSpacing: 1.5 }
                        RowLayout { Layout.fillWidth: true
                        CheckBox { implicitHeight: 30;
                            id: rasterVisible; objectName: "rasterVisible"; text: "Исходная карта"
                            checked: root.appState.layerSettings.raster_visible; enabled: !root.appState.busy
                            onClicked: backend.setLayer("raster_visible", checked)
                        }
                            Item { Layout.fillWidth: true }
                            Label { text: Math.round(rasterOpacity.value*100)+"%"; color: "#687383" }
                        }
                        Slider {
                            id: rasterOpacity; objectName: "rasterOpacity"; implicitHeight: 24; from: 0; to: 1
                            value: root.appState.layerSettings.raster_opacity; Layout.fillWidth: true; enabled: !root.appState.busy
                            onMoved: backend.setLayer("raster_opacity", value)
                        }
                        RowLayout { Layout.fillWidth: true
                        CheckBox { implicitHeight: 30;
                            id: overlayVisible; objectName: "overlayVisible"; text: "Полигоны"
                            checked: root.appState.layerSettings.polygons_visible
                            enabled: (root.appState.count > 0 || root.appState.autoReady) && !root.appState.busy
                            onClicked: backend.setLayer("polygons_visible", checked)
                        }
                            Item { Layout.fillWidth: true }
                            Label { text: Math.round(opacitySlider.value*100)+"%"; color: "#687383" }
                        }
                        Slider {
                            id: opacitySlider; objectName: "opacitySlider"; implicitHeight: 24; from: 0; to: 1
                            value: root.appState.layerSettings.polygons_opacity; Layout.fillWidth: true; enabled: !root.appState.busy
                            onMoved: backend.setLayer("polygons_opacity", value)
                        }
                        CheckBox { implicitHeight: 30;
                            id: basemapVisible; objectName: "basemapVisible"; text: "Подложка"
                            checked: root.appState.layerSettings.basemap_visible
                            enabled: !!root.appState.rasterUrl && !root.appState.busy
                            onClicked: {
                                backend.setLayer("basemap_visible", checked)
                                if (checked && root.appState.layerSettings.basemap_source === "local" && !root.appState.layerSettings.basemap_file)
                                    basemapFileDialog.open()
                                if (checked && root.appState.layerSettings.basemap_source === "yandex" && !root.appState.yandexKeyConfigured)
                                    yandexKeyDialog.open()
                            }
                        }
                        ComboBox { implicitHeight: 34;
                            id: basemapSource; objectName: "basemapSource"; Layout.fillWidth: true
                            model: ["Спутник EOX · 2016", "Яндекс · схема", "Свой GeoTIFF"]
                            currentIndex: root.appState.layerSettings.basemap_source === "local" ? 2 : root.appState.layerSettings.basemap_source === "yandex" ? 1 : 0
                            enabled: !!root.appState.rasterUrl && !root.appState.busy
                            onActivated: {
                                backend.setLayer("basemap_source", ["eox2016", "yandex", "local"][currentIndex])
                                if (currentIndex === 2 && !root.appState.layerSettings.basemap_file) basemapFileDialog.open()
                                if (currentIndex === 1 && !root.appState.yandexKeyConfigured) yandexKeyDialog.open()
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ActionButton {
                                text: "Выбрать снимок…"; objectName: "openBasemapFile"
                                visible: basemapSource.currentIndex === 2; Layout.fillWidth: true
                                enabled: !!root.appState.rasterUrl && !root.appState.busy
                                onClicked: basemapFileDialog.open()
                            }
                            ActionButton {
                                text: "Обновить"; objectName: "refreshBasemap"
                                visible: basemapVisible.checked; enabled: !root.appState.basemapLoading
                                onClicked: backend.refreshBasemap()
                            }
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; textFormat: Text.PlainText
                            visible: basemapVisible.checked
                            text: root.appState.basemapError || (root.appState.basemapLoading ? "Загружаю подложку…" : root.appState.basemapNote)
                            color: root.appState.basemapError ? "#a43725" : "#687383"; font.pixelSize: 11
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ActionButton {
                                text: "Обрезать KML"; objectName: "loadAreaButton"; Layout.fillWidth: true
                                enabled: !!root.appState.rasterUrl && !root.appState.busy
                                onClicked: areaFileDialog.open()
                            }
                            ActionButton {
                                text: "Вернуть"; objectName: "clearAreaButton"
                                enabled: !!root.appState.areaName && !root.appState.busy
                                ToolTip.visible: hovered
                                ToolTip.text: "Вернуть карту целиком. Для восстановления полигонов используйте отмену обрезки."
                                onClicked: backend.clearArea()
                            }
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11
                            text: root.appState.areaName || "Обработка всей карты"; color: "#687383"
                        }
                        CheckBox { implicitHeight: 30;
                            id: areaVisible; objectName: "areaVisible"; text: "Граница области"
                            checked: root.appState.layerSettings.area_visible
                            enabled: !!root.appState.areaName && !root.appState.busy
                            onClicked: backend.setLayer("area_visible", checked)
                        }
                        Label { text: "ЦВЕТОВЫЕ ГРУППЫ"; color: "#687383"; font.pixelSize: 10; font.letterSpacing: 1.1 }
                        Repeater {
                            model: root.appState.colors
                            delegate: RowLayout {
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true; height: 22
                                TapHandler { onTapped: {root.attributesPinned=true;attributeTarget.currentIndex=1;attributeGroup.currentIndex=index+1} }
                                Rectangle { width: 14; height: 14; radius: 4; color: modelData.color; border.color: "#aebfbd" }
                                Label { text: modelData.label; Layout.fillWidth: true; font.pixelSize: 12 }
                                Label { text: modelData.count; color: "#687383"; font.pixelSize: 11 }
                            }
                        }
                        CheckBox { implicitHeight: 30; text: "Подсветка сомнений"; checked: showReviewMark.checked
                            enabled: root.reviewOpen && root.appState.reviewCount>0; onClicked: showReviewMark.checked=checked }
                        }

                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                Flow { Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 4; padding: 4
                    ActionButton { text: "Оцифровать"; description: "Подготовить автоматическую оцифровку"; shortcutLabel: "D"; objectName: "autoPreviewButton"; enabled: !!root.appState.rasterUrl && !root.appState.busy
                        onClicked: {modeTabs.currentIndex=0; root.request("run")} }
                    ActionButton { text: "Палитра"; objectName: "openPaletteButton"; shortcutLabel: "K"; description: "Подготовить группы цветов"; enabled: root.appState.qualityReady && !root.appState.busy; onClicked: paletteDialog.open() }
                    ActionButton { text: "Проверка · "+root.appState.reviewCount; objectName: "reviewToggle"; shortcutLabel: "R"; description: "Открыть / закрыть проверку"; highlighted: root.reviewOpen; enabled: root.appState.reviewCount>0
                        onClicked: root.reviewOpen=!root.reviewOpen }
                    ActionButton { id: compareButton; objectName: "compareOriginalButton"; text: root.originalOnly ? "Исходник" : "Результат"; highlighted: root.originalOnly; enabled: !!root.appState.rasterUrl
                        onClicked: root.originalOnly=!root.originalOnly; description: "Исходник / результат"; shortcutLabel: "Shift" }
                    ActionButton { text: "Таблица"; objectName: "openTableButton"; shortcutLabel: "T"; description: "Открыть таблицу полигонов"; enabled: root.appState.count>0 && !root.appState.busy; onClicked: polygonDialog.open() }
                    ActionButton { text: "Атрибуты"; objectName: "attributesToggle"; shortcutLabel: "A"; description: "Показать / скрыть атрибуты"; highlighted: root.attributesPinned; onClicked: root.attributesPinned=!root.attributesPinned }
                    ActionButton { text: "−"; objectName: "zoomOutButton"; description: "Уменьшить карту"; shortcutLabel: "−"; onClicked: viewport.zoomAtCenter(1/1.3) }
                    ActionButton { text: "Вписать"; objectName: "fitMapButton"; description: "Показать всю карту"; shortcutLabel: "F"; onClicked: viewport.fit() }
                    ActionButton { text: "+"; objectName: "zoomInButton"; description: "Увеличить карту"; shortcutLabel: "+"; onClicked: viewport.zoomAtCenter(1.3) }
                }

                Rectangle {
                    id: reviewPanel; objectName: "reviewPanel"
                    parent: viewport; x: root.sidebarOpen ? toolsSidebar.width+4 : 4; y: 4; z: 110
                    width: viewport.usableWidth-8; height: implicitHeight
                    visible: root.reviewOpen && (root.appState.reviewCount>0 || root.appState.reviewLogged>0)
                    Layout.fillWidth: true; implicitHeight: reviewRows.implicitHeight+16; color: "#f1f4f9"
                    readonly property bool navigationReady: !root.appState.busy && !root.appState.geometryReady && !root.appState.editPoints.length && !mapMouse.edgeDragging && !mapMouse.vertexDragging && !mapMouse.lassoDragging
                    function selectTool() { var tool=root.appState.reviewTool; if(modeTabs.currentIndex===2 && tool>=0){root.viewing=false;editTool.currentIndex=tool===2 ? 5 : tool} }
                    ColumnLayout { id: reviewRows; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; spacing: 4
                        Flow { Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 4
                            ActionButton { objectName: "previousReview"; shortcutLabel: "["; text: "←"; Accessible.name: "Предыдущее сомнение"; enabled: reviewPanel.navigationReady && root.appState.reviewPosition>1; onClicked: {backend.previousReviewSite();reviewPanel.selectTool()} }
                            Label { text: root.appState.reviewPosition ? "Участок "+root.appState.reviewPosition+" / "+root.appState.reviewCount : "Проверить: "+root.appState.reviewCount; font.bold: true }
                            ActionButton { objectName: "nextReview"; shortcutLabel: "]"; text: "→"; Accessible.name: "Следующее сомнение"; enabled: reviewPanel.navigationReady && root.appState.reviewPosition<root.appState.reviewCount; onClicked: {backend.nextReviewSite();reviewPanel.selectTool()} }
                            CheckBox { implicitHeight: 30; id: showReviewMark; objectName: "showReviewMark"; text: "Подсветка"; checked: true }
                            ActionButton { text: root.appState.autoReady ? "Применить и править" : "Исправить"; visible: root.appState.reviewPosition>0 && modeTabs.currentIndex===0; enabled: reviewPanel.navigationReady
                                onClicked: {if(!root.appState.autoReady || backend.applyForReview()){modeTabs.currentIndex=2;reviewPanel.selectTool()}} }
                            ActionButton { text: "Подтвердить участок"; visible: root.appState.reviewActive; enabled: reviewPanel.navigationReady; onClicked: {backend.confirmReviewSite();reviewPanel.selectTool()} }
                            ActionButton { text: "×"; objectName: "closeReview"; Accessible.name: "Закрыть проверку"; onClicked: root.reviewOpen=false }
                            ActionButton { text: "Журнал…"; visible: root.appState.reviewLogged>0; onClicked: reviewSaveDialog.open() }
                        }
                        Label { text: root.appState.reviewText+(root.appState.reviewDecision ? " · "+root.appState.reviewDecision : ""); Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: !!text; color: "#566273" }
                    }
                }
                Flow { id: contextActions; objectName: "contextActions"
                    parent: viewport; x: root.sidebarOpen ? toolsSidebar.width+4 : 4; y: reviewPanel.visible ? reviewPanel.height+8 : 4; z: 110
                    width: viewport.usableWidth-8; height: implicitHeight; padding: 5
                    Rectangle { parent: viewport; x: contextActions.x; y: contextActions.y; width: contextActions.width; height: contextActions.height; visible: contextActions.visible; color: "#f5f7fa"; border.color: "#dce0e6"; radius: 3; z: 109; MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: function(wheel){wheel.accepted=true} } }
                    visible: root.appState.autoReady || (!root.viewing && modeTabs.currentIndex===2 && (root.appState.editSelected.length || root.appState.editPoints.length || root.appState.geometryReady || editTool.currentIndex===5)); spacing: 4
                    Label { text: "Выбрано: "+root.appState.editSelected.length; padding: 6; visible: root.appState.editSelected.length>0 }
                    ActionButton { text: "Объединить · "+root.appState.editSelected.length; id: quickMergeButton; objectName: "quickMergeButton"; description: "Объединить выбранные полигоны"; shortcutLabel: "Enter"; visible: editTool.currentIndex===0 && root.appState.editSelected.length>1; enabled: !root.appState.busy; onClicked: backend.mergeSelected() }
                    ActionButton { text: "Предпросмотр"; objectName: "mergePreviewButton"; visible: editTool.currentIndex===0 && root.appState.editSelected.length>1; enabled: !root.appState.busy; onClicked: backend.previewGeometry("merge") }
                    ActionButton { text: "Атрибуты"; visible: root.appState.editSelected.length>0; onClicked: root.attributesPinned=true }
                    ActionButton { text: "Снять выбор"; shortcutLabel: "Esc"; visible: root.appState.editSelected.length>0; enabled: !root.appState.busy; onClicked: backend.clearEditSelection() }
                    CheckBox { implicitHeight: 30; id: singleEdge; objectName: "singleEdge"; visible: modeTabs.currentIndex===2 && editTool.currentIndex===5; text: "Одна вершина" }
                    ActionButton { text: "− точка"; shortcutLabel: "Backspace"; visible: root.appState.editPoints.length>0; enabled: !root.appState.busy; onClicked: backend.removeEditPoint() }
                    ActionButton { text: "Сбросить линию"; shortcutLabel: "Esc"; visible: root.appState.editPoints.length>0; enabled: !root.appState.busy; onClicked: backend.clearEditLine() }
                    ActionButton { text: "Предпросмотр"; id: linePreviewButton; objectName: "linePreviewButton"; description: "Проверить нарисованную правку"; shortcutLabel: "Enter"; visible: modeTabs.currentIndex===2 && editTool.currentIndex>=1 && editTool.currentIndex<=3
                        enabled: !root.appState.busy && root.appState.editPoints.length >= (editTool.currentIndex===3 ? 3 : 2) && (editTool.currentIndex===3 || root.appState.editSelected.length===(editTool.currentIndex===1 ? 1 : 2))
                        onClicked: backend.previewGeometry(editTool.currentIndex===3 ? "draw" : editTool.currentIndex===1 ? "split" : "boundary") }
                    ActionButton { text: "Применить правку"; objectName: "applyGeometryButton"; description: "Применить подготовленную правку"; shortcutLabel: "Enter"; highlighted: true; visible: root.appState.geometryReady; enabled: !root.appState.busy; onClicked: backend.acceptGeometry() }
                    Label { text: root.appState.autoSummary; width: Math.min(implicitWidth,contextActions.width-10); wrapMode: Text.WordWrap; padding: 6; visible: root.appState.autoReady; color: "#566273" }
                    CheckBox { implicitHeight: 30; id: showAutoPreview; objectName: "showAutoPreview"; text: "Новый результат"; visible: root.appState.autoReady; checked: true }
                    Connections { target: backend; function onChanged(){if(!root.appState.autoReady)showAutoPreview.checked=true} }
                    ActionButton { text: "Применить результат"; objectName: "autoAcceptButton"; shortcutLabel: "Enter"; visible: root.appState.autoReady; enabled: !root.appState.busy; highlighted: true; onClicked: backend.acceptAuto() }
                    ActionButton { text: "Отклонить"; objectName: "autoDiscardButton"; visible: root.appState.autoReady; enabled: !root.appState.busy; onClicked: backend.discardAuto() }
                }

                Flickable {
                    id: viewport; objectName: "mapViewport"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Rectangle {
                        parent: viewport
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        anchors.margins: 8; z: 100
                        width: Math.min(600, viewport.usableWidth-(root.appState.layerSettings.basemap_source === "yandex" ? 112 : 16)); height: creditText.implicitHeight+12
                        color: "#eeffffff"; radius: 3
                        visible: root.appState.layerSettings.basemap_visible && !!root.appState.basemapUrl
                        Label {
                            id: creditText; objectName: "basemapCredit"
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 6
                            text: root.appState.basemapCredit; textFormat: Text.PlainText
                            wrapMode: Text.WordWrap; font.pixelSize: 10; color: "#263f43"
                        }
                        TapHandler {
                            enabled: root.appState.layerSettings.basemap_source !== "local"
                            onTapped: Qt.openUrlExternally(root.appState.layerSettings.basemap_source === "yandex"
                                ? "https://yandex.ru/legal/maps_termsofuse/" : "https://cloudless.eox.at/documentation/faq")
                        }
                    }
                    Image {
                        objectName: "yandexLogo"; parent: viewport
                        anchors.right: parent.right; anchors.bottom: parent.bottom; z: 101
                        anchors.rightMargin: root.attributesShown ? attributesPanel.width+8 : 0
                        width: 88; height: 48; source: "yandex-logo.png"; smooth: true
                        visible: root.appState.layerSettings.basemap_visible && root.appState.layerSettings.basemap_source === "yandex" && !!root.appState.basemapUrl
                        TapHandler { onTapped: Qt.openUrlExternally("https://yandex.ru/maps/") }
                    }
                    Connections { target: backend; function onBasemapConfigurationChanged() { basemapTimer.restart() } }
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    property real zoom: 1
                    // Camera offsets change only on an explicit fit, never when
                    // an inspector, review bar or contextual action appears.
                    property real frameX: 0
                    property real frameY: 0
                    readonly property real usableWidth: Math.max(1,width-(root.attributesShown ? attributesPanel.width+8 : 0)-(root.sidebarOpen ? toolsSidebar.width+4 : 0))
                    readonly property real paddingX: root.appState.layerSettings.basemap_visible ? Math.ceil(root.appState.width*root.appState.basemapMargin) : 0
                    readonly property real paddingY: root.appState.layerSettings.basemap_visible ? Math.ceil(root.appState.height*root.appState.basemapMargin) : 0
                    property real previousPaddingX: 0
                    property real previousPaddingY: 0
                    onPaddingXChanged: Qt.callLater(updateContext)
                    onPaddingYChanged: Qt.callLater(updateContext)
                    function updateContext() {
                        if (!root.appState.width || !root.appState.height) return
                        var oldFit = Math.min((width-40)/(root.appState.width+2*previousPaddingX), (height-40)/(root.appState.height+2*previousPaddingY))
                        var fitted = Math.abs(zoom-oldFit) < .001
                        var dx = (paddingX-previousPaddingX)*zoom
                        var dy = (paddingY-previousPaddingY)*zoom
                        previousPaddingX = paddingX; previousPaddingY = paddingY
                        if (fitted) fit()
                        else {
                            contentX = Math.max(0, Math.min(contentWidth-width, contentX+dx))
                            contentY = Math.max(0, Math.min(contentHeight-height, contentY+dy))
                        }
                    }
                    property string basemapKey: root.appState.rasterUrl + "|" + JSON.stringify(root.appState.layerSettings)
                    onBasemapKeyChanged: basemapTimer.restart()
                    property bool fitMode: false
                    property bool fitting: false
                    onZoomChanged: { if(!fitting)fitMode=false;basemapTimer.restart() }
                    onContentXChanged: basemapTimer.restart()
                    onContentYChanged: basemapTimer.restart()
                    onWidthChanged: basemapTimer.restart()
                    onHeightChanged: basemapTimer.restart()
                    Timer {
                        id: basemapTimer; interval: 400
                        onTriggered: {
                            if (!root.appState.layerSettings.basemap_visible || !root.appState.rasterUrl) return
                            var left = (viewport.contentX - rasterCanvas.x) / viewport.zoom
                            var top = (viewport.contentY - rasterCanvas.y) / viewport.zoom
                            var x = Math.max(-viewport.paddingX, left)
                            var y = Math.max(-viewport.paddingY, top)
                            var w = Math.max(0, Math.min(root.appState.width+viewport.paddingX, left+viewport.width/viewport.zoom)-x)
                            var h = Math.max(0, Math.min(root.appState.height+viewport.paddingY, top+viewport.height/viewport.zoom)-y)
                            var dpr = Screen.devicePixelRatio || 1
                            backend.requestBasemap(x, y, w, h, Math.ceil(w*viewport.zoom*dpr), Math.ceil(h*viewport.zoom*dpr))
                        }
                    }
                    contentWidth: Math.max(width, (root.appState.width+2*paddingX) * zoom+frameX+20)
                    contentHeight: Math.max(height, (root.appState.height+2*paddingY) * zoom+frameY+20)
                    function zoomAtCenter(factor) {
                        var px=(contentX+width/2-rasterCanvas.x)/zoom
                        var py=(contentY+height/2-rasterCanvas.y)/zoom
                        zoom=Math.max(.1,Math.min(12,zoom*factor))
                        contentX=Math.max(0,Math.min(contentWidth-width,rasterCanvas.x+px*zoom-width/2))
                        contentY=Math.max(0,Math.min(contentHeight-height,rasterCanvas.y+py*zoom-height/2))
                    }
                    function fit() {
                        if (!root.appState.width || !root.appState.height) return
                        fitting=true
                        // Leave room for a single floating action row even in
                        // View, so its appearance cannot cover the top edge.
                        zoom = Math.max(.01, Math.min((usableWidth - 40) / (root.appState.width+2*paddingX), (height - 64) / (root.appState.height+2*paddingY)))
                        frameX=(root.sidebarOpen ? toolsSidebar.width+4 : 0)+Math.max(0,(usableWidth-(root.appState.width+2*paddingX)*zoom)/2)
                        frameY=44+Math.max(0,(height-64-(root.appState.height+2*paddingY)*zoom)/2)
                        contentX = 0; contentY = 0
                        fitMode=true;fitting=false
                    }
                    Connections { target: backend; function onMapLoaded() { viewport.fit() } }
                    Item {
                        id: rasterCanvas; objectName: "rasterCanvas"
                        property bool showingAuto: modeTabs.currentIndex === 0 && root.appState.autoReady && showAutoPreview.checked
                        width: root.appState.width * viewport.zoom; height: root.appState.height * viewport.zoom
                        x: viewport.paddingX*viewport.zoom + viewport.frameX
                        y: viewport.paddingY*viewport.zoom + viewport.frameY
                        visible: root.appState.rasterUrl !== ""
                        Image {
                            objectName: "basemapImage"
                            readonly property var region: root.appState.basemapRect
                            x: (region.x || 0)*viewport.zoom; y: (region.y || 0)*viewport.zoom
                            width: (region.w || 0)*viewport.zoom; height: (region.h || 0)*viewport.zoom
                            source: root.appState.basemapUrl
                            visible: root.appState.layerSettings.basemap_visible
                            cache: false; smooth: true
                        }
                        Image { objectName: "rasterImage"; anchors.fill: parent; source: root.appState.areaDraftRasterUrl || root.appState.rasterUrl; visible: root.originalOnly || rasterVisible.checked || !!root.appState.areaDraftRasterUrl; opacity: root.originalOnly ? 1 : rasterOpacity.value; cache: false; smooth: false }
                        Image { objectName: "polygonOverlay"; anchors.fill: parent; source: parent.showingAuto ? root.appState.autoPreviewUrl : modeTabs.currentIndex === 2 && root.appState.geometryReady ? root.appState.editPreviewUrl : root.appState.overlayUrl; visible: !root.originalOnly && !root.appState.areaDraftRasterUrl && (mapMouse.direct || overlayVisible.checked || (modeTabs.currentIndex === 2 && root.appState.geometryReady)); opacity: mapMouse.direct ? Math.max(.4,opacitySlider.value) : opacitySlider.value; cache: false; smooth: false }
                        Image { anchors.fill: parent; source: modeTabs.currentIndex === 2 && root.appState.geometryReady ? root.appState.editSelectionUrl : root.appState.selectionUrl; visible: !root.originalOnly && !parent.showingAuto && !root.appState.areaDraftRasterUrl; cache: false; smooth: false }
                        Image { anchors.fill: parent; source: root.appState.hintUrl; visible: !root.originalOnly && modeTabs.currentIndex !== 2 && !parent.showingAuto && !root.appState.areaDraftRasterUrl; cache: false; smooth: false }
                        Image {
                            objectName: "reviewHighlight"
                            readonly property var mark: root.appState.reviewMarker
                            x: (mark.x || 0)*viewport.zoom; y: (mark.y || 0)*viewport.zoom
                            width: (mark.w || 0)*viewport.zoom; height: (mark.h || 0)*viewport.zoom
                            source: mark.url || ""; smooth: false
                            visible: !root.originalOnly && reviewPanel.visible && showReviewMark.checked && root.appState.reviewPosition>0 && !root.appState.areaDraftRasterUrl
                        }
                        Image {
                            objectName: "areaOverlay"; anchors.fill: parent
                            source: root.appState.areaDraftUrl || root.appState.areaUrl
                            visible: !root.originalOnly && (!!root.appState.areaDraftUrl || areaVisible.checked)
                            cache: false; smooth: false
                        }
                        Canvas {
                            id: editCanvas; objectName: "editCanvas"
                            // Keep the drawing surface bounded to the viewport at every zoom level.
                            width: viewport.width; height: viewport.height
                            x: viewport.contentX - parent.x; y: viewport.contentY - parent.y
                            visible: !root.originalOnly && modeTabs.currentIndex === 2 && !root.appState.geometryReady && !root.appState.areaDraftRasterUrl
                            onWidthChanged: requestPaint(); onHeightChanged: requestPaint()
                            onXChanged: requestPaint(); onYChanged: requestPaint()
                            Connections { target: backend; function onChanged() { editCanvas.requestPaint() } }
                            onPaint: {
                                var ctx = getContext("2d"); ctx.clearRect(0,0,width,height)
                                if (mapMouse.direct) {
                                    ctx.strokeStyle="#e52c7a"; ctx.lineWidth=3
                                    if (mapMouse.edgeDragging) {
                                        ctx.beginPath()
                                        for (var n=0; n<mapMouse.edgePath.length; n++) {
                                            var node=mapMouse.edgePath[n]
                                            var ex=node.x*viewport.zoom+(mapMouse.dragX-mapMouse.pressX)*node.weight-x
                                            var ey=node.y*viewport.zoom+(mapMouse.dragY-mapMouse.pressY)*node.weight-y
                                            if (n===0) ctx.moveTo(ex,ey)
                                            else ctx.lineTo(ex,ey)
                                        }
                                        ctx.stroke()
                                    } else if (mapMouse.snap.x!==undefined) {
                                        ctx.beginPath(); ctx.arc(mapMouse.snap.x*viewport.zoom-x,mapMouse.snap.y*viewport.zoom-y,5,0,2*Math.PI); ctx.stroke()
                                    }
                                    return
                                }
                                if (mapMouse.lassoDragging && mapMouse.lassoPoints.length) {
                                    var lasso=mapMouse.lassoPoints
                                    ctx.beginPath(); ctx.moveTo(lasso[0][0]*viewport.zoom-x,lasso[0][1]*viewport.zoom-y)
                                    for(var k=1;k<lasso.length;k++)ctx.lineTo(lasso[k][0]*viewport.zoom-x,lasso[k][1]*viewport.zoom-y)
                                    ctx.closePath();ctx.fillStyle="rgba(0,110,210,0.12)";ctx.fill()
                                    ctx.strokeStyle="#006eff";ctx.lineWidth=2;ctx.stroke()
                                }
                                var points = editTool.currentIndex === 4 ? root.appState.vertexPoints : root.appState.editPoints
                                ctx.strokeStyle = editTool.currentIndex === 4 ? "#356ac3" : "#ed2f8c"; ctx.lineWidth = 2
                                if (points.length && editTool.currentIndex !== 4) {
                                    ctx.beginPath(); ctx.moveTo(points[0].x * viewport.zoom-x, points[0].y * viewport.zoom-y)
                                    for (var i=1; i<points.length; i++) ctx.lineTo(points[i].x * viewport.zoom-x, points[i].y * viewport.zoom-y)
                                    if (editTool.currentIndex === 3 && points.length > 2) {
                                        ctx.closePath(); ctx.fillStyle = "#3376cdb7"; ctx.fill()
                                    }
                                    ctx.stroke()
                                }
                                for (var j=0; j<points.length; j++) {
                                    var px = points[j].x * viewport.zoom-x; var py = points[j].y * viewport.zoom-y
                                    if (px < -5 || py < -5 || px > width+5 || py > height+5) continue
                                    ctx.beginPath(); ctx.arc(px,py,4,0,Math.PI*2)
                                    ctx.fillStyle = "white"; ctx.fill(); ctx.stroke()
                                }
                                if (mapMouse.vertexDragging && root.appState.vertexSource.x !== undefined) {
                                    ctx.strokeStyle = "#ed2f8c"; ctx.beginPath()
                                    ctx.moveTo(root.appState.vertexSource.x * viewport.zoom-x, root.appState.vertexSource.y * viewport.zoom-y)
                                    ctx.lineTo(mapMouse.dragX-x,mapMouse.dragY-y); ctx.stroke()
                                    ctx.beginPath(); ctx.arc(mapMouse.dragX-x,mapMouse.dragY-y,6,0,Math.PI*2); ctx.stroke()
                                }
                            }
                        }
                        MouseArea {
                            id: mapMouse; objectName: "mapMouse"
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                            readonly property bool lassoMode: !root.viewing && !root.originalOnly && modeTabs.currentIndex===2 && editTool.currentIndex===0 && lassoToggle.checked && !parent.showingAuto
                            property bool lassoDragging: false
                            property var lassoPoints: []
                            property bool lassoAdditive: false
                            property bool panning: false
                            property var panOrigin: ({})
                            property real panContentX: 0
                            property real panContentY: 0
                            onLassoModeChanged: { if (!lassoMode) { lassoDragging=false;lassoPoints=[];editCanvas.requestPaint() } }
                            property bool vertexDragging: false
                            property real dragX: 0
                            property real dragY: 0
                            property real pressX: 0
                            property real pressY: 0
                            readonly property bool direct: !root.viewing && !root.originalOnly && modeTabs.currentIndex===2 && editTool.currentIndex===5
                            property var edgePath: []
                            readonly property bool edgeDragging: edgePath.length>0
                            property var snap: ({})
                            hoverEnabled: direct
                            function clearEdge() {
                                edgePath=[]; snap={}; backend.cancelEdgeDrag(); editCanvas.requestPaint()
                            }
                            onDirectChanged: { if (!direct) clearEdge() }
                            onExited: { snap={}; editCanvas.requestPaint() }
                            preventStealing: direct || vertexDragging || lassoMode || panning
                            onPressed: function(mouse) {
                                if (mouse.button===Qt.MiddleButton) {
                                    panning=true;panOrigin=mapToItem(viewport,mouse.x,mouse.y)
                                    panContentX=viewport.contentX;panContentY=viewport.contentY;return
                                }
                                if(root.viewing || root.originalOnly){mouse.accepted=false;return}
                                if (lassoMode) {
                                    if (root.appState.busy) return
                                    backend.clearEditLine()
                                    lassoDragging=true;lassoPoints=[[mouse.x/viewport.zoom,mouse.y/viewport.zoom]]
                                    lassoAdditive=!!(mouse.modifiers & (Qt.ControlModifier|Qt.MetaModifier|Qt.ShiftModifier))
                                    editCanvas.requestPaint();return
                                }
                                if (direct) {
                                    pressX=dragX=mouse.x; pressY=dragY=mouse.y
                                    edgePath=backend.beginEdgeDrag(mouse.x/viewport.zoom,mouse.y/viewport.zoom,8/viewport.zoom,singleEdge.checked)
                                    if (!edgeDragging) mouse.accepted=false
                                    editCanvas.requestPaint(); return
                                }
                                if (modeTabs.currentIndex === 2 && editTool.currentIndex === 4) {
                                    pressX = dragX = mouse.x; pressY = dragY = mouse.y
                                    vertexDragging = backend.beginVertexDrag(mouse.x/viewport.zoom,mouse.y/viewport.zoom,9/viewport.zoom)
                                }
                            }
                            onPositionChanged: function(mouse) {
                                if (panning) {
                                    var current=mapToItem(viewport,mouse.x,mouse.y)
                                    viewport.contentX=Math.max(0,Math.min(Math.max(0,viewport.contentWidth-viewport.width),panContentX+panOrigin.x-current.x))
                                    viewport.contentY=Math.max(0,Math.min(Math.max(0,viewport.contentHeight-viewport.height),panContentY+panOrigin.y-current.y));return
                                }
                                if (lassoDragging) {
                                    var points=lassoPoints, last=points[points.length-1]
                                    if (Math.hypot(mouse.x-last[0]*viewport.zoom,mouse.y-last[1]*viewport.zoom)>=3) {
                                        if(points.length>=2048)points=points.filter(function(p,i){return i%2===0})
                                        lassoPoints=points.concat([[mouse.x/viewport.zoom,mouse.y/viewport.zoom]])
                                        editCanvas.requestPaint()
                                    }
                                    return
                                }
                                if (direct && !edgeDragging) {
                                    snap=backend.edgeHover(mouse.x/viewport.zoom,mouse.y/viewport.zoom,8/viewport.zoom)
                                    editCanvas.requestPaint()
                                }
                                if (edgeDragging || vertexDragging) { dragX=mouse.x; dragY=mouse.y; editCanvas.requestPaint() }
                            }
                            onReleased: function(mouse) {
                                if (mouse.button===Qt.MiddleButton) { panning=false;return }
                                if (lassoDragging) {
                                    if(lassoPoints.length>=3)backend.selectLasso(JSON.stringify(lassoPoints.concat([[mouse.x/viewport.zoom,mouse.y/viewport.zoom]])),lassoAdditive)
                                    else backend.selectForEdit(mouse.x/viewport.zoom,mouse.y/viewport.zoom)
                                    lassoDragging=false;lassoPoints=[];editCanvas.requestPaint();return
                                }
                                if (edgeDragging) {
                                    var dx=mouse.x-pressX, dy=mouse.y-pressY
                                    if (Math.hypot(dx,dy)>2) backend.finishEdgeDrag(dx/viewport.zoom,dy/viewport.zoom)
                                    clearEdge(); return
                                }
                                if (vertexDragging) {
                                    if (Math.abs(mouse.x-pressX)+Math.abs(mouse.y-pressY) > 2)
                                        backend.finishVertexDrag(mouse.x/viewport.zoom,mouse.y/viewport.zoom)
                                    else backend.cancelVertexDrag()
                                    vertexDragging=false; editCanvas.requestPaint()
                                }
                            }
                            onCanceled: { panning=false;lassoDragging=false;lassoPoints=[];clearEdge(); vertexDragging=false; backend.cancelVertexDrag(); editCanvas.requestPaint() }
                            cursorShape: panning ? Qt.ClosedHandCursor : root.viewing || root.originalOnly ? Qt.OpenHandCursor : lassoMode ? Qt.CrossCursor : (modeTabs.currentIndex === 1 && hintMode.currentIndex > 0) || (modeTabs.currentIndex === 2 && editTool.currentIndex > 0) ? Qt.CrossCursor : Qt.ArrowCursor
                            onClicked: function(mouse) {
                                if (root.viewing || root.originalOnly || mouse.button!==Qt.LeftButton || parent.showingAuto || direct || lassoMode) return
                                var x = mouse.x / viewport.zoom; var y = mouse.y / viewport.zoom
                                if (modeTabs.currentIndex === 2) {
                                    if (editTool.currentIndex === 0) backend.selectForEdit(x,y)
                                    else if (editTool.currentIndex !== 4) backend.addEditPoint(x,y)
                                }
                                else if (modeTabs.currentIndex === 1 && hintMode.currentIndex > 0)
                                    backend.addHint(x,y,hintMode.currentIndex === 1 ? 1 : 0,roiSize.currentValue)
                                else backend.selectAt(x,y)
                            }
                            onWheel: function(wheel) {
                                if (vertexDragging || edgeDragging || lassoDragging) { wheel.accepted=true; return }
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
                        ActionButton { anchors.horizontalCenter: parent.horizontalCenter; text: "Восстановить рабочую копию"; visible: root.appState.recoveries.length > 0; onClicked: recoveryDialog.open() }
                    }
                    ScrollBar.horizontal: ScrollBar { }
                    ScrollBar.vertical: ScrollBar { }
                }
                Rectangle {
                    Layout.fillWidth: true; height: 24; color: "#f0f2f5"
                    Label { anchors.verticalCenter: parent.verticalCenter; x: 12; width: parent.width - 24; elide: Text.ElideRight; text: root.appState.metadata || "Q — просмотр · V — выбор · колесо — масштаб · F1 — справка"; font.pixelSize: 10; color: "#4c676c" }
                }
            }
            Rectangle {
                id: attributesPanel; objectName: "attributesPanel"; visible: root.attributesShown
                parent: viewport; anchors.right: parent.right; anchors.top: parent.top; z: 120
                width: 292; height: Math.min(viewport.height,attributeControls.implicitHeight+24); color: "#ffffff"; border.color: "#dce0e6"
                MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: function(wheel){wheel.accepted=true} }
                property var scopeData: ({count:0,values:{},mixed:[],epoch:0})
                property int draftToken: 0
                property bool resetting: false
                property bool noteEdited: false
                property string initialNote: ""
                property int dataEpoch: root.appState.attributeEpoch || 0
                property string selectionKey: String(root.appState.selection.object_id || 0)+JSON.stringify(root.appState.editSelected)
                property string scopeSpec: JSON.stringify({mode:["selected","group","filtered"][attributeTarget.currentIndex],group:attributeGroup.currentIndex,
                    query:attributeQuery.text,review:["all","unreviewed","accepted","edited"][attributeReview.currentIndex],missing:attributeMissing.checked})
                function refreshScope() {
                    if(!noteField)return
                    resetting=true
                    scopeData=backend.attributeScope(scopeSpec)
                    noteField.text=scopeData.values.note || ""
                    initialNote=noteField.text;noteEdited=false;draftToken++
                    resetting=false
                }
                function patch() {
                    var result={}
                    if(noteEdited || noteField.text!==initialNote)result.note=noteField.text
                    for(var i=0;i<attributeRepeater.count;i++) {
                        var editor=attributeRepeater.itemAt(i)
                        if(editor.isChanged())result[editor.field.key]=editor.readValue()
                    }
                    return JSON.stringify(result)
                }
                onScopeSpecChanged: refreshScope()
                onDataEpochChanged: refreshScope()
                onSelectionKeyChanged: if(attributeTarget.currentIndex===0)refreshScope()
                Component.onCompleted: refreshScope()
                ColumnLayout {
                    id: attributeControls
                    anchors.fill: parent; anchors.margins: 12; spacing: 8
                    RowLayout { Layout.fillWidth: true
                        Label { text: "Атрибуты"; font.bold: true; Layout.fillWidth: true }
                        ActionButton { text: "Поля…"; objectName: "manageFieldsButton"; enabled: !!root.appState.rasterUrl && !root.appState.busy; onClicked: schemaManager.open() }
                        ActionButton { text: "×"; description: "Закрыть атрибуты"; objectName: "closeAttributes"; onClicked: root.attributesPinned=false }
                    }
                    ComboBox { implicitHeight: 34; id: attributeTarget; objectName: "attributeTarget"; Layout.fillWidth: true; model: ["Выделенные объекты","Группа","По фильтру"] }
                    ComboBox { implicitHeight: 34; id: attributeGroup; objectName: "attributeGroup"; Layout.fillWidth: true
                        visible: attributeTarget.currentIndex>0; model: [attributeTarget.currentIndex===2 ? "Все группы" : "Выберите группу"].concat(root.groupNames) }
                    TextField { implicitHeight: 34; id: attributeQuery; objectName: "attributeQuery"; visible: attributeTarget.currentIndex===2; Layout.fillWidth: true; placeholderText: "Поиск по атрибутам" }
                    ComboBox { implicitHeight: 34; id: attributeReview; objectName: "attributeReview"; visible: attributeTarget.currentIndex===2; Layout.fillWidth: true; model:["Все состояния","Не проверен","Принят","Изменён вручную"] }
                    CheckBox { implicitHeight: 30; id: attributeMissing; visible: attributeTarget.currentIndex===2; text: "Без обязательных значений" }
                    Label { text: "Объектов: "+attributesPanel.scopeData.count; font.bold: true }
                    CheckBox { implicitHeight: 30; id: scopeOnlyEmpty; objectName: "scopeOnlyEmpty"; text: "Только незаполненные"; checked: true; visible: attributesPanel.scopeData.count>1 }
                    ScrollView { id: attributeScroll; Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredHeight: Math.min(attributeForm.implicitHeight,Math.max(90,viewport.height-330)); contentWidth: availableWidth; clip: true
                        ColumnLayout { id: attributeForm; width: attributeScroll.availableWidth; spacing: 8
                            Label { text: "Примечание" }
                            ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 76
                                TextArea { id: noteField; objectName: "noteField"; enabled: attributesPanel.scopeData.count>0 && !root.appState.busy
                                    placeholderText: attributesPanel.scopeData.mixed.indexOf("note")>=0 ? "Разные значения" : "Не задано"; wrapMode: Text.WordWrap
                                    onTextChanged: if(activeFocus && !attributesPanel.resetting)attributesPanel.noteEdited=true }
                            }
                            Repeater { id: attributeRepeater; model: root.customFieldsModel
                                delegate: AttributeInput { required property var modelData; Layout.fillWidth: true; field: modelData
                                    currentValue: attributesPanel.scopeData.values[modelData.key]
                                    mixed: attributesPanel.scopeData.mixed.indexOf(modelData.key)>=0; resetToken: attributesPanel.draftToken
                                    active: attributesPanel.scopeData.count>0 && !root.appState.busy }
                            }
                        }
                    }
                    ActionButton { id: applyAttributesButton; text: "Применить к "+attributesPanel.scopeData.count+" объектам"; shortcutLabel: root.commandKey+"+Enter"; objectName: "applyAttributesButton"; Layout.fillWidth: true; highlighted: true
                        enabled: attributesPanel.scopeData.count>0 && !root.appState.busy
                        onClicked: backend.applyAttributeScope(attributesPanel.scopeSpec,attributesPanel.patch(),scopeOnlyEmpty.checked && attributesPanel.scopeData.count>1,attributesPanel.scopeData.epoch) }
                    Label { text: "Изменятся только отредактированные поля. Отмена — Ctrl/Cmd+Z."; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#687383"; font.pixelSize: 11 }
                }
            }

        }
        Loader {
            id: webMapHost; objectName: "webMapHost"
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: workspaceTabs.currentIndex===1
            active: root.webMapOpened && root.appState.webMapAvailable
            source: active ? "WebMap.qml" : ""
            onLoaded: item.shortcutsAllowed=Qt.binding(function(){return !root.autosavePaused && !polygonDialog.visible && !root.appState.busy && !backend.isTextInput(root.activeFocusItem)})
        }
        Label {
            visible: workspaceTabs.currentIndex===1 && !root.appState.webMapAvailable
            Layout.fillWidth: true; Layout.fillHeight: true; padding: 36; wrapMode: Text.WordWrap
            text: "Для вкладки Яндекс Карт требуется обновление компонентов приложения. Оцифровка и подложки доступны в первой вкладке."
        }
        RowLayout { visible: root.appState.qualityBusy; Layout.fillWidth: true
            Label { text: root.appState.qualityStage+" · "+root.appState.qualityETA; Layout.fillWidth: true; elide: Text.ElideRight }
            ActionButton { text: "Остановить"; enabled: !root.appState.qualityCancelPending; onClicked: backend.cancelQuality() }
        }
        ProgressBar { Layout.fillWidth: true; Layout.preferredHeight: root.appState.busy ? 3 : 0; visible: root.appState.busy; indeterminate: true }
        Rectangle {
            Layout.fillWidth: true; height: 26; color: root.appState.error ? "#fbe4de" : "#ffffff"
            Label { anchors.left: parent.left; anchors.right: autosaveLabel.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 12; verticalAlignment: Text.AlignVCenter; text: (root.appState.dirty ? "●  " : "") + root.appState.status; elide: Text.ElideMiddle; color: root.appState.error ? "#a43725" : "#566273"; font.pixelSize: 12 }
            Label { id: autosaveLabel; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                text: root.appState.autosaveStatus; color: "#687383"; font.pixelSize: 11 }
        }
    }
}
