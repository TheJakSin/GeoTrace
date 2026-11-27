import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

Item {
    id: panels
    required property var controller
    required property var appRoot
    property var state: controller.state
    property var mappingDraft: []
    property int editingType: 0
    signal lineColourRequested(int identity)
    signal lineStyleColourRequested(int identity,bool sourceOnly)
    signal mapFrameRequested()
    property var actions: [
        {name:"Добавить листы карты", panel:"add"}, {name:"Добавить участок KML", panel:"kml"},
        {name:"Листы проекта", panel:"sheets"}, {name:"Сопоставить группы разных листов", panel:"groups"},
        {name:"Группы полигонов", panel:"classes"}, {name:"Сглаживание границ",panel:"smooth"}, {name:"Проверка обновлений",panel:"updates"}, {name:"Линии и разломы", panel:"lines"},
        {name:"Проверить стыки и покрытие", panel:"seams"}, {name:"Сводка участка", panel:"summary"},
        {name:"Макет и экспорт карты", panel:"layout"}, {name:"Контрольные сохранения", panel:"checkpoints"},
        {name:"О программе", panel:"about"}
    ]
    function openPanel(name) {
        if (name==="add") addDialog.open()
        else if(name==="kml") kmlDialog.open()
        else if(name==="sheets") sheetsDialog.open()
        else if(name==="groups") groupsDialog.open()
        else if(name==="classes") classManager.open()
        else if(name==="newGroup") {classManager.open();classManager.newGroup()}
        else if(name==="smooth") smoothPanel.open()
        else if(name==="updates") updateDialog.open()
        else if(name==="lines") {controller.endGroupMatching();linesDialog.open()}
        else if(name==="lineFields") lineFieldsDialog.open()
        else if(name==="lineAttributes") {if(!panels.state.lineMode)controller.setLineMode(true);appRoot.attributesPinned=true}
        else if(name==="summary") summaryDialog.open()
        else if(name==="about") aboutDialog.open()
        else if(name==="search") commandDialog.open()
        else if(name==="seams") controller.checkSeams()
        else if(name==="checkpoints") {controller.refreshCheckpoints();checkpointDialog.open()}
        else if(name==="layout") {layoutWindow.draft=controller.defaultLayout();layoutWindow.show()}
    }
    function editGroup(identity){if(identity>0){stylesDialog.identity=identity;stylesDialog.open()}}
    readonly property bool modalOpen: sheetsDialog.visible || stylesDialog.visible ||
        lineFieldDialog.visible || lineFieldsDialog.visible || summaryDialog.visible || aboutDialog.visible || commandDialog.visible ||
        updateDialog.visible || checkpointDialog.visible || addDialog.visible || kmlDialog.visible || portableDialog.visible
    onModalOpenChanged: controller.pauseAutosave(modalOpen || appRoot.autosavePaused)
    component Input: TextField {
        id:field;implicitHeight:32;leftPadding:9;rightPadding:9;font.pixelSize:13;selectByMouse:true
        color:enabled?"#263445":"#87919e";placeholderTextColor:"#697586";selectionColor:"#356ac3";selectedTextColor:"#ffffff"
        background:Rectangle {color:"white";radius:3;border.color:field.activeFocus?"#356ac3":"#d3d9e2"}
    }
    component Choice: ComboBox {
        id:choice;implicitHeight:32;leftPadding:9;rightPadding:25;font.pixelSize:13
        contentItem:Label {text:choice.displayText;verticalAlignment:Text.AlignVCenter;elide:Text.ElideRight;color:choice.enabled?"#303944":"#939ba6"}
        indicator:Label {x:choice.width-20;y:(choice.height-height)/2;text:"▾";color:"#687383"}
        background:Rectangle {color:choice.enabled?"white":"#f0f2f5";radius:3;border.color:choice.activeFocus?"#356ac3":"#d3d9e2"}
    }
    component Counter: SpinBox {
        id:counter;property int factor:1
        palette.text:"#263445";palette.windowText:"#263445";palette.base:"white";palette.button:"#f0f2f5";palette.buttonText:"#303944";implicitHeight:34;font.pixelSize:13
        validator:DoubleValidator {bottom:counter.from/counter.factor;top:counter.to/counter.factor;decimals:counter.factor===1?0:1;notation:DoubleValidator.StandardNotation;locale:counter.locale.name}
        textFromValue:function(value,locale){return Number(value/counter.factor).toLocaleString(locale,"f",counter.factor===1?0:1)}
        valueFromText:function(text,locale){return Math.round(Number.fromLocaleString(locale,text)*counter.factor)}
    }
    component Action: Button {
        id: button
        onPressed:appRoot.noteCompareGesture()
        implicitHeight: 31; leftPadding: 10; rightPadding: 10; topInset: 0; bottomInset: 0
        hoverEnabled: true
        background: Rectangle {radius:3;color:!button.enabled?"#eef0f3":button.highlighted?"#356ac3":button.hovered?"#e2e8f1":"#f0f2f5"}
        contentItem: Label {text:button.text;font:button.font;color:!button.enabled?"#939ba6":button.highlighted?"white":"#303944";horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter;elide:Text.ElideRight}
    }
    component SurfaceDialog: Dialog {
        id:surface
        modal:true; focus:true; anchors.centerIn:parent
        palette:appRoot.palette
        width:Math.min(900,appRoot.width-60); height:Math.min(650,appRoot.height-70)
        closePolicy:Popup.CloseOnEscape
        background:Rectangle {color:"white";border.color:"#dce0e6";radius:4}
        standardButtons:Dialog.NoButton
        header:Label {text:surface.title;padding:16;bottomPadding:12;font.pixelSize:17;font.bold:true;color:"#252f3d";elide:Text.ElideRight}
        footer:Item {implicitHeight:50;Action {anchors.right:parent.right;anchors.rightMargin:14;anchors.verticalCenter:parent.verticalCenter;text:"Закрыть";onClicked:surface.close()}}
    }
    FileDialog {id:addDialog;title:"Добавить листы карты";fileMode:FileDialog.OpenFiles;nameFilters:["Геопривязанные карты (*.tif *.tiff)"];onAccepted:controller.addSheets(selectedFiles)}
    FileDialog {id:kmlDialog;title:"Добавить лицензионный участок";nameFilters:["Контуры KML (*.kml)"];onAccepted:controller.loadArea(selectedFile)}
    FileDialog {id:portableDialog;title:"Сохранить переносимый проект с исходниками";fileMode:FileDialog.SaveFile;nameFilters:["Проект GeoTrace (*.geotrace)"];defaultSuffix:"geotrace";onAccepted:controller.savePortable(selectedFile)}
    function savePortable() {portableDialog.open()}
    function closeWindows(){layoutWindow.close()}
    ColorDialog {id:styleColorPicker;property var targetField:null;title:"Цвет оформления";onAccepted:{if(targetField)targetField.text=selectedColor.toString()}}
    function chooseColor(field){styleColorPicker.targetField=field;styleColorPicker.selectedColor=field.text;styleColorPicker.open()}

    SurfaceDialog {
        id:sheetsDialog;objectName:"workspaceSheetsDialog";title:"Листы проекта";height:Math.min(appRoot.height-70,265+Math.min(5,(panels.state.sheets||[]).length)*81)
        contentItem:ColumnLayout {
            spacing:12
            RowLayout {
                Action {text:"Добавить листы…";enabled:!panels.state.busy;onClicked:addDialog.open()}
                Action {text:"Участок KML…";enabled:!panels.state.busy;onClicked:kmlDialog.open()}
                Item {Layout.fillWidth:true}
                Label {text:(panels.state.sheets||[]).length+" листов · EPSG:3857";color:"#687383"}
            }
            Label {text:"Каждый лист распознаётся целиком. KML ограничивает общий результат участка. В перекрытии приоритет у последнего листа.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383"}
            CheckBox {objectName:"smoothScanSeams";text:"Сглаживать узкие стыки сканов";checked:panels.state.smoothScanSeams!==false;enabled:!panels.state.busy&&(panels.state.sheets||[]).length>1;onClicked:controller.setSmoothScanSeams(checked)
                ToolTip.visible:hovered;ToolTip.delay:650;ToolTip.text:"Только отображение крайнего ряда пикселей. Координаты и полигоны не меняются."}
            ListView {
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:5;model:panels.state.sheets||[]
                ScrollBar.vertical:ScrollBar {}
                delegate:Rectangle {
                    required property var modelData
                    width:ListView.view.width;height:76;color:modelData.id===panels.state.activeSheet?"#edf3fc":"#f7f8fa";radius:3
                    RowLayout {anchors.fill:parent;anchors.margins:9;spacing:10
                        CheckBox {checked:modelData.visible;enabled:!panels.state.busy;onClicked:controller.setSheetVisible(modelData.id,checked);ToolTip.visible:hovered;ToolTip.text:"Видимость исходного листа"}
                        ColumnLayout {Layout.fillWidth:true;spacing:3
                            Label {text:modelData.name;font.bold:true;Layout.fillWidth:true;elide:Text.ElideRight}
                            Label {text:modelData.processed?modelData.polygons+" полигонов · полный лист":"Оцифровка ещё не выполнена";color:"#687383";font.pixelSize:11}
                        }
                        Action {text:modelData.id===panels.state.activeSheet?"Активный":"Выбрать";enabled:!panels.state.busy;onClicked:controller.activateSheet(modelData.id)}
                        Action {text:"↑";enabled:!panels.state.busy;onClicked:controller.moveSheet(modelData.id,1)}
                        Action {text:"↓";enabled:!panels.state.busy;onClicked:controller.moveSheet(modelData.id,-1)}
                        Action {text:"Убрать";enabled:!panels.state.busy&&(panels.state.sheets||[]).length>1;onClicked:controller.removeSheet(modelData.id)}
                    }
                }
            }
            RowLayout {
                Action {text:"Подготовить активный лист";highlighted:true;enabled:!!panels.state.activeSheet&&!panels.state.busy;onClicked:{sheetsDialog.close();controller.prepareQuality()}}
                Action {text:"Оцифровать все листы";enabled:!!panels.state.activeSheet&&!panels.state.busy;onClicked:{sheetsDialog.close();controller.digitiseAll()}}
                Action {text:"Сопоставить группы…";enabled:(panels.state.classMappings||[]).length>0&&!panels.state.busy;onClicked:{sheetsDialog.close();groupsDialog.open()}}
            }
        }
    }
    component MatchCard: Rectangle {
        id:card
        required property string side
        readonly property var choice:side==="A"?panels.state.matchingA||({}):panels.state.matchingB||({})
        Layout.fillWidth:true;implicitHeight:112;radius:4;color:"#f5f7fa"
        border.width:panels.state.matchingSide===side?2:1;border.color:panels.state.matchingSide===side?(side==="A"?"#356ac3":"#d78119"):"#dce0e6"
        ColumnLayout {anchors.fill:parent;anchors.margins:9;spacing:5
            RowLayout {Layout.fillWidth:true
                Label {text:side;font.bold:true;color:side==="A"?"#356ac3":"#bd6e10";font.pixelSize:16}
                Choice {Layout.fillWidth:true;model:(panels.state.sheets||[]).filter(function(s){return s.processed});textRole:"name";valueRole:"id";currentIndex:model.findIndex(function(s){return s.id===card.choice.sheet})
                    onActivated:controller.chooseMatchingGroup(side,currentValue,0)}
                Action {text:"На карте";highlighted:panels.state.matchingSide===side;enabled:!panels.state.busy;onClicked:controller.setMatchingSide(side)}
            }
            RowLayout {Layout.fillWidth:true
                Rectangle {width:24;height:24;radius:3;color:card.choice.color||"#eef1f6";border.color:"#aeb8c5"}
                Choice {Layout.fillWidth:true;model:(panels.state.classMappings||[]).filter(function(g){return g.sheet===card.choice.sheet&&!g.matched});textRole:"group"
                    displayText:card.choice.group?"Группа "+card.choice.group:"Выберите заливку…";currentIndex:model.findIndex(function(g){return g.group===card.choice.group})
                    delegate:ItemDelegate {required property var modelData;width:parent.width
                        contentItem:RowLayout {Rectangle {width:18;height:18;color:modelData.color;radius:2} Label {text:"Группа "+modelData.group;Layout.fillWidth:true}}}
                    onActivated:controller.chooseMatchingGroup(side,card.choice.sheet,model[currentIndex].group)}
            }
            Label {text:card.choice.className||"Щёлкните нужную заливку на карте";Layout.fillWidth:true;elide:Text.ElideRight;color:"#566273";font.pixelSize:11}
        }
    }
    Rectangle {
        id:groupsDialog;objectName:"workspaceGroupsDialog";parent:appRoot.mapViewport
        anchors.bottom:parent.bottom;anchors.horizontalCenter:parent.horizontalCenter;anchors.bottomMargin:10;z:135
        width:Math.min(850,parent.width-30);height:matchingBody.visible?matchingContent.implicitHeight+20:52
        color:"white";border.color:"#cbd4e1";radius:5;visible:panels.state.groupMatching===true
        function open(){appRoot.chooseView();controller.beginGroupMatching()}
        function close(){controller.endGroupMatching()}
        MouseArea {anchors.fill:parent;acceptedButtons:Qt.AllButtons;onWheel:function(wheel){wheel.accepted=true}}
        ColumnLayout {id:matchingContent;anchors.fill:parent;anchors.margins:10;spacing:7
            RowLayout {Layout.fillWidth:true
                Label {text:"Группы листов";font.bold:true}
                Label {text:"Выберите заливку "+(panels.state.matchingSide||"A")+" на карте";Layout.fillWidth:true;color:"#687383";font.pixelSize:12}
                CheckBox {text:"Подсветка";checked:appRoot.matchingHighlight;onClicked:appRoot.matchingHighlight=checked}
                Action {text:matchingBody.visible?"Свернуть":"Развернуть";onClicked:matchingBody.visible=!matchingBody.visible}
                Action {text:"×";onClicked:groupsDialog.close()}
            }
            ColumnLayout {id:matchingBody;Layout.fillWidth:true;spacing:7
                RowLayout {Layout.fillWidth:true;spacing:10;MatchCard {side:"A"} MatchCard {side:"B"}}
                RowLayout {Layout.fillWidth:true
                    Label {text:"Общий класс"}
                    Choice {id:matchTarget;Layout.fillWidth:true;model:panels.state.projectClasses||[];textRole:"name";valueRole:"id";currentIndex:Math.max(0,(panels.state.matchingA||{}).classId-1)}
                    Input {id:matchName;Layout.preferredWidth:200;maximumLength:200;placeholderText:"Или название нового класса"}
                    Action {text:"Сопоставить";highlighted:true;enabled:(!!(panels.state.matchingA||{}).group||!!(panels.state.matchingB||{}).group)&&!panels.state.busy;onClicked:controller.applyMatchedGroups(matchTarget.currentValue||0,matchName.text)}
                }
                RowLayout {Layout.fillWidth:true
                    Label {text:"Готовые заливки исчезают из списков, дубликаты групп удаляются. Можно назначить общую группу одному образцу.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";font.pixelSize:11}
                    Action {text:"Проверить стыки";enabled:!panels.state.busy;onClicked:controller.checkSeams()}
                }
            }
        }
    }
    SurfaceDialog {
        id:classManager;objectName:"workspaceClassManager";title:"Группы полигонов"
        parent:appRoot.mapViewport;anchors.centerIn:null;x:Math.max(0,parent.width-width-8);y:8
        modal:false;dim:false;width:390;height:Math.min(parent.height-16,680)
        property int selectedId:0
        property bool creating:false
        property int seenEpoch:-1
        readonly property var current:(panels.state.projectClasses||[]).find(function(c){return c.id===classManager.selectedId})||({})
        function choose(identity){var rows=panels.state.projectClasses||[];selectedId=rows.some(function(c){return c.id===identity})?identity:((rows[0]||{}).id||0);creating=false;classTitle.text=current.name||"";classFill.text=(current.style||{}).fill||"#80b5d2"}
        function newGroup(){seenEpoch=panels.state.polygonEpoch;selectedId=0;creating=true;classTitle.text="";classFill.text="#80b5d2";classTitle.forceActiveFocus()}
        onAboutToShow:{seenEpoch=panels.state.polygonEpoch;choose(panels.state.groupFocusId||(panels.state.selection||{}).class_id||((panels.state.projectClasses||[])[0]||{}).id||0)}
        contentItem:ColumnLayout {spacing:8
            RowLayout {Layout.fillWidth:true
                Label {text:"Группы проекта";font.bold:true;Layout.fillWidth:true}
                Action {text:"+ Новая";objectName:"newProjectGroup";enabled:!panels.state.busy;onClicked:classManager.newGroup()}
            }
            ListView {Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:130;clip:true;spacing:4;model:panels.state.projectClasses||[];ScrollBar.vertical:ScrollBar {}
                delegate:Rectangle {required property var modelData;width:ListView.view.width;height:42;radius:3;color:classManager.selectedId===modelData.id?"#edf3fc":"#f5f7fa"
                    RowLayout {anchors.fill:parent;anchors.margins:5;spacing:7
                        CheckBox {checked:(panels.state.hiddenClassIds||[]).indexOf(modelData.id)<0;enabled:!panels.state.busy;onClicked:controller.setGroupVisible(modelData.id,checked);ToolTip.visible:hovered;ToolTip.text:"Видимость группы"}
                        Rectangle {objectName:"groupStyleSwatch"+modelData.id;width:20;height:20;radius:3;color:modelData.style.fill;MouseArea {anchors.fill:parent;onClicked:panels.editGroup(modelData.id)}}
                        Label {Layout.fillWidth:true;elide:Text.ElideRight;text:modelData.id+" · "+modelData.name}
                        Label {text:((panels.state.summary||{}).classes||[]).filter(function(c){return c.id===modelData.id}).map(function(c){return c.count}).join("");color:"#687383"}
                    }
                    TapHandler {onTapped:classManager.choose(modelData.id)}
                }
            }
            Label {text:classManager.creating?"Новая группа":"Группа "+classManager.selectedId;font.bold:true}
            Input {id:classTitle;objectName:"projectGroupName";Layout.fillWidth:true;maximumLength:200;placeholderText:"Название группы";enabled:!panels.state.busy}
            RowLayout {Layout.fillWidth:true
                Rectangle {width:28;height:28;color:classFill.acceptableInput?classFill.text:"#ffffff";border.color:"#cbd4e1";radius:3;MouseArea {anchors.fill:parent;onClicked:{if(classManager.creating)panels.chooseColor(classFill);else panels.editGroup(classManager.selectedId)}}}
                Input {id:classFill;objectName:"projectGroupColor";visible:classManager.creating;Layout.fillWidth:true;placeholderText:"#RRGGBB";validator:RegularExpressionValidator {regularExpression:/#[0-9a-fA-F]{6}/}}
                Action {text:classManager.creating?"Цвет…":"Оформление…";Layout.fillWidth:!classManager.creating;onClicked:{if(classManager.creating)panels.chooseColor(classFill);else panels.editGroup(classManager.selectedId)}}
            }
            RowLayout {Layout.fillWidth:true
                Action {text:classManager.creating?"Создать группу":"Сохранить";objectName:"saveProjectGroup";highlighted:true;enabled:!panels.state.busy&&!!classTitle.text.trim()&&classFill.acceptableInput
                    onClicked:{if(classManager.creating)controller.createGroup(classTitle.text,classFill.text);else{var style=Object.assign({},classManager.current.style);style.fill=classFill.text;controller.styleClass(classManager.selectedId,classTitle.text,JSON.stringify(style))}}}
            }
            Action {Layout.fillWidth:true;text:"Назначить выбранным полигонам";enabled:!classManager.creating&&classManager.selectedId>0&&!!(panels.state.selection||{}).object_id&&!panels.state.busy&&!panels.state.polygonsLocked;onClicked:controller.setObjectsGroup(classManager.selectedId)}
            Rectangle {Layout.fillWidth:true;height:1;color:"#dce0e6"}
            Label {text:"Удаление группы";font.bold:true}
            Choice {id:deleteClassTarget;objectName:"deleteClassTarget";visible:((panels.state.summary||{}).classes||[]).some(function(c){return c.id===classManager.selectedId&&c.count>0});Layout.fillWidth:true;model:[{id:0,name:"Перенести объекты в…"}].concat((panels.state.projectClasses||[]).filter(function(c){return c.id!==classManager.selectedId}));textRole:"name";valueRole:"id"}
            Action {Layout.fillWidth:true;text:"Удалить группу";objectName:"deleteProjectGroup";enabled:!classManager.creating&&classManager.selectedId>0&&!panels.state.busy&&!panels.state.polygonsLocked;onClicked:controller.deleteGroup(classManager.selectedId,deleteClassTarget.currentValue||0)}
            Label {text:"При удалении занятой группы выберите назначение: геометрия и атрибуты полигонов сохранятся. Действия можно отменить.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";font.pixelSize:11}
        }
        Connections {target:controller;function onChanged(){if(classManager.visible&&!panels.state.busy&&classManager.seenEpoch!==panels.state.polygonEpoch){classManager.seenEpoch=panels.state.polygonEpoch;classManager.choose(panels.state.groupFocusId||classManager.selectedId)}}}
    }
    SurfaceDialog {
        id:smoothPanel;objectName:"workspaceSmoothing";title:"Сглаживание границ"
        parent:appRoot.mapViewport;anchors.centerIn:null;x:Math.max(0,parent.width-width-8);y:8
        modal:false;dim:false;width:365;height:Math.min(parent.height-16,smoothScope.currentValue==="group"?500:468)
        property bool dirtyPreview:true
        onOpened:{if(!controller.beginSmoothing()){close();return}appRoot.viewing=true;appRoot.originalOnly=false;if(panels.state.lineMode)controller.setLineMode(false);dirtyPreview=true;smoothScope.currentIndex=((panels.state.editSelected||[]).length||(panels.state.selection||{}).object_id)?0:2;smoothGroup.currentIndex=Math.max(0,((panels.state.selection||{}).class_id||1)-1)}
        onClosed:controller.cancelSmoothing()
        function preview(){dirtyPreview=false;controller.previewSmoothing(smoothScope.currentValue,smoothGroup.currentValue||0,smoothMode.currentValue,smoothStrength.value)}
        contentItem:ColumnLayout {spacing:8
            Label {font.pixelSize:12;text:"Общие границы меняются согласованно. Контур участка и узлы соединений закреплены.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383"}
            Choice {id:smoothScope;Layout.fillWidth:true;model:[{key:"selected",name:"Выбранные полигоны"},{key:"group",name:"Одна группа"},{key:"all",name:"Весь результат"}];textRole:"name";valueRole:"key";enabled:!panels.state.busy;onActivated:smoothPanel.dirtyPreview=true}
            Choice {id:smoothGroup;visible:smoothScope.currentValue==="group";Layout.fillWidth:true;model:panels.state.projectClasses||[];textRole:"name";valueRole:"id";enabled:!panels.state.busy;onActivated:smoothPanel.dirtyPreview=true}
            Choice {id:smoothMode;objectName:"smoothingMode";Layout.fillWidth:true;model:[{key:"gentle",name:"Бережное"},{key:"smooth",name:"Плавное"},{key:"simplify",name:"Упрощение"}];textRole:"name";valueRole:"key";enabled:!panels.state.busy;onActivated:smoothPanel.dirtyPreview=true}
            RowLayout {Layout.fillWidth:true
                Label {text:"Сила"}
                Slider {id:smoothStrength;objectName:"smoothingStrength";Layout.fillWidth:true;from:0;to:100;stepSize:1;value:50;enabled:!panels.state.busy;onMoved:smoothPanel.dirtyPreview=true}
                Label {text:Math.round(smoothStrength.value)+"%";Layout.preferredWidth:38}
            }
            Action {text:"Предпросмотр";objectName:"previewSmoothingButton";Layout.fillWidth:true;highlighted:true;enabled:!panels.state.busy&&panels.state.count>0;onClicked:smoothPanel.preview()}
            CheckBox {objectName:"smoothingComparison";text:"Показывать сглаженный контур";checked:panels.state.geometryPreviewVisible!==false;enabled:panels.state.geometryReady;onClicked:controller.showSmoothPreview(checked)}
            Label {text:smoothPanel.dirtyPreview&&panels.state.geometryReady?"Настройки изменены. Обновите предпросмотр.":panels.state.smoothInfo||"Бережное сглаживание выполняется и при оцифровке. Изменение можно отменить.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#566273";font.pixelSize:12}
            Item {Layout.fillHeight:true}
            Action {text:"Применить";objectName:"applySmoothingButton";Layout.fillWidth:true;highlighted:true;enabled:panels.state.geometryReady&&!panels.state.busy&&!smoothPanel.dirtyPreview;onClicked:{controller.acceptGeometry();smoothPanel.close()}}
        }
    }
    SurfaceDialog {
        id:stylesDialog;objectName:"workspaceStylesDialog";title:"Оформление · "+(current.name||"группа");width:480;height:460
        property int identity:0
        property var current:({})
        function choose() {current=(panels.state.projectClasses||[]).find(function(c){return c.id===stylesDialog.identity})||({})}
        onOpened:choose()
        footer:Item {implicitHeight:54
            RowLayout {anchors.fill:parent;anchors.leftMargin:12;anchors.rightMargin:12
            Action {objectName:"applyGroupStyle";text:"Применить оформление";highlighted:true;enabled:!panels.state.busy&&!!stylesDialog.current.id
                onClicked:{controller.styleClass(stylesDialog.current.id,className.text,JSON.stringify({fill:fillColor.text,outline:edgeColor.text,width:edgeWidth.value/10,opacity:fillOpacity.value,label:classLabel.currentValue||""}));stylesDialog.close()}}
                Item {Layout.fillWidth:true}
                Action {text:"Отмена";onClicked:stylesDialog.close()}
            }
        }
        contentItem:ColumnLayout {
            spacing:12
            Label {text:"Группа "+stylesDialog.identity;font.bold:true;color:"#687383"}
            Input {id:className;maximumLength:200;Layout.fillWidth:true;text:stylesDialog.current.name||"";placeholderText:"Название класса"}
            GridLayout {columns:3;columnSpacing:12;rowSpacing:10
                Label {text:"Заливка"} Rectangle {width:28;height:28;color:fillColor.text.match(/^#[0-9a-f]{6}$/i)?fillColor.text:"transparent";border.color:"#aeb8c5";MouseArea {anchors.fill:parent;onClicked:panels.chooseColor(fillColor)}}
                Input {id:fillColor;objectName:"groupStyleFill";Layout.fillWidth:true;text:stylesDialog.current.style?stylesDialog.current.style.fill:"#80b5d2";placeholderText:"#RRGGBB"}
                Label {text:"Граница"} Rectangle {width:28;height:28;color:edgeColor.text.match(/^#[0-9a-f]{6}$/i)?edgeColor.text:"transparent";border.color:"#aeb8c5";MouseArea {anchors.fill:parent;onClicked:panels.chooseColor(edgeColor)}}
                Input {id:edgeColor;Layout.fillWidth:true;text:stylesDialog.current.style?stylesDialog.current.style.outline:"#334b5f"}
                Label {text:"Толщина"} Counter {id:edgeWidth;factor:10;from:1;to:100;value:stylesDialog.current.style?Math.round(stylesDialog.current.style.width*10):10;editable:true;stepSize:1}
                Label {text:"px";color:"#687383"}
                Label {text:"Непрозрачность"} Slider {id:fillOpacity;from:0;to:1;value:stylesDialog.current.style?stylesDialog.current.style.opacity:.5;Layout.fillWidth:true}
                Label {text:Math.round(fillOpacity.value*100)+"%"}
            }
            Label {text:"Цвет оформления независим от цвета распознавания исходного листа.";wrapMode:Text.WordWrap;Layout.fillWidth:true;color:"#687383"}
            Choice {id:classLabel;Layout.fillWidth:true;model:[{key:"",label:"Без подписей"},{key:"class_name",label:"Название класса"},{key:"object_id",label:"Номер объекта"}].concat((panels.state.fields||[]).map(function(f){return {key:f.key,label:f.label}}));textRole:"label";valueRole:"key"
                currentIndex:Math.max(0,model.findIndex(function(f){return f.key===((stylesDialog.current.style||{}).label||"")}))}
            Item {Layout.fillHeight:true}

        }
    }
    SurfaceDialog {
        id:linesDialog;objectName:"workspaceLinesDialog";title:"Типы и оформление линий"
        parent:appRoot.mapViewport;anchors.centerIn:null;x:Math.max(0,parent.width-width-8);y:8
        modal:false;dim:false;width:380;height:Math.min(parent.height-16,610)
        property var selectedType:({})
        function choose(){selectedType=(panels.state.lineTypes||[]).find(function(t){return t.id===panels.state.currentLineType})||({})}
        function save(draw){
            var colors=selectedType.colors||[lineDisplayColor.text]
            if(!controller.saveLineType(JSON.stringify({id:selectedType.id||0,name:lineTypeName.text.trim()||"Разлом",colors:colors,style:{color:lineDisplayColor.text,width:lineWidth.value/10,dash:["solid","dash","dot"][lineDash.currentIndex],label:lineLabel.currentValue||""}})))return false
            choose()
            if(draw){controller.setLineMode(true);controller.setLineTool("draw");appRoot.viewing=false;close()}
            return true
        }
        onOpened:choose()
        contentItem:ColumnLayout {spacing:9
            RowLayout {Layout.fillWidth:true
                Choice {id:lineTypeChoice;Layout.fillWidth:true;model:panels.state.lineTypes||[];textRole:"name";valueRole:"id";displayText:linesDialog.selectedType.id?currentText:"Новый тип";currentIndex:model.findIndex(function(t){return t.id===panels.state.currentLineType})
                    onActivated:{controller.chooseLineType(currentValue);linesDialog.choose()}}
                Action {text:"＋";onClicked:linesDialog.selectedType=({});ToolTip.visible:hovered;ToolTip.text:"Новый тип линий"}
            }
            TabBar {id:lineTypePages;objectName:"lineTypePages";Layout.fillWidth:true
                Repeater {model:["Рисование","Бета-поиск"]
                    TabButton {required property string modelData;text:modelData
                        contentItem:Text {text:parent.text;color:parent.checked?"#245bb7":"#566273";font:parent.font;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
                        background:Rectangle {color:parent.checked?"#ffffff":"#edf0f4";Rectangle {anchors.bottom:parent.bottom;width:parent.width;height:2;color:"#356ac3";visible:parent.parent.checked}}
                    }
                }
            }
            StackLayout {Layout.fillWidth:true;Layout.fillHeight:true;currentIndex:lineTypePages.currentIndex
                ColumnLayout {spacing:9
                    Label {text:"Название типа";font.bold:true}
                    Input {id:lineTypeName;Layout.fillWidth:true;text:linesDialog.selectedType.name||"";placeholderText:"Разлом"}
                    Label {text:"Цвет линии";font.bold:true}
                    RowLayout {Layout.fillWidth:true
                        Rectangle {width:28;height:28;radius:3;color:lineDisplayColor.text.match(/^#[0-9a-f]{6}$/i)?lineDisplayColor.text:"transparent";MouseArea {anchors.fill:parent;onClicked:panels.chooseColor(lineDisplayColor)}}
                        Input {id:lineDisplayColor;Layout.fillWidth:true;text:linesDialog.selectedType.style?linesDialog.selectedType.style.color:"#b53239";placeholderText:"#RRGGBB"}
                        Action {text:"Выбрать";onClicked:panels.chooseColor(lineDisplayColor)}
                    }
                    Action {text:"Взять цвет с карты · пипетка";Layout.fillWidth:true;enabled:!panels.state.busy
                        onClicked:if(linesDialog.save(false)){var identity=panels.state.currentLineType;linesDialog.close();panels.lineStyleColourRequested(identity,false)}}
                    RowLayout {Layout.fillWidth:true
                        Label {text:"Толщина";Layout.fillWidth:true}
                        Counter {id:lineWidth;factor:10;from:1;to:200;value:linesDialog.selectedType.style?Math.round(linesDialog.selectedType.style.width*10):20;editable:true;palette.text:"#263445";palette.base:"white"}
                        Label {text:"px"}
                    }
                    Choice {id:lineDash;Layout.fillWidth:true;model:["Сплошная линия","Пунктир","Точки"];currentIndex:linesDialog.selectedType.style?["solid","dash","dot"].indexOf(linesDialog.selectedType.style.dash):0}
                    Choice {id:lineLabel;Layout.fillWidth:true;model:[{key:"",label:"Без подписи"},{key:"name",label:"Название линии"},{key:"object_id",label:"Номер объекта"},{key:"type_name",label:"Тип линии"}].concat((panels.state.lineFields||[]).map(function(f){return {key:f.key,label:f.label}}));textRole:"label";valueRole:"key";currentIndex:Math.max(0,model.findIndex(function(f){return f.key===((linesDialog.selectedType.style||{}).label||"")}))}
                    Label {Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";text:"Рисуйте разломы по исходнику. Название, тип, длина и собственные поля доступны в панели «Атрибуты» (A).";font.pixelSize:12}
                    Item {Layout.fillHeight:true}
                    RowLayout {Layout.fillWidth:true
                        Action {text:"Сохранить тип";enabled:!panels.state.busy;onClicked:linesDialog.save(false)}
                        Action {text:"Рисовать линию";highlighted:true;Layout.fillWidth:true;enabled:!panels.state.busy;onClicked:linesDialog.save(true)}
                    }
                }
                ColumnLayout {spacing:10
                    Label {text:"Автоопределение · Бета";font.bold:true;color:"#95600c"}
                    Label {Layout.fillWidth:true;wrapMode:Text.WordWrap;text:"Экспериментальный поиск по цветам. Может находить подписи и границы вместо разломов. Основной сценарий — рисование вручную.";color:"#687383"}
                    Label {text:"Образцы цвета активного типа";font.bold:true}
                    Flow {Layout.fillWidth:true;spacing:6
                        Repeater {model:linesDialog.selectedType.colors||[]
                            delegate:Rectangle {required property string modelData;width:28;height:28;radius:3;color:modelData;border.color:"#aeb8c5"}}
                    }
                    Action {text:"Добавить образец с карты";Layout.fillWidth:true;enabled:!panels.state.busy;onClicked:if(linesDialog.save(false)){var identity=panels.state.currentLineType;linesDialog.close();panels.lineStyleColourRequested(identity,true)}}
                    Label {Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";text:"Поиск использует заданные типы проекта на активном листе. Предложения нужно проверить до применения."}
                    Item {Layout.fillHeight:true}
                    Action {text:"Найти линии · Бета";Layout.fillWidth:true;enabled:(panels.state.lineTypes||[]).length>0&&!panels.state.busy;onClicked:{controller.detectLines();linesDialog.close()}}
                }
            }
        }
    }
    Dialog {
        id:lineAttributes;objectName:"workspaceLineAttributes";title:"Атрибуты линий"
        parent:appRoot.mapViewport;x:parent.width-width;y:0;width:340;height:Math.min(parent.height,450+Math.min(5,(panels.state.lineFields||[]).length)*68)
        modal:false;dim:false;focus:false;closePolicy:Popup.NoAutoClose;visible:appRoot.attributesPinned&&panels.state.lineMode===true
        padding:14;background:Rectangle {color:"white";border.color:"#dce0e6"}
        header:RowLayout {height:46
            Label {text:"Атрибуты линий";font.pixelSize:15;font.bold:true;leftPadding:14;Layout.fillWidth:true}
            Action {text:"×";onClicked:appRoot.attributesPinned=false}
        }
        property string selectionKey:JSON.stringify(panels.state.lineSelected||[])+JSON.stringify(panels.state.lineSelection||{})
        onSelectionKeyChanged:if(visible){draft=Object.assign({},panels.state.lineSelection||{});changes=({})}
        property var draft:({})
        property var changes:({})
        function edit(key,value){var d=Object.assign({},draft);d[key]=value;draft=d;var c=Object.assign({},changes);c[key]=value;changes=c}
        onOpened:{draft=Object.assign({},panels.state.lineSelection||{});changes=({})}
        contentItem:ColumnLayout {
            Label {text:"Выбрано: "+(panels.state.lineSelected||[]).length+" · "+Number((panels.state.lineSelection||{}).length_m||0).toFixed(1)+" м";font.bold:true}
            Label {text:"При выборе нескольких линий меняются только отредактированные поля.";color:"#687383";Layout.fillWidth:true;wrapMode:Text.WordWrap}
            Input {id:lineName;objectName:"lineAttributeName";Layout.fillWidth:true;text:lineAttributes.draft.name||"";placeholderText:"Название";onTextEdited:lineAttributes.edit("name",text)}
            Choice {Layout.fillWidth:true;model:panels.state.lineTypes||[];textRole:"name";valueRole:"id";currentIndex:Math.max(0,model.findIndex(function(t){return t.id===lineAttributes.draft.type_id}));onActivated:lineAttributes.edit("type_id",currentValue)}
            ScrollView {id:lineNoteView;Layout.fillWidth:true;Layout.preferredHeight:80;clip:true
                TextArea {id:lineNote;width:lineNoteView.availableWidth;color:"#263445";placeholderTextColor:"#697586";text:lineAttributes.draft.note||"";placeholderText:"Примечание";wrapMode:TextEdit.Wrap;onTextChanged:if(activeFocus)lineAttributes.edit("note",text)}}
            ScrollView {Layout.fillHeight:true;Layout.fillWidth:true;clip:true
                ColumnLayout {width:parent.width
                    Repeater {model:panels.state.lineFields||[]
                        delegate:ColumnLayout {required property var modelData;Layout.fillWidth:true
                            RowLayout {Layout.fillWidth:true
                                Label {text:modelData.label;Layout.fillWidth:true;elide:Text.ElideRight}
                                Action {text:"⋯";onClicked:lineFieldDialog.edit(modelData)}
                            }
                            Input {Layout.fillWidth:true;visible:modelData.type!=="choice"&&modelData.type!=="boolean";text:lineAttributes.draft[modelData.key]===undefined||lineAttributes.draft[modelData.key]===null?"":String(lineAttributes.draft[modelData.key]);placeholderText:modelData.type==="date"?"ГГГГ-ММ-ДД":"Не задано"
                                onTextEdited:lineAttributes.edit(modelData.key,text)}
                            Choice {Layout.fillWidth:true;visible:modelData.type==="choice"||modelData.type==="boolean"
                                model:modelData.type==="boolean"?["Не задано","Да","Нет"]:["Не задано"].concat(modelData.choices)
                                currentIndex:lineAttributes.draft[modelData.key]===undefined||lineAttributes.draft[modelData.key]===null?0:modelData.type==="boolean"?(lineAttributes.draft[modelData.key]?1:2):model.indexOf(lineAttributes.draft[modelData.key])
                                onActivated:lineAttributes.edit(modelData.key,currentIndex===0?null:modelData.type==="boolean"?currentIndex===1:currentText)}
                        }
                    }
                }
            }
            RowLayout {
                Action {text:"Добавить поле…";onClicked:lineFieldDialog.edit(null)}
                Item {Layout.fillWidth:true}
                Action {id:lineApply;objectName:"lineApplyAttributes";text:"Применить";highlighted:true;enabled:(panels.state.lineSelected||[]).length>0&&Object.keys(lineAttributes.changes).length>0&&!panels.state.lineLocked&&!panels.state.busy;onClicked:{
                    if(controller.applyLineAttributes(JSON.stringify(lineAttributes.changes)))lineAttributes.changes=({})
                }}
            }
        }
            Shortcut {sequences:["Ctrl+Return","Meta+Return"];enabled:lineAttributes.visible&&lineApply.enabled;onActivated:lineApply.clicked()}
    }
    SurfaceDialog {
        id:lineFieldsDialog;objectName:"workspaceLineFields";title:"Поля линий";width:500;height:390
        contentItem:ColumnLayout {spacing:10
            Label {text:"Номер, тип, имя, примечание и длина входят в базовые атрибуты. Здесь настраиваются дополнительные поля.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383"}
            ListView {Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:panels.state.lineFields||[]
                delegate:ItemDelegate {required property var modelData;width:ListView.view.width;text:modelData.label+" · "+modelData.key;onClicked:lineFieldDialog.edit(modelData)}
                ScrollBar.vertical:ScrollBar {}
                Label {anchors.centerIn:parent;visible:parent.count===0;text:"Дополнительных полей пока нет";color:"#87919e"}
            }
            Action {text:"Добавить поле…";onClicked:lineFieldDialog.edit(null)}
        }
    }
    SurfaceDialog {
        id:lineFieldDialog;title:original?"Поле линий":"Новое поле линий";width:520;height:400
        property string original:""
        function edit(field){original=field?field.key:"";lfKey.text=original;lfName.text=field?field.label:"";lfType.currentIndex=field?["text","integer","number","boolean","date","choice"].indexOf(field.type):0;lfChoices.text=field?(field.choices||[]).join(";"):"";lfRequired.checked=field?field.required:false;open()}
        contentItem:ColumnLayout {
            Input {id:lfKey;Layout.fillWidth:true;enabled:!lineFieldDialog.original;placeholderText:"Код для ГИС, например confidence"}
            Input {id:lfName;Layout.fillWidth:true;placeholderText:"Название поля"}
            Choice {id:lfType;Layout.fillWidth:true;model:["Текст","Целое число","Число","Да / Нет","Дата","Список"]}
            Input {id:lfChoices;Layout.fillWidth:true;visible:lfType.currentIndex===5;placeholderText:"Варианты через ;"}
            CheckBox {id:lfRequired;text:"Обязательное при экспорте"}
            Item {Layout.fillHeight:true}
            RowLayout {Layout.fillWidth:true
                Action {text:"Удалить поле";visible:!!lineFieldDialog.original;onClicked:if(controller.removeLineField(lineFieldDialog.original))lineFieldDialog.close()}
                Item {Layout.fillWidth:true}
                Action {text:"Сохранить поле";highlighted:true;onClicked:{if(controller.saveLineField(JSON.stringify({key:lfKey.text,label:lfName.text,type:["text","integer","number","boolean","date","choice"][lfType.currentIndex],required:lfRequired.checked,default:null,choices:lfType.currentIndex===5?lfChoices.text.split(";").map(function(s){return s.trim()}):[]}),lineFieldDialog.original))lineFieldDialog.close()}}
            }
        }
    }
    SurfaceDialog {
        id:summaryDialog;objectName:"workspaceSummary";title:"Сводка участка";height:Math.min(appRoot.height-70,250+Math.min(10,(panels.state.projectClasses||[]).length)*38)
        contentItem:ColumnLayout {
            Label {text:Number((panels.state.summary||{}).area_m2||0)/10000+" га · "+Number((panels.state.summary||{}).length_m||0).toFixed(1)+" м линий";font.bold:true}
            Label {text:"Геодезические измерения. Сумма площадей относится к имеющимся полигонам; непокрытые части участка проверяются отдельно.";wrapMode:Text.WordWrap;Layout.fillWidth:true;color:"#687383"}
            ListView {Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:(panels.state.summary||{}).classes||[];ScrollBar.vertical:ScrollBar {}
                delegate:RowLayout {required property var modelData;width:ListView.view.width;height:38
                    Label {text:modelData.name;Layout.fillWidth:true;elide:Text.ElideRight}
                    Label {text:modelData.count+" полигонов";Layout.preferredWidth:110}
                    Label {text:(modelData.area_m2/10000).toFixed(3)+" га";Layout.preferredWidth:130}
                    Label {text:(modelData.share*100).toFixed(1)+"%";Layout.preferredWidth:65}
                }
            }
            Action {text:"Проверить покрытие и стыки";onClicked:{summaryDialog.close();controller.checkSeams()}}
        }
    }
    SurfaceDialog {
        id:checkpointDialog;title:"Контрольные сохранения";width:690
        contentItem:ColumnLayout {
            RowLayout {Input {id:checkpointName;Layout.fillWidth:true;placeholderText:"Например, до правки границ"}
                Action {text:"Создать";enabled:!panels.state.busy;onClicked:controller.createCheckpoint(checkpointName.text)}}
            Label {text:"Снимок сохраняет листы, группы, полигоны, линии и оформление. Возврат можно отменить.";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383"}
            ListView {Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:panels.state.checkpointItems||[];ScrollBar.vertical:ScrollBar {}
                delegate:RowLayout {required property var modelData;width:ListView.view.width;height:42
                    Label {text:modelData.name;Layout.fillWidth:true;elide:Text.ElideRight}
                    Action {text:"Восстановить";enabled:!panels.state.busy;onClicked:{controller.restoreCheckpoint(modelData.path);checkpointDialog.close()}}
                }
            }
        }
    }
    SurfaceDialog {
        id:aboutDialog;objectName:"workspaceAbout";title:"О GeoTrace";width:620;height:480
        contentItem:ColumnLayout {
            Label {text:"GeoTrace "+controller.releaseLabel;font.pixelSize:22;font.bold:true}
            Label {text:"Оцифровка карт, работа с участками и выпуск картографических материалов.";wrapMode:Text.WordWrap;Layout.fillWidth:true}
            Rectangle {Layout.fillWidth:true;height:1;color:"#dce0e6"}
            Label {text:"TJS TECH × TJS HORIZON";font.bold:true;color:"#566273"}
            Label {text:"TJS PRIME — разработка приложения\nTJS MERIDIAN / DATA — ГИС и пространственные данные\nTJS GEOPHYSICS — предметные сценарии\nTJS XLAB — исследования и экспериментальные методы\nTJS QUANTUM — направление вычислительных исследований";wrapMode:Text.WordWrap;Layout.fillWidth:true;color:"#566273";lineHeight:1.5}
            Label {text:"Автоматические предложения требуют проверки. Цветовой класс не устанавливает геологический возраст или название подразделения.";wrapMode:Text.WordWrap;Layout.fillWidth:true;color:"#687383"}
            Item {Layout.fillHeight:true}
            Action {text:"Проверить обновления…";onClicked:{aboutDialog.close();updateDialog.open();controller.checkUpdates()}}
            Action {text:"Руководство пользователя";onClicked:controller.openManual()}
        }
    }
    SurfaceDialog {
        id:updateDialog;objectName:"workspaceUpdates";title:"Обновление GeoTrace";width:560;height:430
        contentItem:ColumnLayout {spacing:12
            Label {text:"Установлена версия "+controller.releaseLabel;font.bold:true}
            Label {text:panels.state.updateMessage||"";Layout.fillWidth:true;wrapMode:Text.WordWrap;color:panels.state.updateState==="error"?"#a3313f":"#303944"}
            ProgressBar {Layout.fillWidth:true;visible:panels.state.updateState==="checking"||panels.state.updateState==="downloading";indeterminate:panels.state.updateState==="checking";from:0;to:100;value:panels.state.updateProgress||0}
            ScrollView {Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                TextArea {text:panels.state.updateNotes||"Изменения новой версии появятся здесь после проверки.";readOnly:true;textFormat:TextEdit.PlainText;wrapMode:TextEdit.Wrap;color:"#566273";background:null}}
            RowLayout {Layout.fillWidth:true
                Action {text:"Проверить";objectName:"checkUpdatesButton";enabled:["checking","downloading"].indexOf(panels.state.updateState)<0;onClicked:controller.checkUpdates()}
                Action {text:"Скачать";visible:panels.state.updateState==="available";highlighted:true;onClicked:controller.downloadUpdate()}
                Action {text:"Отменить загрузку";visible:panels.state.updateState==="downloading";onClicked:controller.cancelUpdateDownload()}
                Action {text:"Установить и закрыть GeoTrace";visible:panels.state.updateState==="ready";enabled:!panels.state.busy;highlighted:true;onClicked:{updateDialog.close();appRoot.request("installUpdate")}}
                Item {Layout.fillWidth:true}
            }
            Action {text:"Открыть страницу выпусков";onClicked:controller.openReleasePage()}
        }
    }
    SurfaceDialog {
        id:commandDialog;objectName:"workspaceCommandSearch";title:"Найти действие · Ctrl/Cmd+K";width:650;height:470
        onOpened:commandQuery.forceActiveFocus()
        contentItem:ColumnLayout {
            Input {id:commandQuery;Layout.fillWidth:true;placeholderText:"Листы, разломы, атрибуты, макет…";onTextChanged:commandResults.currentIndex=0
                Keys.onReturnPressed:{if(commandResults.currentItem)commandResults.currentItem.run()}}
            ListView {id:commandResults;Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                model:panels.actions.filter(function(a){return a.name.toLowerCase().indexOf(commandQuery.text.toLowerCase())>=0})
                delegate:ItemDelegate {required property var modelData;width:ListView.view.width;text:modelData.name;highlighted:ListView.isCurrentItem
                    function run(){commandDialog.close();panels.openPanel(modelData.panel)}
                    onClicked:run()}
            }
        }
    }
    Window {
        id:layoutWindow;objectName:"workspaceLayoutWindow";title:"GeoTrace · макет";width:1240;height:850;minimumWidth:950;minimumHeight:650;color:"#e8ebef"
        property var draft:({})
        property bool arrange: false
        function setValue(key,value){var d=Object.assign({},draft);d[key]=value;draft=d}
        function legendRows(){var ids=(draft.class_ids||[]).slice();(panels.state.projectClasses||[]).forEach(function(c){if(ids.indexOf(c.id)<0)ids.push(c.id)});return ids.map(function(id){return panels.state.projectClasses[id-1]})}
        function moveLegend(id,direction){var ids=(draft.class_ids||[]).slice(),i=ids.indexOf(id),j=i+direction;if(i<0||j<0||j>=ids.length)return;var swap=ids[j];ids[j]=ids[i];ids[i]=swap;setValue("class_ids",ids)}
        function changeLegend(id,value){var names=Object.assign({},draft.legend_names||{});names[String(id)]=value;setValue("legend_names",names)}
        function elementRect(key,values){var positions=Object.assign({},draft.positions||{});positions[key]=values;setValue("positions",positions)}
        Control {anchors.fill:parent;palette:appRoot.palette;font.family:appRoot.font.family;font.pixelSize:13
        ColumnLayout {anchors.fill:parent;anchors.margins:12;spacing:10
            RowLayout {
                Label {text:"Макет карты";font.pixelSize:19;font.bold:true}
                Item {Layout.fillWidth:true}
                Choice {Layout.preferredWidth:190;model:panels.state.layoutTemplates||[];textRole:"name";displayText:currentIndex>=0?currentText:"Шаблоны";onActivated:layoutWindow.draft=controller.loadLayoutTemplate(currentIndex)}
                Action {text:"Сохранить шаблон";enabled:!panels.state.busy;onClicked:controller.saveLayoutTemplate(JSON.stringify(layoutWindow.draft))}
                Action {text:"Предпросмотр";enabled:!panels.state.busy;onClicked:controller.previewLayout(JSON.stringify(layoutWindow.draft))}
                Action {text:"Элементы";highlighted:layoutWindow.arrange;onClicked:layoutWindow.arrange=!layoutWindow.arrange}
                Action {text:"Экспорт…";highlighted:true;enabled:!panels.state.busy;onClicked:layoutExport.open()}
                Action {text:"Атлас…";enabled:!panels.state.busy;onClicked:atlasExport.open()}
            }
            RowLayout {Layout.fillHeight:true;Layout.fillWidth:true;spacing:12
                ColumnLayout {objectName:"layoutSettings";Layout.minimumWidth:330;Layout.preferredWidth:330;Layout.maximumWidth:330;Layout.fillHeight:true;spacing:12
                    TabBar {id:layoutSections;objectName:"layoutSections";Layout.fillWidth:true
                        Repeater {model:["Лист","Слои","Легенда","Подписи"]
                            TabButton {required property string modelData;text:modelData
                                contentItem:Text {text:parent.text;color:parent.checked?"#245bb7":"#566273";font:parent.font;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
                                background:Rectangle {color:parent.checked?"#ffffff":"#edf0f4";Rectangle {anchors.bottom:parent.bottom;width:parent.width;height:2;color:"#356ac3";visible:parent.parent.checked}}
                            }
                        }
                    }
                    StackLayout {Layout.fillWidth:true;Layout.fillHeight:true;currentIndex:layoutSections.currentIndex
                        ColumnLayout {spacing:10
                        Label {text:"Страница";font.bold:true}
                        Input {Layout.fillWidth:true;placeholderText:"Название шаблона";text:layoutWindow.draft.name||"";onEditingFinished:layoutWindow.setValue("name",text)}
                        Choice {Layout.fillWidth:true;model:["A4","A3","A2","A1","A0"];currentIndex:Math.max(0,model.indexOf(layoutWindow.draft.paper||"A3"));onActivated:layoutWindow.setValue("paper",currentText)}
                        CheckBox {text:"Альбомная ориентация";checked:layoutWindow.draft.landscape===true;onClicked:layoutWindow.setValue("landscape",checked)}
                        RowLayout {Label {text:"DPI"} Counter {from:72;to:600;stepSize:50;editable:true;value:layoutWindow.draft.dpi||300;onValueModified:layoutWindow.setValue("dpi",value)}}
                        Label {text:"Область карты";font.bold:true}
                        Choice {Layout.fillWidth:true;model:["Участок KML / все листы","Текущий вид карты","Выделить рамку на карте"];currentIndex:Math.max(0,["area","view","bounds"].indexOf(layoutWindow.draft.extent||"area"))
                            onActivated:{if(currentIndex===0){layoutWindow.setValue("extent","area");layoutWindow.setValue("bounds",null)}
                                else if(currentIndex===1){layoutWindow.setValue("extent","view");layoutWindow.setValue("bounds",controller.currentMapBounds())}
                                else{layoutWindow.hide();panels.mapFrameRequested()}}}
                        Label {text:"Масштаб 1 : · 0 — вписать область";color:"#566273";font.pixelSize:12}
                        Input {Layout.fillWidth:true;placeholderText:"Например, 100000";text:String(layoutWindow.draft.scale||0);onEditingFinished:layoutWindow.setValue("scale",Number(text.replace(/\s/g,"")))}
                            Item {Layout.fillHeight:true}
                        }
                        ColumnLayout {spacing:10
                        Label {text:"Состав";font.bold:true}
                        Repeater {model:[{key:"raster",label:"Исходная карта"},{key:"polygons",label:"Полигоны"},{key:"lines",label:"Линии"},{key:"legend",label:"Легенда"},{key:"grid",label:"Координатная сетка"},{key:"north",label:"Север"},{key:"scalebar",label:"Масштабная линейка"}]
                            delegate:CheckBox {required property var modelData;text:modelData.label;checked:layoutWindow.draft[modelData.key]===true;onClicked:layoutWindow.setValue(modelData.key,checked)}}
                            Item {Layout.fillHeight:true}
                            Label {Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";text:"Линейка показывает наземное расстояние. Контуры и текст в PDF остаются векторными."}
                        }
                        ColumnLayout {spacing:10
                        Label {text:"Легенда";font.bold:true}
                        Choice {Layout.fillWidth:true;model:[{key:"class_name",label:"Название класса"}].concat((panels.state.fields||[]).map(function(f){return {key:f.key,label:f.label}}));textRole:"label";valueRole:"key";currentIndex:Math.max(0,model.findIndex(function(f){return f.key===layoutWindow.draft.legend_field}));onActivated:layoutWindow.setValue("legend_field",currentValue)}
                        ListView {Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:8;model:layoutWindow.legendRows();ScrollBar.vertical:ScrollBar {}
                            delegate:ColumnLayout {required property var modelData;width:ListView.view.width;spacing:3
                                RowLayout {Layout.fillWidth:true
                                    CheckBox {checked:(layoutWindow.draft.class_ids||[]).indexOf(modelData.id)>=0;onClicked:{var ids=(layoutWindow.draft.class_ids||[]).slice();if(checked)ids.push(modelData.id);else ids=ids.filter(function(i){return i!==modelData.id});layoutWindow.setValue("class_ids",ids)}}
                                    Rectangle {width:12;height:12;color:modelData.style.fill;radius:2}
                                    Label {text:modelData.name;Layout.fillWidth:true;elide:Text.ElideRight;font.pixelSize:12}
                                    Action {text:"↑";enabled:(layoutWindow.draft.class_ids||[]).indexOf(modelData.id)>0;onClicked:layoutWindow.moveLegend(modelData.id,-1)}
                                    Action {text:"↓";enabled:(layoutWindow.draft.class_ids||[]).indexOf(modelData.id)>=0;onClicked:layoutWindow.moveLegend(modelData.id,1)}
                                }
                                Input {Layout.fillWidth:true;placeholderText:"Название в легенде";text:(layoutWindow.draft.legend_names||{})[String(modelData.id)]||modelData.name;onEditingFinished:layoutWindow.changeLegend(modelData.id,text)}
                            }
                        }
                        }
                        ColumnLayout {spacing:10
                        Label {text:"Подписи";font.bold:true}
                        Input {Layout.fillWidth:true;placeholderText:"Заголовок";text:layoutWindow.draft.title||"";onEditingFinished:layoutWindow.setValue("title",text)}
                        Input {Layout.fillWidth:true;placeholderText:"Подзаголовок";text:layoutWindow.draft.subtitle||"";onEditingFinished:layoutWindow.setValue("subtitle",text)}
                        Input {Layout.fillWidth:true;placeholderText:"Источник / подпись внизу";text:layoutWindow.draft.footer||"";onEditingFinished:layoutWindow.setValue("footer",text)}
                        Choice {Layout.fillWidth:true;model:[{key:"",label:"Подписи из оформления классов"},{key:"class_name",label:"Название класса"},{key:"object_id",label:"Номер объекта"}].concat((panels.state.fields||[]).map(function(f){return {key:f.key,label:f.label}}));textRole:"label";valueRole:"key";currentIndex:Math.max(0,model.findIndex(function(f){return f.key===layoutWindow.draft.labels}));onActivated:layoutWindow.setValue("labels",currentValue)}
                            Item {Layout.fillHeight:true}
                        }
                    }
                    Label {Layout.fillWidth:true;wrapMode:Text.WordWrap;color:"#687383";font.pixelSize:11;text:"«Элементы» — положение и размер рамок на листе. Растровая часть PDF — до 24 млн пикселей на страницу."}
                }
                Rectangle {Layout.minimumWidth:300;Layout.fillWidth:true;Layout.fillHeight:true;color:"#d9dee5";radius:3
                    Image {id:layoutPreview;objectName:"layoutPreview";anchors.fill:parent;anchors.margins:18;source:panels.state.layoutPreviewUrl||"";fillMode:Image.PreserveAspectFit;cache:false;smooth:true
                        Item {id:pageHandles;visible:layoutWindow.arrange&&!!layoutPreview.source
                            x:(layoutPreview.width-layoutPreview.paintedWidth)/2;y:(layoutPreview.height-layoutPreview.paintedHeight)/2;width:layoutPreview.paintedWidth;height:layoutPreview.paintedHeight
                            Repeater {model:[{key:"map",name:"Карта"},{key:"title",name:"Заголовок"},{key:"legend",name:"Легенда"},{key:"north",name:"Север"},{key:"scalebar",name:"Линейка"},{key:"footer",name:"Подвал"}]
                                delegate:Rectangle {id:handle;required property var modelData
                                    property var rect:(layoutWindow.draft.positions||{})[modelData.key]||[0,0,.1,.1]
                                    x:rect[0]*pageHandles.width;y:rect[1]*pageHandles.height;width:rect[2]*pageHandles.width;height:rect[3]*pageHandles.height
                                    color:"#08356ac3";border.color:"#356ac3";border.width:1
                                    Label {text:handle.modelData.name;color:"#356ac3";font.pixelSize:10;background:Rectangle {color:"#eaf2ff"}}
                                    MouseArea {anchors.fill:parent;cursorShape:Qt.SizeAllCursor
                                        property point origin;property var before
                                        onPressed:function(mouse){origin=mapToItem(pageHandles,mouse.x,mouse.y);before=handle.rect.slice()}
                                        onPositionChanged:function(mouse){if(!pressed)return;var p=mapToItem(pageHandles,mouse.x,mouse.y);layoutWindow.elementRect(handle.modelData.key,[Math.max(0,Math.min(1-before[2],before[0]+(p.x-origin.x)/pageHandles.width)),Math.max(0,Math.min(1-before[3],before[1]+(p.y-origin.y)/pageHandles.height)),before[2],before[3]])}
                                        onReleased:if(!panels.state.busy)controller.previewLayout(JSON.stringify(layoutWindow.draft))
                                    }
                                    Rectangle {anchors.right:parent.right;anchors.bottom:parent.bottom;width:10;height:10;color:"#356ac3"
                                        MouseArea {anchors.fill:parent;cursorShape:Qt.SizeFDiagCursor
                                            onPositionChanged:function(mouse){if(!pressed)return;var p=mapToItem(pageHandles,mouse.x,mouse.y),r=handle.rect;layoutWindow.elementRect(handle.modelData.key,[r[0],r[1],Math.max(.05,Math.min(1-r[0],p.x/pageHandles.width-r[0])),Math.max(.04,Math.min(1-r[1],p.y/pageHandles.height-r[1]))])}
                                            onReleased:if(!panels.state.busy)controller.previewLayout(JSON.stringify(layoutWindow.draft))
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Label {anchors.centerIn:parent;visible:!panels.state.layoutPreviewUrl;text:panels.state.busy?"Готовлю макет…":"Настройте страницу и нажмите «Предпросмотр»";color:"#687383"}
                }
            }
            Label {text:panels.state.status||"";color:panels.state.error?"#a43725":"#687383";Layout.fillWidth:true;elide:Text.ElideRight}
        }
        }
        FileDialog {id:layoutExport;title:"Экспорт макета";fileMode:FileDialog.SaveFile;nameFilters:["PDF (*.pdf)","PNG (*.png)","SVG (*.svg)"];defaultSuffix:selectedNameFilter.extensions.length?selectedNameFilter.extensions[0].replace("*.",""):"pdf";onAccepted:controller.exportLayout(selectedFile,JSON.stringify(layoutWindow.draft))}
        FileDialog {id:atlasExport;title:"Атлас участков KML";fileMode:FileDialog.SaveFile;nameFilters:["PDF (*.pdf)"];defaultSuffix:"pdf";onAccepted:controller.exportAtlas(selectedFile,JSON.stringify(layoutWindow.draft))}
    }
    Connections {target:controller
        function onExportFrameReady(){layoutWindow.setValue("extent","bounds");layoutWindow.setValue("bounds",controller.state.exportFrameBounds);layoutWindow.show()}
        function onGroupsMatched(){matchName.text=""}
    }
}
