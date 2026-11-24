import QtQuick

Rectangle {
    id: view
    objectName: "paletteViewport"
    required property int columns
    required property int rows
    required property url rasterSource
    required property url paletteSource
    required property url evidenceSource
    required property bool showEvidence
    required property bool picking
    signal sampled(int column, int row)
    property bool framing: false
    signal frameSelected(real x0, real y0, real x1, real y1)
    property point frameEnd

    color: "#e8ebef"
    radius: 3
    clip: true
    property real zoom: 1
    property real centerX: 0.5
    property real centerY: 0.5
    readonly property real fitScale: Math.max(0.001, Math.min((width-16)/Math.max(1,columns),
                                                            (height-16)/Math.max(1,rows)))
    readonly property real pixelScale: zoom*fitScale

    function origin(extent, frame, center) {
        if (extent <= frame) return (frame-extent)/2
        return Math.max(frame-extent, Math.min(0, frame/2-center*extent))
    }
    function setCenterAt(px, py, relX, relY) {
        // Clamp the stored center as well, so panning reverses immediately at an edge.
        var left = origin(mapLayer.width, width, relX+(width/2-px)/mapLayer.width)
        var top = origin(mapLayer.height, height, relY+(height/2-py)/mapLayer.height)
        centerX = (width/2-left)/mapLayer.width
        centerY = (height/2-top)/mapLayer.height
    }
    function zoomAt(factor, px, py) {
        if (mapLayer.width <= 0 || mapLayer.height <= 0) return
        var relX = (px-mapLayer.x)/mapLayer.width, relY = (py-mapLayer.y)/mapLayer.height
        zoom = Math.max(1, Math.min(32, zoom*factor))
        setCenterAt(px,py,relX,relY)
    }
    function fitMap() { zoom = 1; centerX = 0.5; centerY = 0.5 }

    Item {
        id: mapLayer; objectName: "paletteMapLayer"
        width: view.columns*view.pixelScale; height: view.rows*view.pixelScale
        x: view.origin(width, view.width, view.centerX)
        y: view.origin(height, view.height, view.centerY)
        Image { anchors.fill: parent; source: view.rasterSource; smooth: false; cache: false }
        Image { anchors.fill: parent; source: view.paletteSource; smooth: false; cache: false }
        Image { anchors.fill: parent; source: view.evidenceSource; visible: view.showEvidence; smooth: false; cache: false }
    }
    Rectangle {
        visible:view.framing && pointer.pressed
        x:Math.min(pointer.start.x,view.frameEnd.x);y:Math.min(pointer.start.y,view.frameEnd.y)
        width:Math.abs(pointer.start.x-view.frameEnd.x);height:Math.abs(pointer.start.y-view.frameEnd.y)
        color:"#22356ac3";border.color:"#356ac3";border.width:2
    }
    MouseArea {
        id: pointer; objectName: "sampleMap"; anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        preventStealing: true
        property point start
        property point mapAnchor
        property bool moved: false
        cursorShape: pressed && moved ? Qt.ClosedHandCursor : view.picking ? Qt.CrossCursor : Qt.OpenHandCursor
        onPressed: function(mouse) {
            start = Qt.point(mouse.x,mouse.y)
            mapAnchor = Qt.point((mouse.x-mapLayer.x)/mapLayer.width,(mouse.y-mapLayer.y)/mapLayer.height)
            moved = false
            view.frameEnd=start
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            if (Math.hypot(mouse.x-start.x,mouse.y-start.y) >= Qt.styleHints.startDragDistance) moved = true
            if(view.framing && pressedButtons===Qt.LeftButton){view.frameEnd=Qt.point(mouse.x,mouse.y);return}
            if (moved) view.setCenterAt(mouse.x,mouse.y,mapAnchor.x,mapAnchor.y)
        }
        onReleased: function(mouse) {
            if(view.framing && mouse.button===Qt.LeftButton){
                var x0=Math.max(0,Math.min(view.columns,(start.x-mapLayer.x)/view.pixelScale)),y0=Math.max(0,Math.min(view.rows,(start.y-mapLayer.y)/view.pixelScale))
                var x1=Math.max(0,Math.min(view.columns,(mouse.x-mapLayer.x)/view.pixelScale)),y1=Math.max(0,Math.min(view.rows,(mouse.y-mapLayer.y)/view.pixelScale))
                if(Math.abs(x1-x0)>=4&&Math.abs(y1-y0)>=4)view.frameSelected(Math.min(x0,x1),Math.min(y0,y1),Math.max(x0,x1),Math.max(y0,y1))
                return
            }
            if (moved || mouse.button !== Qt.LeftButton || !view.picking) return
            var x = Math.floor((mouse.x-mapLayer.x)/view.pixelScale)
            var y = Math.floor((mouse.y-mapLayer.y)/view.pixelScale)
            if (x>=0 && y>=0 && x<view.columns && y<view.rows) view.sampled(x,y)
        }
        onWheel: function(wheel) {
            var delta = wheel.pixelDelta
            if ((delta.x || delta.y) && !(wheel.modifiers & Qt.ControlModifier)) {
                view.setCenterAt(view.width/2+delta.x,view.height/2+delta.y,
                                 (view.width/2-mapLayer.x)/mapLayer.width,(view.height/2-mapLayer.y)/mapLayer.height)
            } else {
                var step = delta.y ? delta.y/80 : wheel.angleDelta.y/120
                if (step) view.zoomAt(Math.pow(1.25,step),wheel.x,wheel.y)
            }
            wheel.accepted = true
        }
    }
}
