import copy
import numpy as np
import pytest

from geotrace.document import save_document,load_document
from geotrace.groups import create_group,replace_groups,remove_group,match_groups
from test_document import two_sheets


def test_group_transfer_compacts_every_reference_without_changing_objects(tmp_path):
    doc=two_sheets();doc.manual=doc.compose(doc.canvas(300))
    original=[(f['properties']['object_id'],f['geometry'].wkb) for f in doc.manual.features]
    first,second=doc.classes[0]['id'],doc.classes[2]['id']
    doc.layouts=[dict(name='Карта',class_ids=[first,second,2],legend_names={str(first):'A',str(second):'B'})]
    doc.settings.update(draw_class=second,hidden_classes=[second])
    target=replace_groups(doc,[second],first)
    assert target==1 and [c['id'] for c in doc.classes]==[1,2,3]
    assert [(f['properties']['object_id'],f['geometry'].wkb) for f in doc.manual.features]==original
    assert doc.sheets[1].groups[1]==target
    assert doc.layouts[0]['class_ids']==[1,2] and doc.settings['draw_class']==1
    assert all(f['properties']['class_id']<=3 for f in doc.manual.features)
    assert set(np.unique(doc.manual.labels))<=set(range(4))
    path=tmp_path/'groups.geotrace';save_document(path,doc,doc.manual.raster,doc.manual)
    loaded=load_document(path)
    assert len(loaded.classes)==3 and loaded.sheets[1].groups==doc.sheets[1].groups
    assert loaded.layouts==doc.layouts


def test_used_group_requires_destination_and_new_empty_group_can_be_deleted():
    doc=two_sheets();old=copy.deepcopy(doc.classes)
    with pytest.raises(ValueError,match='переноса'):remove_group(doc,1)
    assert doc.classes==old
    cid=create_group(doc,'Своя группа','#aabbcc');assert doc.classes[-1]['name']=='Своя группа'
    remove_group(doc,cid);assert doc.classes==old


def test_matching_removes_redundant_classes_and_keeps_native_colours():
    doc=two_sheets();doc.manual=doc.compose(doc.canvas(300))
    source=copy.deepcopy([s.result.colors for s in doc.sheets])
    target=match_groups(doc,[{'sheet':'a','group':1},{'sheet':'b','group':1}],0,'Общее подразделение')
    assert len(doc.classes)==3 and doc.classes[target-1]['name']=='Общее подразделение'
    assert doc.sheets[0].groups[1]==doc.sheets[1].groups[1]==target
    assert doc.settings['matched_groups']==['a:1','b:1']
    assert [s.result.colors for s in doc.sheets]==source
    # A third source can be assigned to the existing class without reselecting a
    # completed sample, which is now hidden in the matching UI.
    target=match_groups(doc,[{'sheet':'a','group':2}],target)
    assert len(doc.classes)==2 and doc.sheets[0].groups[2]==target


def test_open_uses_saved_unreviewed_output_without_recomposition(tmp_path):
    from geotrace.workspace_app import build_workspace,open_workspace
    from geotrace.editing import _result_from_features
    (tmp_path/'initial').mkdir();(tmp_path/'restored').mkdir()
    doc,raster,result=build_workspace(two_sheets(),tmp_path/'initial')
    feature=copy.deepcopy(result.features[0])
    feature['properties'].update(review='new',note='')
    # A saved automatic object need not equal a future recomposition of sheets.
    # Opening a file is a read, not a request to replace it with fresh output.
    doc.manual=_result_from_features(raster,[feature],result.colors,result.fields)
    path=tmp_path/'saved.geotrace';save_document(path,doc,raster,doc.manual)
    restored,_,output=open_workspace(path,tmp_path/'restored')
    assert len(output.features)==1
    assert output.features[0]['geometry'].equals_exact(feature['geometry'],0)
    assert output.features[0]['properties']==feature['properties']
    assert restored.manual is output and restored.dissolve_classes is False


def test_empty_output_group_detaches_native_colour_without_transfer(tmp_path):
    from geotrace.editing import _result_from_features
    doc=two_sheets();result=doc.compose(doc.canvas(300));empty=doc.sheets[0].groups[2]
    features=[f for f in result.features if f['properties']['class_id']!=empty]
    doc.manual=_result_from_features(result.raster,features,result.colors,result.fields)
    before=[(f['geometry'].wkb,f['properties']['object_id']) for f in features]
    doc.settings['matched_groups']=['a:2']
    remove_group(doc,empty)
    assert 'a:2' not in doc.settings['matched_groups']
    assert doc.sheets[0].groups[2]==0
    assert [(f['geometry'].wkb,f['properties']['object_id']) for f in doc.manual.features]==before
    path=tmp_path/'empty.geotrace';save_document(path,doc,doc.manual.raster,doc.manual)
    restored=load_document(path)
    assert restored.sheets[0].groups[2]==0
    restored.compose(restored.canvas(300))
    target=match_groups(restored,[dict(sheet='a',group=2)],1)
    assert target==1 and restored.sheets[0].groups[2]==1
