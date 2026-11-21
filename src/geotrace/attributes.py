"""Per-project attribute schemas and lossless, explicit value conversion."""
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

BASE_FIELDS = {'object_id','class_id','note','review','origin'}
SUGGESTED_FIELDS = [
    dict(key='unit_index',label='Геологический индекс',type='text'),
    dict(key='unit_name',label='Название подразделения',type='text'),
    dict(key='age',label='Геологический возраст',type='text'),
    dict(key='lithology',label='Литология',type='choice',
         choices=['Песчаник','Известняк','Глина','Доломит']),
    dict(key='thickness_m',label='Мощность, м',type='number'),
    dict(key='source_map',label='Источник данных',type='text'),
]
TYPES = ('text','integer','number','boolean','date','choice')


def parse_value(field, value, enforce_required=False):
    label = field['label']
    if value is None or (isinstance(value,str) and not value.strip()):
        if enforce_required and field.get('required'):
            raise ValueError(f'Заполните обязательное поле «{label}».')
        return None
    kind = field['type']
    try:
        if kind in ('text','choice','date'):
            if not isinstance(value,str):
                raise ValueError()
            value = value.strip()
            if kind == 'choice' and value not in field['choices']:
                raise ValueError()
            if kind == 'date':
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError()
            return value
        if kind == 'boolean':
            if isinstance(value,str): value=value.strip().lower()
            if type(value) is bool:
                return value
            if value in ('true','да','1',1):
                return True
            if value in ('false','нет','0',0):
                return False
            raise ValueError()
        if type(value) is bool or not isinstance(value,(int,float,str)):
            raise ValueError()
        if kind == 'integer':
            number = Decimal(str(value).replace(',','.'))
            if not number.is_finite() or number != number.to_integral_value() or abs(number)>9007199254740991:
                raise ValueError()
            return int(number)
        number = float(value.replace(',','.') if isinstance(value,str) else value)
        if not math.isfinite(number):
            raise ValueError()
        return number
    except (ValueError, TypeError, OverflowError, InvalidOperation):
        names = dict(text='текст',integer='целое число',number='число',boolean='Да или Нет',
                     date='дату ГГГГ-ММ-ДД',choice='значение из списка')
        raise ValueError(f'Поле «{label}»: укажите {names[kind]}.') from None


def validate_schema(fields):
    if not isinstance(fields,list) or len(fields)>64:
        raise ValueError('В проекте допускается до 64 пользовательских полей.')
    normalized=[];keys=set()
    for field in fields:
        if not isinstance(field,dict) or not isinstance(field.get('key'),str) or not isinstance(field.get('label'),str):
            raise ValueError('Некорректное описание поля.')
        key=field.get('key','');label=field.get('label','').strip();kind=field.get('type')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}',key) or key in BASE_FIELDS | {'fid','geom','geometry'}:
            raise ValueError('Код поля: латинская буква, затем латинские строчные буквы, цифры или _. Служебные имена заняты.')
        if key in keys:
            raise ValueError('Код поля уже существует.')
        if not label or len(label)>80 or kind not in TYPES:
            raise ValueError('Укажите название поля до 80 символов и поддерживаемый тип.')
        choices=field.get('choices',[])
        if kind == 'choice':
            if (not isinstance(choices,list) or not 1<=len(choices)<=100
                    or any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in choices)):
                raise ValueError('Задайте от 1 до 100 непустых вариантов списка.')
            choices=[v.strip() for v in choices]
            if len(set(choices))!=len(choices):
                raise ValueError('Варианты списка не должны повторяться.')
        else:
            choices=[]
        item=dict(key=key,label=label,type=kind,required=bool(field.get('required',False)),choices=choices)
        item['default']=parse_value(item,field.get('default'))
        normalized.append(item);keys.add(key)
    return normalized


def defaults(fields):
    return {f['key']:f['default'] for f in fields}


def values_for(fields, values, enforce_required=False):
    return {f['key']:parse_value(f,values.get(f['key'],f['default']),enforce_required) for f in fields}


def change_schema(result, fields):
    """Stage all conversions before replacing anything; deletion can be undone as a result snapshot."""
    from .core import Result
    fields=validate_schema(fields)
    features=[]
    for feature in result.features:
        props={k:v for k,v in feature['properties'].items() if k in BASE_FIELDS}
        props.update(values_for(fields,feature['properties']))
        features.append(dict(geometry=feature['geometry'],properties=props))
    return Result(result.raster,result.labels,features,[list(c) for c in result.colors],fields)
