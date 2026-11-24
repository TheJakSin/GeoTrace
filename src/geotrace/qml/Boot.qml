import QtQuick
import QtQuick.Window

Window {
    id:boot
    width:440;height:265;visible:true;color:"transparent"
    flags:Qt.SplashScreen|Qt.FramelessWindowHint
    title:"GeoTrace"
    property string phase:"Подготовка редактора…"
    property string version:"1.0 · разработка"
    Rectangle {anchors.fill:parent;color:"#ffffff";radius:8;border.color:"#dce0e6"
        Column {anchors.centerIn:parent;spacing:12
            Image {anchors.horizontalCenter:parent.horizontalCenter;width:66;height:66;source:"icons/geotrace.svg";sourceSize.width:132;sourceSize.height:132
                opacity:0;scale:.96
                ParallelAnimation on opacity {running:true;NumberAnimation {from:0;to:1;duration:180}}
                NumberAnimation on scale {from:.96;to:1;duration:220;running:true;easing.type:Easing.OutCubic}
            }
            Text {anchors.horizontalCenter:parent.horizontalCenter;text:"GeoTrace";font.pixelSize:31;font.bold:true;color:"#252f3d"}
            Text {anchors.horizontalCenter:parent.horizontalCenter;text:boot.version;font.pixelSize:11;color:"#7b8593"}
        }
        Text {anchors.horizontalCenter:parent.horizontalCenter;anchors.bottom:parent.bottom;anchors.bottomMargin:35;text:boot.phase;font.pixelSize:11;color:"#7b8593"}
        Text {anchors.horizontalCenter:parent.horizontalCenter;anchors.bottom:parent.bottom;anchors.bottomMargin:15;text:"TJS TECH  ×  TJS HORIZON";font.pixelSize:9;font.letterSpacing:1.1;color:"#89929e"}
    }
}
