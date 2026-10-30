"""Validate schema examples and targeted negative cases. Requires jsonschema>=4.18."""
import copy
import json
from pathlib import Path
from decimal import Decimal
from jsonschema import Draft202012Validator, FormatChecker

BASE=Path(__file__).resolve().parent
load=lambda name:json.loads((BASE/name).read_text(encoding='utf-8'))
schema=load('loan_complaint.schema.json')
Draft202012Validator.check_schema(schema)
validator=Draft202012Validator(schema,format_checker=FormatChecker())
sample=load('example_from_attachment.json')
config=load('field_config.json')
results=[]
def test(name,mutate=None,valid=True,instance=None):
    x=copy.deepcopy(sample if instance is None else instance)
    if mutate: mutate(x)
    errors=list(validator.iter_errors(x))
    passed=(not errors)==valid
    results.append({'name':name,'expectedValid':valid,'passed':passed,'errorCount':len(errors)})
    assert passed, (name,[e.message for e in errors])
def blank(node):
    if '$ref' in node:return blank(schema['$defs'][node['$ref'].split('/')[-1]])
    if 'properties' in node:return {k:blank(v) for k,v in node['properties'].items()}
    return None
test('attachment_reference_passes')
test('all_unknown_leaves_pass',instance=blank(schema))
company=blank(schema)
company.update(load('organization_fragment.json'))
test('normalized_user_organization_passes',instance=company)
test('missing_required_key_rejected',lambda x:x.pop('第三人'),False)
test('extra_root_key_rejected',lambda x:x.update(partyId='P1'),False)
test('category_data_mismatch_rejected',lambda x:x['原告'][0].update(category='法人、非法人组织'),False)
test('empty_party_array_rejected',lambda x:x.update(第三人=[]),False)
test('string_null_rejected_for_enum',lambda x:x['原告'][0]['data'].update(sex='null'),False)
test('multi_sex_rejected',lambda x:x['原告'][0]['data'].update(sex=['男','女']),False)
test('empty_multi_enum_rejected',lambda x:x['事实与理由']['3.借款金额'].update(提供方式=[]),False)
test('duplicate_multi_enum_rejected',lambda x:x['事实与理由']['3.借款金额'].update(提供方式=['转账','转账']),False)
test('unknown_multi_enum_rejected',lambda x:x['事实与理由']['3.借款金额'].update(提供方式=['银行卡']),False)
test('scalar_multi_enum_rejected',lambda x:x['事实与理由']['3.借款金额'].update(提供方式='转账'),False)
test('null_array_item_rejected',lambda x:x['事实与理由']['3.借款金额'].update(提供方式=[None]),False)
test('no_guarantee_with_details_rejected',lambda x:x['事实与理由']['14.是否签订保证合同'].update(签订时间='2026-01-01'),False)
test('other_detail_without_other_rejected',lambda x:x['事实与理由']['3.借款金额'].update(其他说明='其他交付'),False)
test('other_selected_without_detail_allowed',lambda x:x['事实与理由']['3.借款金额'].update(提供方式=['其他']))
test('yes_with_unknown_details_allowed',lambda x:x['事实与理由']['14.是否签订保证合同'].update(是否签订='是'))
test('impossible_calendar_date_rejected',lambda x:x['诉讼请求']['1.本金'].update(截至日期='2026-02-30'),False)
test('money_float_rejected',lambda x:x['诉讼请求']['1.本金'].update(尚欠本金=50000.0),False)
test('money_without_two_decimals_rejected',lambda x:x['诉讼请求']['1.本金'].update(尚欠本金='50000'),False)
test('ownership_other_mismatch_rejected',lambda x:x['原告'][0]['data'].update(ownership='民营'),False,company)
test('state_suboption_requires_state_ownership',lambda x:x['原告'][0]['data'].update(stateOwnershipType=['控股']),False,company)

# Configuration integrity, distinct from extraction quality.
assert all(f['description'] and f['module'] for f in config['fields'])
assert len({(f['outputPath'],f['categoryCondition']) for f in config['fields']})==len(config['fields'])
for f in config['fields']:
    node=schema
    for p in f['schemaPointer'].split('/')[1:]:
        p=p.replace('~1','/').replace('~0','~')
        node=node[int(p)] if isinstance(node,list) else node[p]
    assert isinstance(node,dict)
assert Decimal(sample['诉讼请求']['1.本金']['尚欠本金'])+Decimal(sample['诉讼请求']['2.利息']['尚欠利息'])==Decimal(sample['诉讼请求']['8.标的总额']['金额'])
report={'schemaDraft':'2020-12','schemaSelfValidation':'passed','cases':results,'passed':len(results),'total':len(results),'configurationIntegrity':'passed','samplePrincipalPlusInterestEqualsStatedTotal':True,'scope':'结构、条件约束和样例合计验证；不代表模型抽取准确率，不验证利息计算或法律适用。'}
(BASE/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':len(results),'total':len(results),'configurationIntegrity':'passed'},ensure_ascii=False))
