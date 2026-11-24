import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine

Rectangle {
    id: page; color: "#e8ebef"
    property bool layersOpen: false
    property bool shortcutsAllowed: false
    readonly property bool keysEnabled: visible && shortcutsAllowed
    property var appState: backend.state
    property int projectKey: appState.webMapRevision || 0
    property string pageUrl: appState.webMapUrl
    // WebEngine reloads even when setUrl receives the current URL. Keep that
    // write separate from the frequently changing application-state object.
    onPageUrlChanged: { if (browser.url.toString()!==pageUrl) browser.url=pageUrl }
    Component.onCompleted: { if (pageUrl) browser.url=pageUrl }
    component MapButton: Button {
        id: control
        hoverEnabled: true
        property string description: text
        property string shortcutLabel: ""
        implicitHeight: 30; padding: 5; leftPadding: 9; rightPadding: 9
        ToolTip.visible: hovered; ToolTip.delay: 550; ToolTip.text: description+(shortcutLabel ? " · "+shortcutLabel : "")
        background: Rectangle { radius: 3; color: control.highlighted ? "#e1eafa" : control.down ? "#d8e3f5" : control.hovered ? "#e3eaf6" : "#f0f2f5" }
        contentItem: Label { text: control.text; font.pixelSize: 13; color: control.enabled ? "#303944" : "#939ba6"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    }
    component LayerCheckBox: CheckBox {
        id: control; implicitHeight: 30; spacing: 8
        indicator: Rectangle {
            x: 0; y: (control.height-height)/2; width: 16; height: 16; radius: 2
            color: control.checked ? "#356ac3" : "#ffffff"; border.color: control.checked ? "#356ac3" : "#9ca7b6"
            Label { anchors.centerIn: parent; text: "✓"; color: "white"; font.pixelSize: 13; visible: control.checked }
        }
        contentItem: Label { text: control.text; leftPadding: 24; color: "#303944"; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
    }
    component LayerSlider: Slider {
        id: control; implicitHeight: 24; from: 0; to: 1
        background: Rectangle {
            x: control.leftPadding; y: control.topPadding+(control.availableHeight-height)/2
            width: control.availableWidth; height: 4; radius: 2; color: "#e0e5ec"
            Rectangle { width: control.visualPosition*parent.width; height: parent.height; radius: 2; color: "#356ac3" }
        }
        handle: Rectangle {
            x: control.leftPadding+control.visualPosition*(control.availableWidth-width)
            y: control.topPadding+(control.availableHeight-height)/2
            width: 12; height: 12; radius: 6; color: "#356ac3"; opacity: control.enabled ? 1 : .4
        }
    }
    function updateProject(fit) {
        backend.refreshWebMapContext()
        browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.updateProject("+(fit===true)+")")
    }
    onProjectKeyChanged: updateProject(false)
    Shortcut { autoRepeat: false; sequences: ["G", "П"]; enabled: page.keysEnabled; onActivated: page.layersOpen=!page.layersOpen }
    Shortcut { autoRepeat: false; sequences: ["F", "А"]; enabled: page.keysEnabled && !!page.appState.rasterUrl; onActivated: page.updateProject(true) }
    Shortcut { autoRepeat: false; sequences: ["F5"]; enabled: page.keysEnabled; onActivated: browser.reload() }
    Connections { target: backend; function onWebMapConfigurationChanged() { browser.reload() } }
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 40; color: "#f4f5f7"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                MapButton { objectName: "webMapLayersButton"; text: "Слои"; highlighted: page.layersOpen; shortcutLabel: "G"
                    description: "Показать / скрыть слои карты"; onClicked: page.layersOpen=!page.layersOpen }
                ComboBox {
                    id: mapType; objectName: "webMapType"; implicitHeight: 30; Layout.preferredWidth: 140; hoverEnabled: true
                    model: ["Схема", "Спутник", "Гибрид"]
                    contentItem: Label { text: mapType.displayText; leftPadding: 10; rightPadding: 24; color: "#303944"; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { color: "#ffffff"; border.color: "#cbd2dc"; radius: 3 }
                    indicator: Label { text: "▾"; x: mapType.width-width-10; y: (mapType.height-height)/2; color: "#566273" }
                    ToolTip.visible: hovered; ToolTip.delay: 550; ToolTip.text: "Выбрать подложку"
                    onActivated: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.setType('"+["map","satellite","hybrid"][currentIndex]+"')")
                }
                MapButton { objectName: "webMapFit"; text: "К карте"; description: "Показать область проекта"; shortcutLabel: "F"; enabled: !!page.appState.rasterUrl; onClicked: page.updateProject(true) }
                MapButton { objectName: "webMapReload"; text: "Обновить"; description: "Перезагрузить Яндекс Карты"; shortcutLabel: "F5"; onClicked: browser.reload() }
                Item { Layout.fillWidth: true }
                Label { text: "Обзор проекта"; color: "#687383"; font.pixelSize: 12 }
            }
        }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
        WebEngineView {
            id: browser; objectName: "webMapBrowser"
            anchors.fill: parent
            profile: WebEngineProfile {
                storageName: "GeoTraceYandexOverview"
                offTheRecord: false
                httpCacheType: WebEngineProfile.DiskHttpCache
                httpCacheMaximumSize: 64*1024*1024
            }
            onTitleChanged: {
                var prefix="GeoTrace Web Map · "
                if (title.indexOf(prefix)===0) {
                    var code=title.slice(prefix.length)
                    backend.reportWebMap(code)
                    if(code==="ready") {
                        browser.runJavaScript("GeoTraceMap.setType('"+["map","satellite","hybrid"][mapType.currentIndex]+"');GeoTraceMap.showOutlines("+outlineToggle.checked+");GeoTraceMap.showRaster("+rasterToggle.checked+");GeoTraceMap.showPolygons("+polygonToggle.checked+");GeoTraceMap.showLines("+lineToggle.checked+");GeoTraceMap.setRasterOpacity("+rasterAlpha.value+");GeoTraceMap.setPolygonOpacity("+polygonAlpha.value+")")
                    }
                }
            }
            onVisibleChanged: {
                if (visible) lifecycleState=WebEngineView.LifecycleState.Active
                else if (!loading) lifecycleState=WebEngineView.LifecycleState.Frozen
            }
            onJavaScriptConsoleMessage: function(level, message, line, source) {
                // Third-party API diagnostics can contain URLs with credentials.
                // The page reports a fixed, localised status instead.
            }
            onNavigationRequested: function(request) {
                var destination=request.url.toString()
                var own=page.appState.webMapUrl.replace("index.html","")
                if (request.isMainFrame && destination.indexOf(own)!==0 && destination!=="about:blank") {
                    request.action=WebEngineNavigationRequest.IgnoreRequest
                    if (/^https?:\/\//.test(destination)) Qt.openUrlExternally(request.url)
                }
            }
            onNewWindowRequested: function(request) {
                if (/^https?:\/\//.test(request.requestedUrl.toString())) Qt.openUrlExternally(request.requestedUrl)
            }
        }
            Rectangle {
                id: layerPanel; objectName: "webMapLayersPanel"; visible: page.layersOpen
                anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 8
                width: 266; height: layerControls.implicitHeight+24; color: "#ffffff"; border.color: "#dce0e6"; radius: 3
                MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: function(wheel){wheel.accepted=true} }
                ColumnLayout {
                    id: layerControls; anchors.fill: parent; anchors.margins: 12; spacing: 6
                    RowLayout {
                        Label { text: "Слои проекта"; font.bold: true; Layout.fillWidth: true }
                        MapButton { text: "×"; description: "Закрыть слои"; onClicked: page.layersOpen=false }
                    }
                    LayerCheckBox { id: rasterToggle; objectName: "webMapRaster"; text: "Исходная карта"; checked: true
                        onClicked: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.showRaster("+checked+")") }
                    RowLayout {
                        LayerSlider { id: rasterAlpha; objectName: "webMapRasterOpacity"; Layout.fillWidth: true; value: 0.8; Accessible.name: "Непрозрачность исходной карты"
                            enabled: rasterToggle.checked
                            onMoved: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.setRasterOpacity("+value+")") }
                        Label { text: Math.round(rasterAlpha.value*100)+"%"; Layout.preferredWidth: 34; horizontalAlignment: Text.AlignRight; color: "#687383" }
                    }
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: "#e8ebef" }
                    LayerCheckBox { id: polygonToggle; objectName: "webMapPolygons"; text: "Полигоны"; checked: true
                        onClicked: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.showPolygons("+checked+")") }
                    RowLayout {
                        LayerSlider { id: polygonAlpha; objectName: "webMapPolygonOpacity"; Layout.fillWidth: true; value: 0.25; Accessible.name: "Непрозрачность заливки полигонов"
                            enabled: polygonToggle.checked
                            onMoved: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.setPolygonOpacity("+value+")") }
                        Label { text: Math.round(polygonAlpha.value*100)+"%"; Layout.preferredWidth: 34; horizontalAlignment: Text.AlignRight; color: "#687383" }
                    }
                    LayerCheckBox {id:lineToggle;objectName:"webMapLines";text:"Линии и разломы";checked:true;visible:page.appState.workspaceEnabled===true
                        onClicked:browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.showLines("+checked+")")}
                    LayerCheckBox { id: outlineToggle; objectName: "webMapOutlines"; text: "Контур карты и KML"; checked: true
                        onClicked: browser.runJavaScript("window.GeoTraceMap && GeoTraceMap.showOutlines("+checked+")") }
                    Label { Layout.fillWidth: true; text: "Редактирование — во вкладке «Карта»."; color: "#687383"; font.pixelSize: 11; wrapMode: Text.WordWrap }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 26; color: "#f0f2f5"
            Label { anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; verticalAlignment: Text.AlignVCenter
                text: page.appState.webMapStatus+" · Применённые полигоны обновляются автоматически"; elide: Text.ElideRight; color: "#687383"; font.pixelSize: 11 }
        }
    }
}
