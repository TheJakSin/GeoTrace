"""Project styles never feed back into scan colour recognition."""
import re

DEFAULT_POLYGON = dict(fill='#80b5d2', outline='#334b5f', width=1., opacity=.5, label='')
DEFAULT_LINE = dict(color='#b53239', width=2., dash='solid', label='')


def validate_style(style, line=False):
    if not isinstance(style, dict):
        raise ValueError('Некорректное оформление.')
    base = dict(DEFAULT_LINE if line else DEFAULT_POLYGON)
    for key, value in style.items():
        if key not in base:
            raise ValueError('Неизвестный параметр оформления.')
        if key in ('fill', 'outline', 'color'):
            if not isinstance(value, str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', value):
                raise ValueError('Цвет задаётся как #RRGGBB.')
        elif key == 'width':
            if type(value) not in (int, float) or not .1 <= value <= 20:
                raise ValueError('Толщина должна быть от 0,1 до 20.')
        elif key == 'opacity':
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError('Прозрачность должна быть от 0 до 1.')
        elif key == 'dash' and value not in ('solid', 'dash', 'dot'):
            raise ValueError('Неизвестный тип штриха.')
        elif key == 'label' and (not isinstance(value, str) or len(value) > 80):
            raise ValueError('Некорректное поле подписи.')
        base[key] = value
    return base
