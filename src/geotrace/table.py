"""Virtualized polygon table; filters use stable IDs, never view row numbers."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from .attributes import BASE_FIELDS, parse_value

BASE_COLUMNS = [
    dict(key='_check',label='Выбор',width=64), dict(key='object_id',label='№',width=70),
    dict(key='class_id',label='Группа',width=85), dict(key='review',label='Проверка',width=150),
    dict(key='note',label='Примечание',width=220), dict(key='origin',label='Источник',width=160)]
REVIEW = dict(unreviewed='Не проверен',accepted='Принят',edited='Изменён вручную')
ORIGIN = dict(joint_quality='Авто: цвет + границы',color_clusters='Авто: цвет',color_scan='Авто: скан',prompt_color='Подсказки: цвет',prompt_mobilesam='Подсказки: MobileSAM',
              manual_draw='Ручной контур',manual_split='Разделение',manual_merge='Объединение',
              manual_boundary='Общая граница',manual_vertex='Перемещение вершины')


def display(value):
    if value is None: return '—'
    if type(value) is bool: return 'Да' if value else 'Нет'
    return str(value)


def empty(value):
    return value is None or (isinstance(value,str) and not value.strip())


def editable_fields(result):
    base=[dict(key=key,label=label,type='text',required=False,default=None,choices=[])
          for key,label in [('note','Примечание')]]
    return base + (result.fields if result else [])


def plan_bulk(result, object_ids, key, value, only_empty=False):
    """Return old/new properties without mutating any record."""
    field=next((f for f in editable_fields(result) if f['key']==key),None)
    if field is None: raise ValueError('Это поле нельзя заполнять массово.')
    value=parse_value(field,value)
    if key in BASE_FIELDS and value is None: value=''
    by_id={f['properties']['object_id']:f for f in result.features}
    if not object_ids: raise ValueError('Отметьте полигоны в таблице.')
    if not set(object_ids)<=by_id.keys(): raise ValueError('Набор объектов изменился. Повторите выбор.')
    changes={}
    for oid in sorted(set(object_ids)):
        props=by_id[oid]['properties']
        if only_empty and not empty(props.get(key)): continue
        if props.get(key)==value: continue
        new=props.copy();new[key]=value;new['review']='edited'
        changes[oid]=(props.copy(),new)
    return field,value,changes


class PolygonTable(QAbstractTableModel):
    statsChanged=Signal()
    TEXT=Qt.UserRole+1
    ID=Qt.UserRole+2
    CHECKED=Qt.UserRole+3
    MISSING=Qt.UserRole+4

    def __init__(self,parent=None):
        super().__init__(parent)
        self.result=None;self.rows=[];self.columns=list(BASE_COLUMNS);self.selected_ids=set()
        self.query='';self.review='all';self.missing_only=False;self.class_id=0
        self.sort_key='object_id';self.descending=False
        self.search_text=[];self.missing=[]

    def roleNames(self):
        return {self.TEXT:b'cellText',self.ID:b'objectId',self.CHECKED:b'rowChecked',self.MISSING:b'missingRequired'}

    def rowCount(self,parent=QModelIndex()): return 0 if parent.isValid() else len(self.rows)
    def columnCount(self,parent=QModelIndex()): return 0 if parent.isValid() else len(self.columns)

    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid() or not self.result: return None
        source=self.rows[index.row()];props=self.result.features[source]['properties']
        key=self.columns[index.column()]['key']
        if role in (self.TEXT,Qt.DisplayRole):
            if key=='_check': return ''
            value=props.get(key)
            if key=='review': return REVIEW.get(value,display(value))
            if key=='origin': return ORIGIN.get(value,display(value))
            return display(value)
        if role==self.ID: return props['object_id']
        if role==self.CHECKED: return props['object_id'] in self.selected_ids
        if role==self.MISSING: return self.missing[source]
        return None

    def set_result(self,result):
        self.beginResetModel()
        self.result=result
        self.columns=[*BASE_COLUMNS,*[dict(key=f['key'],label=f['label']+(' *' if f['required'] else ''),width=160)
                                      for f in result.fields]] if result else list(BASE_COLUMNS)
        self.refresh(reset=False)
        self.endResetModel();self.statsChanged.emit()

    def refresh(self,reset=True):
        self.search_text=[];self.missing=[]
        if self.result:
            required=[f['key'] for f in self.result.fields if f['required']]
            for feature in self.result.features:
                props=feature['properties']
                self.search_text.append(' '.join([*(display(v) for v in props.values()),REVIEW.get(props.get('review'),''),ORIGIN.get(props.get('origin'),'')]).casefold())
                self.missing.append(any(empty(props.get(k)) for k in required))
        if self.sort_key not in {c['key'] for c in self.columns}: self.sort_key='object_id'
        self.rebuild(clear_selection=True,reset=reset)

    def filter(self,query,review,missing_only,class_id=0):
        self.query=query.strip().casefold();self.review=review;self.missing_only=missing_only;self.class_id=int(class_id)
        self.rebuild(clear_selection=True)

    def sort_by(self,key):
        if key=='_check' or key not in {c['key'] for c in self.columns}: return
        self.descending=not self.descending if self.sort_key==key else False
        self.sort_key=key;self.rebuild(clear_selection=False)

    def matching_indices(self,query='',review='all',missing_only=False,class_id=0):
        query=query.strip().casefold()
        if self.result is None:
            return []
        return [i for i,f in enumerate(self.result.features)
                if (not query or query in self.search_text[i])
                and (review=='all' or f['properties']['review']==review)
                and (not missing_only or self.missing[i])
                and (not class_id or f['properties']['class_id']==class_id)]

    def rebuild(self,clear_selection,reset=True):
        if reset: self.beginResetModel()
        if clear_selection: self.selected_ids.clear()
        self.rows=[]
        if self.result:
            self.rows=self.matching_indices(self.query,self.review,self.missing_only,self.class_id)
            def order(i):
                value=self.result.features[i]['properties'].get(self.sort_key)
                # Keep missing values last in both directions, numbers sort numerically.
                if isinstance(value,(int,float)): return (0,value)
                return (1,str(value).casefold())
            present=[i for i in self.rows if not empty(self.result.features[i]['properties'].get(self.sort_key))]
            absent=[i for i in self.rows if empty(self.result.features[i]['properties'].get(self.sort_key))]
            self.rows=sorted(present,key=order,reverse=self.descending)+absent
        if reset:
            self.endResetModel();self.statsChanged.emit()

    def choose(self,oid):
        visible_ids={self.result.features[i]['properties']['object_id'] for i in self.rows} if self.result else set()
        if oid not in visible_ids: return
        if oid in self.selected_ids:self.selected_ids.remove(oid)
        else:self.selected_ids.add(oid)
        self.emit_selection()

    def choose_filtered(self,checked):
        self.selected_ids={self.result.features[i]['properties']['object_id'] for i in self.rows} if checked and self.result else set()
        self.emit_selection()

    def emit_selection(self):
        if self.rows:
            self.dataChanged.emit(self.index(0,0),self.index(len(self.rows)-1,len(self.columns)-1),[self.CHECKED])
        self.statsChanged.emit()
