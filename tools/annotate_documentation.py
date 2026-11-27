"""Annotate real editor captures without changing the depicted interface."""
import argparse,math,textwrap
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

SPECS={
 'connection':((240,80,1378,745),[((317,110),'Два клика','Отметьте начало и конец участка на одной границе.'),((500,110),'Сохраняемый контур','Выберите контур соседа или выбранного полигона.'),((712,430),'Общая линия','Проверьте участок и нажмите «Применить правку».')]),
 'connection-drag':((230,180,1130,740),[((650,320),'Исходная граница','Захватывается участок линии, отдельные вершины выбирать не нужно.'),((772,425),'Новая общая линия','Соседний полигон подстроится по проведённому контуру.'),((690,450),'Изменяемая область','Подсветка показывает будущую правку до отпускания кнопки.')]),
 'workspace':(None,[((76,67),'Листы и участок', 'Загрузите TIFF и проверьте состав проекта.'),((270,67),'Оцифровка','Начните с подготовки цветов активного листа.'),((1325,24),'Проект','Группы, проверка, макет и экспорт собраны по задачам.')]),
 'groups':((290,475,1190,850),[((690,668),'Выберите A и B','Нажмите «На карте» и укажите соответствующие заливки.'),((620,775),'Общий класс','Назначьте существующую группу или введите новое имя.'),((1100,775),'Сопоставьте','После применения обработанные образцы исчезнут из выбора.')]),
 'style':((420,200,1020,700),[((620,370),'Цвет группы','Заливка и граница меняются независимо от распознавания.'),((690,455),'Толщина','Настройте обводку, прозрачность и подписи.'),((580,653),'Применение','Оформление сохраняется только для открытой группы.')]),
 'smoothing':((480,90,1435,570),[((1250,247),'Режим и сила','Выберите область, способ сглаживания и силу обработки.'),((1250,335),'Предпросмотр','Сравните с прежним контуром галочкой ниже.'),((1250,485),'Применение','Сохраните результат; обычная отмена вернёт прежние границы.')]),
 'updates':((410,210,1030,685),[((680,331),'Результат проверки','Здесь появится новая версия или сообщение об актуальности.'),((495,545),'Проверить','Повторите запрос при необходимости. Работа проекта не прерывается.'),((610,586),'Страница выпусков','Можно самостоятельно открыть список установщиков и документов.')]),
 'layout':(None,[((170,67),'Разделы макета','Лист, слои, легенда и подписи доступны отдельными вкладками.'),((950,28),'Предпросмотр','Проверьте вид и положение элементов перед выпуском.'),((1110,28),'Экспорт','Сохраните PDF, PNG или SVG; атлас объединяет участки в PDF.')])}


def annotate(source,destination,name):
 image=Image.open(source).convert('RGB');crop,notes=SPECS[name]
 if crop:image=image.crop(crop)
 else:crop=(0,0,*image.size)
 width=1200;scale=width/image.width;image=image.resize((width,round(image.height*scale)),Image.Resampling.LANCZOS)
 fonts=Path('/System/Library/Fonts/Supplemental/Arial.ttf')
 if not fonts.is_file():fonts=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
 font=ImageFont.truetype(str(fonts),23);bold=ImageFont.truetype(str(fonts),24)
 legend_h=180;canvas=Image.new('RGB',(width,image.height+legend_h),'white');canvas.paste(image,(0,0));draw=ImageDraw.Draw(canvas)
 blue='#3568c0';ink='#263445'
 for i,(point,title,body) in enumerate(notes,1):
  px,py=(point[0]-crop[0])*scale,(point[1]-crop[1])*scale
  cx=max(22,min(width-22,px-48));cy=py+50 if name=="connection" and py<70 else max(22,py-38)
  draw.line((cx,cy,px,py),fill=blue,width=3)
  angle=math.atan2(py-cy,px-cx)
  draw.polygon([(px,py),(px-11*math.cos(angle-.5),py-11*math.sin(angle-.5)),(px-11*math.cos(angle+.5),py-11*math.sin(angle+.5))],fill=blue)
  draw.ellipse((cx-17,cy-17,cx+17,cy+17),fill=blue,outline='white',width=2);draw.text((cx,cy),str(i),font=bold,fill='white',anchor='mm')
  x=(i-1)*400+18;y=image.height+20
  draw.ellipse((x,y,x+30,y+30),fill=blue);draw.text((x+15,y+15),str(i),font=font,fill='white',anchor='mm');draw.text((x+41,y+2),title,font=bold,fill=ink)
  lines=textwrap.wrap(body,width=28)
  for row,line in enumerate(lines):draw.text((x+2,y+45+row*27),line,font=font,fill=ink)
 destination.parent.mkdir(parents=True,exist_ok=True);canvas.save(destination,optimize=True)


def main():
 p=argparse.ArgumentParser();p.add_argument('--captures',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 for name in SPECS:annotate(args.captures/(name+'.png'),args.output/(name+'.png'),name)

if __name__=='__main__':main()
